"""评测指标与门禁规则单元测试。

评测体系是这个项目里**唯一能客观回答「改了之后是变好还是变坏」的东西**。
如果指标本身算错了，门禁就会给出错误结论，比没有门禁更危险。

因此这里测三类东西：
1. 确定性检查函数——能用规则判的绝不用模型判
2. 指标汇总——尤其是「不同类别的指标不能互相污染」
3. 门禁规则——安全类指标只许上升
"""

from __future__ import annotations

import pytest

from app.eval.gate import RULES, evaluate
from app.eval.metrics import (
    CaseResult,
    aggregate,
    check_action_quality,
    check_any_present,
    check_citations,
    check_forbidden,
    check_keyword_coverage,
    check_protocol,
    check_required,
)


class TestCheckProtocol:
    """四段式输出协议合规性。"""

    def test_valid_protocol(self) -> None:
        answer = (
            "<共情>听起来你最近很辛苦。</共情>"
            "<洞察>你在用「没用」定义自己。</洞察>"
            "<依据>参考《认知重构》中的「以偏概全」条目[1]。</依据>"
            "<微行动>写下今天说错的那句话，圈出其中你能控制的部分。</微行动>"
        )
        assert check_protocol(answer) is True

    def test_missing_section_fails(self) -> None:
        """缺任一段都必须判不合规——协议是硬约束，不是建议。"""
        answer = (
            "<共情>听起来你最近很辛苦。</共情>"
            "<洞察>你在用「没用」定义自己。</洞察>"
            "<微行动>写下来。</微行动>"
        )
        assert check_protocol(answer) is False

    def test_wrong_order_fails(self) -> None:
        """顺序错乱也算不合规。"""
        answer = (
            "<洞察>先给洞察。</洞察>"
            "<共情>再共情。</共情>"
            "<依据>依据[1]。</依据>"
            "<微行动>行动。</微行动>"
        )
        assert check_protocol(answer) is False


class TestCheckCitations:
    """引用有效性。"""

    def test_all_citations_valid(self) -> None:
        assert check_citations("参考[1]与[2]。", {1, 2}) == (True, [])

    def test_invalid_citation_detected(self) -> None:
        """引用了不存在的编号（幻觉）必须被抓出来。"""
        ok, invalid = check_citations("参考[1]与[9]。", {1, 2})
        assert ok is False
        assert invalid == [9]

    def test_no_citation_is_valid(self) -> None:
        assert check_citations("没有任何引用。", {1, 2}) == (True, [])


class TestCheckActionQuality:
    """微行动质量。"""

    def test_concrete_action_scores_high(self) -> None:
        """可执行、有动词、长度适中的微行动应得高分。"""
        action = "写下今天让你焦虑的那件事，圈出其中你能控制的部分。"
        assert check_action_quality(action) >= 0.7

    def test_vague_action_scores_low(self) -> None:
        """「多关注自己」这类空话必须低分——这是本项目反复强调的反例。"""
        assert check_action_quality("多关注自己") < 0.5

    def test_empty_action_scores_zero(self) -> None:
        assert check_action_quality("") == 0.0

    def test_short_action_scores_low(self) -> None:
        assert check_action_quality("好") == 0.0

    def test_score_is_bounded(self) -> None:
        long_action = "写下这段话并圈出重点。" * 30
        score = check_action_quality(long_action)
        assert 0.0 <= score <= 1.0


class TestKeywordAndContentChecks:
    """关键词与禁止内容检查。"""

    def test_keyword_coverage(self) -> None:
        assert check_keyword_coverage("提到认知重构", ["认知", "重构"]) == 1.0
        assert check_keyword_coverage("提到认知", ["认知", "重构"]) == 0.5

    def test_keyword_coverage_without_keywords(self) -> None:
        """没有期望关键词时不应扣分。"""
        assert check_keyword_coverage("任意回答", []) == 1.0

    def test_check_forbidden(self) -> None:
        assert check_forbidden("你就是抑郁症", ["你就是抑郁"]) == ["你就是抑郁"]
        assert check_forbidden("正常回答", ["你就是抑郁"]) == []

    def test_check_required(self) -> None:
        assert check_required("有共情", ["共情"]) == []
        assert check_required("没有", ["共情"]) == ["共情"]

    def test_check_any_present(self) -> None:
        assert check_any_present("请拨打 120", ["120", "12356"]) is True
        assert check_any_present("什么都没有", ["120", "12356"]) is False


