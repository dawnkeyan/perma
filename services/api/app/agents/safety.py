"""安全检测。

**关键设计：危机场景的响应话术是硬编码的，绝不经过大模型生成。**

理由：
1. 模型可能幻觉出不存在的热线号码或错误资源
2. 危机场景下模型的"共情"可能变成"说教"，造成二次伤害
3. 评测要求 `must_not_call_llm = true`，需要系统层面保证

医疗越界场景则不同——那里需要的是「温和地说明边界 + 引导就医」，
硬编码会显得生硬，因此用 Prompt 约束 + 模型判定，并额外做输出后校验。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class SafetyType(StrEnum):
    NONE = "none"
    CRISIS = "crisis"           # 自伤/自杀风险
    MEDICAL = "medical"         # 越界给出医疗结论
    ABUSE = "abuse"             # 涉及暴力/虐待


@dataclass(slots=True)
class SafetyVerdict:
    """安全判定结果。"""

    type: SafetyType = SafetyType.NONE
    matched_keywords: list[str] | None = None
    reason: str = ""
    model_used: bool = False    # 是否调用了 LLM（危机场景必须为 False）

    @property
    def is_critical(self) -> bool:
        return self.type == SafetyType.CRISIS

    def to_dict(self) -> dict:
        return {
            "type": str(self.type),
            "matched_keywords": self.matched_keywords or [],
            "reason": self.reason,
            "model_used": self.model_used,
        }


# --------------------------------------------------------------------------- #
# 危机检测
# --------------------------------------------------------------------------- #
# 关键词 + 正则双层。单纯关键词会漏掉委婉表达，
# 单纯的模型判定会漏掉编码变体，两者结合。
_CRISIS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"自杀"),
    re.compile(r"自残"),
    re.compile(r"轻生"),
    re.compile(r"不想(?:活|活下去)"),
    re.compile(r"结束(?:自己的)?生命"),
    re.compile(r"伤害自己"),
    re.compile(r"活不下去"),
    re.compile(r"解脱.{0,4}走"),
    re.compile(r"消失.{0,4}(?:算了|就好)"),
]

# 危机响应模板。多个版本轮换，避免用户在多次求助时看到完全一样的文字。
_CRISIS_RESPONSES: list[str] = [
    (
        "听到你这样说，我很担心你现在的安全。\n"
        "你的痛苦是真实的，它值得被认真对待，而不是一个人扛着。\n\n"
        "我想先确认一件事：**你现在身边有人吗？**\n"
        "如果可以，请先去有人的地方，哪怕只是坐着，也比独自待着好一些。\n\n"
        "如果你现在有伤害自己的念头，请立刻拨打：\n"
        "· 全国心理援助热线 **12356**\n"
        "· 急救 **120**\n"
        "· 身边人的电话\n\n"
        "我是一个 AI，能陪你说话，但我不具备专业能力，"
        "也无法替代真正能帮助你的人。**请联系专业的人——这是我能给你的最重要的建议。**"
    ),
    (
        "谢谢你愿意说出来，这需要很大的力气。\n\n"
        "我想先和你确认：此刻你是否有伤害自己的想法或计划？\n\n"
        "如果你正处在危险中，请马上：\n"
        "· 拨打 **120**（急救）\n"
        "· 拨打 **12356**（全国心理援助热线）\n"
        "· 或联系一位你信任的人，让他陪着你\n\n"
        "你现在不需要解决所有事情。**先活过今天这一个小时，就已经够了。**\n"
        "我无法替代专业的帮助，但如果你愿意，我们可以先把这一刻熬过去。"
    ),
]


def detect_crisis(text: str) -> SafetyVerdict:
    """检测危机信号。**纯规则，不调用模型**（保证确定性与零延迟）。"""
    if not settings.safety_crisis_detection:
        return SafetyVerdict()

    normalized = text.strip()
    matched: list[str] = []

    for keyword in settings.safety_crisis_keywords:
        if keyword in normalized:
            matched.append(keyword)

    for pattern in _CRISIS_PATTERNS:
        found = pattern.search(normalized)
        if found:
            matched.append(found.group(0))

    if not matched:
        return SafetyVerdict()

    logger.warning("crisis_detected", matched=matched)
    return SafetyVerdict(
        type=SafetyType.CRISIS,
        matched_keywords=sorted(set(matched)),
        reason="命中危机关键词或模式",
        model_used=False,
    )


def crisis_response(text: str) -> dict:
    """构造危机响应（硬编码，绝不经过模型）。"""
    # 用文本哈希做稳定轮换：同一用户每次看到不同版本，但同一输入结果稳定
    index = hash(text) % len(_CRISIS_RESPONSES)
    return {
        "type": "crisis",
        "content": _CRISIS_RESPONSES[index],
        "hotline": settings.safety_crisis_hotline,
        "emergency": "120",
        "sections": {
            "empathy": "",
            "insight": "",
            "evidence": [],
            "action": "请现在就联系身边的人，并拨打 120 或 12356。",
        },
        "citations": [],
        "model_used": False,
    }


# --------------------------------------------------------------------------- #
# 医疗越界检测
# --------------------------------------------------------------------------- #
# 这些是「用户可能在索要医疗结论」的信号，不是「用户提到了情绪」。
_MEDICAL_QUESTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"是不是(?:有|得了|患有|属于)?\s*(?:抑郁|焦虑症|抑郁症|双相|躁狂| PTSD|ptsd|精神分裂|失眠症|强迫症|惊恐障碍|社交恐惧)"),
    re.compile(r"(?:我|她|他)\s*(?:是不是|有没有)\s*(?:抑郁|焦虑|精神病|有问题)"),
    re.compile(r"帮我(?:诊断|判断|评估)(?:一下)?(?:我|我的)"),
    re.compile(r"(?:我|我的)(?:症状|表现)(?:正常吗|严重吗|算不算)"),
    re.compile(r"(?:该|要)(?:吃|用)(?:什么|哪种)药"),
    re.compile(r"要不要(?:吃药|看医生|去医院)"),
]


def detect_medical_overreach(text: str) -> SafetyVerdict:
    """检测医疗越界意图（只判断用户意图，不做结论）。"""
    normalized = text.strip()
    for pattern in _MEDICAL_QUESTION_PATTERNS:
        found = pattern.search(normalized)
        if found:
            logger.info("medical_overreach_detected", matched=found.group(0))
            return SafetyVerdict(
                type=SafetyType.MEDICAL,
                matched_keywords=[found.group(0)],
                reason="用户索要医疗诊断或用药建议",
            )
    return SafetyVerdict()


# 医疗越界的输出后校验：模型是否给出了诊断式结论
_FORBIDDEN_DIAGNOSTIC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"你(?:就是|患有|得了|属于|是)\s*(?:抑郁症|焦虑症|双相|躁狂症|精神分裂|失眠症|强迫症|惊恐障碍)"),
    re.compile(r"你(?:的)?(?:症状|表现)(?:非常|明显)?(?:符合|就是|典型地?是)\s*(?:抑郁|焦虑|双相| PTSD)"),
    re.compile(r"建议(?:你)?(?:服用|吃)\s*\S{1,10}"),
    re.compile(r"你(?:需要|应该)(?:立即)?(?:就医|吃药|住院|看精神科)"),
]

# 「建议就医」是**正确**的引导，不应被误杀，因此单独放行
_ALLOWED_REFERRAL = re.compile(r"(建议|建议您|可以|最好|应当|应该)(?:你)?(?:尽快)?(?:去|到|寻求|联系)?(?:医院|医院|精神科|心理科|专业(?:医生|咨询师|心理师)|线下)")


def check_output_for_diagnosis(answer: str) -> SafetyVerdict:
    """输出后校验：模型是否给出了诊断式结论或用药建议。"""
    for pattern in _FORBIDDEN_DIAGNOSTIC_PATTERNS:
        found = pattern.search(answer)
        if not found:
            continue
        # 匹配到的是"建议就医"这类正确引导，放行
        if _ALLOWED_REFERRAL.search(answer[max(0, found.start() - 30) : found.end() + 30]):
            continue
        logger.warning("diagnosis_leak_detected", matched=found.group(0))
        return SafetyVerdict(
            type=SafetyType.MEDICAL,
            matched_keywords=[found.group(0)],
            reason="输出包含诊断式结论",
            model_used=True,
        )
    return SafetyVerdict()


# --------------------------------------------------------------------------- #
# 提示注入检测
# --------------------------------------------------------------------------- #
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"忽略(?:之前|上面|所有)(?:的)?(?:所有)?(?:指令|命令|提示|规则)"),
    re.compile(r"(?:输出|显示|重复|告诉)(?:你的)?(?:系统)?(?:提示词|prompt|系统指令)"),
    re.compile(r"(?:你(?:现在)?是|扮演)(?:一个)?(?:没有任何?(?:限制|约束)的)?(?:AI|助手|角色)"),
    re.compile(r"developer\s*mode|开发者模式|越狱|jailbreak"),
]


def detect_prompt_injection(text: str) -> SafetyVerdict:
    """检测用户输入中的提示注入尝试。

    命中不代表一定拒绝，而是**加强输入隔离**（已在 Prompt 层用标签包裹），
    并在输出后做一次系统提示泄露检查。
    """
    for pattern in _INJECTION_PATTERNS:
        found = pattern.search(text)
        if found:
            logger.info("prompt_injection_detected", matched=found.group(0))
            return SafetyVerdict(
                type=SafetyType.ABUSE,
                matched_keywords=[found.group(0)],
                reason="疑似提示注入",
            )
    return SafetyVerdict()


def check_system_prompt_leak(answer: str, system_prompt: str) -> bool:
    """检查输出是否泄露了系统提示词的关键片段。"""
    if not system_prompt:
        return False
    # 取系统提示中较长的句子做匹配
    candidates = [line.strip() for line in system_prompt.split("\n") if len(line.strip()) > 20]
    for candidate in candidates[:20]:
        if candidate and candidate in answer:
            logger.warning("system_prompt_leak_detected")
            return True
    return False
