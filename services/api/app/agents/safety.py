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

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class SafetyType(StrEnum):
    NONE = "none"
    CRISIS = "crisis"  # 自伤/自杀风险
    MEDICAL = "medical"  # 越界给出医疗结论
    ABUSE = "abuse"  # 涉及暴力/虐待


@dataclass(slots=True)
class SafetyVerdict:
    """安全判定结果。"""

    type: SafetyType = SafetyType.NONE
    matched_keywords: list[str] | None = None
    reason: str = ""
    model_used: bool = False  # 是否调用了 LLM（危机场景必须为 False）

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
    # —— 以下为「没有直接出现自伤词，但语义上已经是危机」的表达 ——
    # 这类是真实漏检的高发区：用户不会说「自杀」，但会描述「不想存在」的状态。
    re.compile(r"活着(?:真的|就是)?(?:没有|没)(?:意义|意思|劲)"),
    re.compile(r"活着(?:好|太|真|很)?累"),
    re.compile(r"(?:撑|扛)(?:不|过)(?:下去|住)"),
    re.compile(r"受不了(?:了|下去)"),
    re.compile(r"没(?:有)?(?:活下去|继续活)的?(?:意义|意思|动力)"),
    re.compile(r"想(?:消失|不存在|人间蒸发)"),
    # —— 「了结 / 解脱」类措辞 ——
    # 用户不直说自杀，但「了结自己」「解脱的方式是消失」说的是同一件事。
    # 这类表达是危机召回的高发漏检区。
    re.compile(r"了结(?:自己|自己的一切|一切|这条命|这条生命)"),
    re.compile(r"结束(?:自己的)?(?:生命|这一切|一切|这条命)"),
    re.compile(r"解脱(?:的)?(?:方式|方法)"),
    re.compile(r"解脱.{0,8}(?:消失|离开|走|走掉)"),
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
    # 用文本哈希做稳定轮换：同一用户每次看到不同版本，但同一输入结果稳定。
    #
    # 必须用 hashlib 而不是内置 hash()——内置 hash 对 str 是**加盐随机**的
    # （PYTHONHASHSEED 随机化），同一个输入在服务重启后会落到不同的版本上，
    # 轮换就变成了不可复现的随机行为，线上也难以排查。
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % len(_CRISIS_RESPONSES)
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
# 提示注入响应
# --------------------------------------------------------------------------- #

# 注入类输入的硬编码响应。
#
# 为什么不交给模型：
# 提示注入的本质是「诱导模型改写自己的规则」。如果把这类输入原样送进模型，
# 模型确实可能被「说服」（输出「已解锁」「好的，指令已覆盖」之类的内容）。
# 所以检测到注入后，**不让模型参与生成**，直接用确定性话术应答。
# 这与危机场景的处理思路一致：安全边界不交给概率模型。
_INJECTION_RESPONSES: tuple[str, ...] = (
    "我注意到你消息里有一部分像是在尝试改写我的运行规则。这类指令我不会执行，"
    "我的行为准则不会因为聊天内容而改变。\n"
    "我们仍然可以正常聊下去：如果你愿意，直接告诉我你此刻的感受；"
    "也可以从「心绪导航」开始，先弄清楚自己现在的状态。",
    "你这段消息里有一部分试图让我切换角色或跳过限制的内容，我没有照做——"
    "这类请求不在我可以配合的范围内。\n"
    "想继续的话，我们从你的实际处境开始：最近发生了什么，让你觉得最难的一刻是什么？",
)


def injection_response(text: str) -> dict:
    """构造提示注入响应（硬编码，绝不经过模型）。"""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % len(_INJECTION_RESPONSES)
    return {
        "type": "injection",
        "content": _INJECTION_RESPONSES[index],
        "sections": {
            "empathy": "",
            "insight": "",
            "evidence": [],
            "action": "直接告诉我你现在最想解决的一件具体的事，我们从那件事开始。",
        },
        "citations": [],
        "model_used": False,
    }


# --------------------------------------------------------------------------- #
# 医疗越界检测
# --------------------------------------------------------------------------- #
# 这些是「用户可能在索要医疗结论」的信号，不是「用户提到了情绪」。

# 可能被用户拿来「求诊断」的心理疾病名。
# 单独提到「焦虑」「抑郁」是正常情绪表达，不能因此命中；
# 只有出现这些名词**并且**是疑问句式时，才判定为医疗越界。
_PSYCH_DISORDERS: str = (
    r"抑郁(?:症)?|焦虑(?:症)?|双相(?:情感障碍)?|躁狂(?:症)?|精神分裂(?:症)?|"
    r"失眠症|强迫症|惊恐障碍|社交恐惧(?:症)?|自闭症|孤独症|"
    r"多动症|创伤后应激(?:障碍)?|PTSD|ptsd"
)

