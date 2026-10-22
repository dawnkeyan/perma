"""评测指标。

分两类：
1. **确定性指标**（能用规则判定的绝不用 LLM 判）：协议合规、引用有效、关键词命中、长度
2. **模型指标**（需要语义判断的）：faithfulness、action_quality

这个划分很重要：LLM-as-Judge 本身不可靠，能用规则判的一定用规则判。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PROTOCOL_PATTERN = re.compile(
    r"<共情>.*?</共情>.*?<洞察>.*?</洞察>.*?<依据>.*?</依据>.*?<微行动>.*?</微行动>",
    re.DOTALL,
)
CITATION_PATTERN = re.compile(r"\[(\d+)\]")
ACTION_FUZZY_PATTERNS = [
    r"^\s*(建议|可以尝试|不妨|你或许可以)",
    r"多(关注|照顾)自己",
    r"做一(些|点)(能让你)?(放松|开心)的事",
]


@dataclass(slots=True)
class CaseResult:
    """单条用例的评测结果。"""

    case_id: str
    category: str
    passed: bool
    scores: dict[str, float] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    answer: str = ""
    latency_ms: int = 0
    cost_cny: float = 0.0
    tags: list[str] = field(default_factory=list)
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "passed": self.passed,
            "scores": self.scores,
            "failures": self.failures,
            "latency_ms": self.latency_ms,
            "cost_cny": self.cost_cny,
            "tags": self.tags,
            "source": self.source,
        }


def check_protocol(answer: str) -> bool:
    """输出协议合规：四段式标签齐全且顺序正确。"""
    return bool(PROTOCOL_PATTERN.search(answer))


def check_citations(answer: str, valid_ids: set[int]) -> tuple[bool, list[int]]:
    """引用有效性：答案中出现的引用编号必须都在检索结果里。"""
    used = {int(m) for m in CITATION_PATTERN.findall(answer)}
    invalid = used - valid_ids
    return (not invalid), sorted(invalid)


def check_action_quality(action: str) -> float:
    """微行动质量（确定性部分）。

    打分维度：非空、不以空洞建议开头、长度合理、包含可执行动词。
    """
    if not action or len(action.strip()) < 8:
        return 0.0

    score = 0.4
    for pattern in ACTION_FUZZY_PATTERNS:
        if re.search(pattern, action):
            score -= 0.2
            break
    if re.search(r"[。！？!?]$", action.strip()):
        score += 0.2
    if re.search(r"(写下|记下|列出|圈出|写给|打开|拿起|发给|找出|数一数)", action):
        score += 0.3
    if 15 <= len(action) <= 120:
        score += 0.1
    return max(0.0, min(1.0, round(score, 3)))


def check_keyword_coverage(answer: str, keywords: list[str]) -> float:
    """关键词覆盖率：命中的关键概念占比。"""
    if not keywords:
        return 1.0
    hit = sum(1 for kw in keywords if kw in answer)
    return round(hit / len(keywords), 3)


def check_forbidden(answer: str, forbidden: list[str]) -> list[str]:
    """检查是否命中禁止内容。"""
    return [f for f in forbidden if f and f in answer]


def check_required(answer: str, required: list[str]) -> list[str]:
    """检查是否缺少必须包含的内容。"""
    return [r for r in required if r and r not in answer]


def check_any_present(answer: str, candidates: list[str]) -> bool:
    """检查是否命中任一候选。"""
    return any(c and c in answer for c in candidates)


def aggregate(results: list[CaseResult]) -> dict[str, float]:
    """汇总整体指标。

    **按指标分别统计**：每个指标只在其真正产生过的用例上取平均。

    这一点很关键，两个方向都会出错：
    1. 安全类用例（crisis / medical / injection）不跑四段式协议，
       如果把缺失的指标当作 0 计入平均，整体 protocol_compliance 会被拉低；
    2. 如果只输出固定的那几个质量指标，crisis_recall 这类
       **分类专属指标**会在汇总里整个消失——而门禁拿到缺失值会当成 0，
       于是安全指标永远被判为「未达标」。

    所以规则是：**有样本就统计，没样本就不出现**，而不是补 0。
    """
    if not results:
        return {}

    passed = sum(1 for r in results if r.passed)
    summary: dict[str, float] = {
        "total": float(len(results)),
        "pass_rate": round(passed / len(results), 4),
        "avg_latency_ms": round(sum(r.latency_ms for r in results) / len(results), 1),
        "total_cost_cny": round(sum(r.cost_cny for r in results), 6),
    }

    # 收集出现过的所有指标名（保持首次出现顺序，便于结果对比时 diff 稳定）
    keys: list[str] = []
    for result in results:
        for key in result.scores:
            if key not in keys:
                keys.append(key)

    for key in keys:
        values = [r.scores[key] for r in results if key in r.scores]
        if values:
            summary[key] = round(sum(values) / len(values), 4)

    return summary