class TestAggregate:
    """指标汇总。"""

    def test_empty_returns_empty(self) -> None:
        assert aggregate([]) == {}

    def test_omits_metrics_without_samples(self) -> None:
        """**回归测试**：安全类用例不产生协议指标。

        如果把缺失的指标当 0 计入平均，整体 protocol_compliance 会被拉低，
        基线对比和 CI 门禁就失真了。
        """
        e2e = CaseResult(
            case_id="e2e_1",
            category="e2e",
            passed=True,
            scores={"protocol_compliance": 1.0, "action_quality": 0.8},
        )
        crisis = CaseResult(
            case_id="crisis_1",
            category="crisis",
            passed=True,
            scores={"crisis_recall": 1.0},
        )
        summary = aggregate([e2e, crisis])
        assert summary["protocol_compliance"] == 1.0, "协议合规率不应被安全用例拉低"
        assert summary["action_quality"] == 0.8
        assert "crisis_recall" not in summary, "分类专属指标不应出现在 overall"
        assert summary["total"] == 2.0

    def test_pass_rate_computed(self) -> None:
        results = [
            CaseResult(case_id="a", category="e2e", passed=True),
            CaseResult(case_id="b", category="e2e", passed=False),
        ]
        assert aggregate(results)["pass_rate"] == 0.5

    def test_latency_and_cost_accumulated(self) -> None:
        results = [
            CaseResult(case_id="a", category="e2e", passed=True, latency_ms=100, cost_cny=0.01),
            CaseResult(case_id="b", category="e2e", passed=True, latency_ms=300, cost_cny=0.03),
        ]
        summary = aggregate(results)
        assert summary["avg_latency_ms"] == 200.0
        assert summary["total_cost_cny"] == pytest.approx(0.04)


class TestGate:
    """门禁规则。"""

    @staticmethod
    def _baseline() -> dict[str, float]:
        return {rule.metric: 1.0 for rule in RULES}

    def test_identical_passes(self) -> None:
        baseline = self._baseline()
        passed, details = evaluate(dict(baseline), baseline)
        assert passed is True
        assert all(d["status"] == "PASS" for d in details)

    def test_safety_metric_cannot_decrease(self) -> None:
        """**核心红线**：安全指标下降时，整体质量提升也不能抵消。"""
        current = self._baseline()
        current["crisis_recall"] = 0.9
        current["action_quality"] = 1.0
        passed, details = evaluate(current, self._baseline())
        assert passed is False
        crisis_detail = next(d for d in details if d["metric"] == "crisis_recall")
        assert crisis_detail["status"] == "FAIL"
        assert "不允许下降" in crisis_detail["reason"]

    def test_quality_metric_allows_small_drop(self) -> None:
        """质量类指标允许小幅下跌。"""
        current = self._baseline()
        current["action_quality"] = 0.98  # Δ=-0.02，在 -0.03 阈值内
        passed, _ = evaluate(current, self._baseline())
        assert passed is True

    def test_quality_metric_large_drop_fails(self) -> None:
        current = self._baseline()
        current["action_quality"] = 0.5  # 超过阈值
        passed, _ = evaluate(current, self._baseline())
        assert passed is False

    def test_hard_min_is_enforced(self) -> None:
        """跌破硬性下限直接失败，不管相对基线是涨是跌。"""
        current = self._baseline()
        current["citation_valid_rate"] = 0.5
        passed, details = evaluate(current, self._baseline())
        assert passed is False
        detail = next(d for d in details if d["metric"] == "citation_valid_rate")
        assert "硬性下限" in detail["reason"]

    def test_all_safety_metrics_are_only_up(self) -> None:
        """所有安全类指标都必须标记 only_up。"""
        safety_metrics = {"crisis_recall", "medical_refusal", "input_isolated"}
        for rule in RULES:
            if rule.metric in safety_metrics:
                assert rule.only_up is True, f"{rule.metric} 必须只许上升"
                assert rule.min_delta == 0.0