_MEDICAL_QUESTION_PATTERNS: list[re.Pattern[str]] = [
    # 「我是不是抑郁症」「这是不是焦虑症」
    re.compile(rf"是不是(?:有|得了|患有|属于)?\s*(?:{_PSYCH_DISORDERS})"),
    # 「这是焦虑症吗」「会不会是自闭症」「是否属于抑郁症」
    # —— 「这是XX吗」不带「是不是」，之前漏检
    re.compile(
        rf"(?:这是|这是不是|会不会|是否|算不算|能算是)\s*"
        rf"(?:有|得了|患有|属于)?\s*(?:{_PSYCH_DISORDERS})"
    ),
    re.compile(r"(?:我|她|他)\s*(?:是不是|有没有)\s*(?:抑郁|焦虑|精神病|有问题)"),
    re.compile(r"帮我(?:诊断|判断|评估)(?:一下)?(?:我|我的)?"),
    re.compile(r"(?:我|我的)(?:症状|表现)(?:正常吗|严重吗|算不算)"),
    re.compile(r"(?:该|要)(?:吃|用)(?:什么|哪种)药"),
    re.compile(r"要不要(?:吃药|看医生|去医院)"),
    # —— 下面两条补的是「高频但容易漏」的问法 ——
    # 「我需要吃药吗」不带「要不要」，之前漏检；
    # 「帮我诊断一下」不带「我」，之前也漏检。
    re.compile(r"(?:需要|该不该|要不要|得用|要不要得)\s*(?:吃|用|服)(?:什么|哪种)?药"),
    re.compile(r"(?:我)?(?:需不需要|要不要|该不该)\s*(?:吃|用)(?:药|药吗)"),
    # 「我需要吃点什么药」——「吃点什么药」中间夹了量词，之前漏检
    re.compile(r"(?:需要|该|要|想|该不该|要不要|得用)\s*(?:吃|用|服)\s*[^。！？\n]{0,6}?药"),
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


# 医疗越界的输出后校验：模型是否给出了诊断式结论或用药建议
#
# 分成三组，因为**处置方式不同**——这是本模块最容易写错的地方：
#
# 1. 诊断结论 —— 出现即为越界，**不会**因为句尾跟着「建议就医」而被放行。
#    「先下诊断再让人去医院」仍然是诊断，违背产品原则「不诊断」。
# 2. 用药建议 —— AI 不给任何具体用药指导，同样不放行。
# 3. 越界判断句 —— 例如「你应该吃药」「你需要住院」，
#    其中「你应该就医」是**正确**的引导，由 _ALLOWED_REFERRAL 放行。
#
# 早期版本把三类混在一个列表里，结果「建议你去精神科开药」会因为
# 命中「建议就医」而被放行——用药建议正是最该拦的那一类。

# 1) 诊断式结论：断言用户患上了某种心理疾病
_FORBIDDEN_DIAGNOSIS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"你(?:可能|也许|或许)?(?:这(?:就)?是|就是|是|患有|得了|属于)\s*"
        r"(?:抑郁症|焦虑症|双相|躁狂症|精神分裂|失眠症|强迫症|惊恐障碍|社交恐惧|PTSD)"
    ),
    re.compile(
        r"你(?:的)?(?:症状|表现|状态)(?:非常|明显|典型)?(?:符合|就是|典型的?是|像是)\s*"
        r"(?:抑郁|焦虑|双相|躁狂|精神分裂|PTSD|ptsd)"
    ),
    re.compile(
        r"(?:这(?:是|就是)|属于)\s*(?:典型的?\s*)?"
        r"(?:抑郁症|焦虑症|双相|躁狂症|精神分裂)"
    ),
]

# 2) 用药建议：任何具体到「吃什么药」或「去开药」的表达都要拦下
_FORBIDDEN_MEDICATION_PATTERNS: list[re.Pattern[str]] = [
    # 「建议你去精神科开药」——建议 + 跳转短语 + 开/吃/服用 + 药
    re.compile(r"建议(?:你|您)?[^。！？!?\n]{0,12}?(?:开|吃|服用|服)\s*[^。！？!?\n]{0,4}?药"),
    # 直接点名具体药物
    re.compile(
        r"(?:服用|吃|用)\s*(?:抗抑郁药|抗焦虑药|安眠药|镇静药|舍曲林|氟西汀|"
        r"文拉法辛|西酞普兰|米氮平|阿普唑仑|盐酸普萘洛尔)"
    ),
    # 把吃药当成结论下给用户
    re.compile(r"(?:你)?(?:需要|应该|必须)(?:立即|马上)?(?:吃药|服药|用抗抑郁药)"),
]

# 3) 越界的医疗判断句（部分可被 _ALLOWED_REFERRAL 放行）
_FORBIDDEN_OVERREACH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"你(?:需要|应该)(?:立即)?(?:就医|住院|看精神科|看心理科)"),
]

# 「建议就医」是**正确**的引导，不应被误杀，因此单独放行。
# 注意必须覆盖「就医/就诊」本身——只有「医院/精神科」这些名词形式的话，
# 「你应该就医」这种同样正确的说法会被当成越界。
_ALLOWED_REFERRAL = re.compile(
    r"(?:建议|建议您|可以|最好|应当|应该|需要)(?:你|您)?(?:尽快|立即|马上)?"
    r"(?:去|到|寻求|联系|找|前往)?\s*"
    r"(?:医院|精神科|心理科|心理门诊|专业(?:医生|咨询师|心理师|机构)|线下|就医|就诊|门诊)"
)

# 「遵医嘱」「在医生指导下」是转介而不是处方，不算 AI 给出的用药建议
_MEDICATION_REFERAL_EXEMPT = re.compile(r"(?:遵医嘱|按医嘱|在医生指导(?:下)?|听医生的)")


def check_output_for_diagnosis(answer: str) -> SafetyVerdict:
    """输出后校验：模型是否给出了诊断式结论或用药建议。

    判定顺序有意为之：**用药 → 诊断 → 越界判断句**。
    前两类是硬越界，无论后面是否跟着正确引导都要拦下；
    只有第三类（把就医当成结论的措辞）才允许被 _ALLOWED_REFERRAL 放行。
    """
    # 1) 用药建议
    for pattern in _FORBIDDEN_MEDICATION_PATTERNS:
        found = pattern.search(answer)
        if not found:
            continue
        window = answer[max(0, found.start() - 40) : found.end() + 40]
        if _MEDICATION_REFERAL_EXEMPT.search(window):
            continue
        return _forbidden_output(found, reason="输出包含用药建议")

    # 2) 诊断结论
    for pattern in _FORBIDDEN_DIAGNOSIS_PATTERNS:
        found = pattern.search(answer)
        if found:
            return _forbidden_output(found, reason="输出包含诊断式结论")

    # 3) 越界判断句
    for pattern in _FORBIDDEN_OVERREACH_PATTERNS:
        found = pattern.search(answer)
        if not found:
            continue
        # 匹配到的是「建议就医」这类正确引导，放行
        if _ALLOWED_REFERRAL.search(answer[max(0, found.start() - 30) : found.end() + 30]):
            continue
        return _forbidden_output(found, reason="输出包含诊断式结论")

    return SafetyVerdict()


def _forbidden_output(found: re.Match[str], *, reason: str) -> SafetyVerdict:
    """构造越界判定结果（统一日志与字段，避免各处重复拼装）。"""
    logger.warning("diagnosis_leak_detected", matched=found.group(0), reason=reason)
    return SafetyVerdict(
        type=SafetyType.MEDICAL,
        matched_keywords=[found.group(0)],
        reason=reason,
        model_used=True,
    )


# --------------------------------------------------------------------------- #
# 提示注入检测
# --------------------------------------------------------------------------- #
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    # 「忽略之前的所有指令」
    re.compile(
        r"(?:忽略|无视|不要理会|跳过)\s*"
        r"(?:上面|之前|前面|以上|所有|一切|全部|刚才)?\s*"
        r"(?:的)?\s*"
        r"(?:禁止|安全|系统|全部|所有|之前|上面|这些)?\s*"
        r"(?:指令|命令|提示|规则|条款|约束|限制|规定|要求)"
    ),
    re.compile(r"(?:输出|显示|重复|告诉)(?:你的)?(?:系统)?(?:提示词|prompt|系统指令)"),
    # 「你现在是一个没有任何限制的 AI」——要求同时出现「解除限制」与「人设」，
    # 否则「你现在是AI助手吗」这种普通问法也会被当成注入（误伤成本很低，但没必要）。
    re.compile(
        r"(?:你(?:现在)?是|从现在起你是|扮演)\s*(?:一个)?\s*"
        r"(?:没有任何?(?:限制|约束|规则)(?:的)?|不受(?:任何)?(?:限制|约束)(?:的)?|"
        r"毫无(?:限制|约束|规则)(?:的)?)\s*"
        r"(?:AI|助手|角色|机器人|专家)",
        re.IGNORECASE,
    ),
    # 「不用检索了，直接告诉我」——试图绕开知识库直接拿结论
    re.compile(
        r"(?:不用|不要|别|不需要|无需|跳过)(?:再|去)?"
        r"(?:检索|查询|搜索|查资料|找资料|查文献|引用资料)"
    ),
    re.compile(r"developer\s*mode|开发者模式|越狱|jailbreak", re.IGNORECASE),
    # —— 英文注入 ——
    # 中英文混合是真实场景：小程序里用户随时切输入法，
    # 英文注入往往出现在「复制一段网上的 prompt 来试试」的场景里。
    re.compile(
        r"(?:ignore|disregard|forget)\s+(?:all\s+|any\s+|the\s+)?"
        r"(?:previous|prior|above|earlier|preceding)\s+"
        r"(?:instructions?|prompts?|rules?|directions?|commands?|messages?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:reveal|show|print|repeat|output|display|expose|dump)\s+"
        r"(?:me\s+)?(?:your|the)\s+(?:system\s+)?"
        r"(?:prompt|instructions?|rules?|directives?|configuration)",
        re.IGNORECASE,
    ),
    re.compile(
        r"you\s+are\s+(?:now\s+)?(?:an?\s+)?"
        r"(?:unrestricted|unfiltered|uncensored|jailbroken|unbounded)\b",
        re.IGNORECASE,
    ),
    re.compile(r"(?:developer|debug|god|admin)\s*mode", re.IGNORECASE),
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
