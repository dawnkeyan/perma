"""评测报告生成器。

把评测执行器产出的原始 JSON 渲染成**人可读的 Markdown 报告**，
并与基线快照做逐指标对比，给出「能不能合并」的结论。

职责边界：
- `app.eval.runner` 负责**跑**（产出结果 JSON）
- `app.eval.gate`   负责**判**（返回退出码，CI 用）
- `app.eval.report` 负责**写**（生成报告，人看）

三者共用同一套指标定义（`app.eval.metrics`）与门禁规则（`app.eval.gate`），
避免出现「报告说通过、CI 说失败」这种自相矛盾的情况。

用法：
    # 生成报告（默认读 reports/eval/current.json，输出 reports/eval/report.md）
    python -m app.eval.report

    # 指定输入输出
    python -m app.eval.report --current reports/eval/current.json --out reports/eval/report.md

    # 门禁通过后更新基线快照（需要显式加 --update-baseline）
    python -m app.eval.report --update-baseline
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.eval.gate import RULES, evaluate

# 本模块刻意只依赖标准库 + app.eval.gate：
# 评测报告在 CI 里是独立的一步，且本地排查时常常在没装齐依赖的环境下运行。
# 与 app.eval.gate 保持同一设计——评测工具链不应该因为缺一个日志库就跑不起来。

# 默认路径：与 CI workflow 中的路径保持一致
DEFAULT_CURRENT = Path("reports/eval/current.json")
DEFAULT_BASELINE = Path("packages/datasets/golden/snapshot/baseline.json")
DEFAULT_OUTPUT = Path("reports/eval/report.md")


# --------------------------------------------------------------------------- #
# 读取与展平
# --------------------------------------------------------------------------- #
def _flatten(raw: dict[str, Any]) -> dict[str, float]:
    """把 overall + by_category 摊平成「指标 → 数值」。

    与 `app.eval.gate._flatten` 保持同一套逻辑：同名指标取简单平均。
    门禁只关心趋势，不要求加权精确。
    """
    flat: dict[str, float] = {}
    flat.update(raw.get("overall", {}))
    for category_metrics in raw.get("by_category", {}).values():
        for key, value in category_metrics.items():
            if key == "total":
                continue
            if key not in flat:
                flat[key] = value
            else:
                flat[key] = round((flat[key] + value) / 2, 4)
    return flat


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"文件不存在：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #
def _fmt_delta(delta: float) -> str:
    """把差值渲染成带箭头的字符串。"""
    if delta > 0:
        return f"+{delta:.4f} ↑"
    if delta < 0:
        return f"{delta:.4f} ↓"
    return "0.0000 ="


def render_markdown(
    current_raw: dict[str, Any],
    details: list[dict[str, Any]],
    passed: bool,
) -> str:
    """渲染 Markdown 报告。

    注意：`details` 已经由 `evaluate(current, baseline)` 算好了每个指标的
    基线值、当前值与差值，所以这里不再单独接收 baseline——
    否则就会出现「两个来源的基线」这种不一致的风险。
    """
    overall = current_raw.get("overall", {})
    by_category = current_raw.get("by_category", {})
    failures = current_raw.get("failures", [])

    lines: list[str] = []
    lines.append("# Lumina 评测报告")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now(UTC).astimezone().isoformat(timespec='seconds')}")
    lines.append(f"- 门禁结论：**{'PASS ✅' if passed else 'FAIL ❌'}**")
    lines.append(f"- 用例总数：{int(overall.get('total', 0))}")
    lines.append(f"- 通过率：{overall.get('pass_rate', 0):.2%}")
    lines.append(f"- 平均延迟：{overall.get('avg_latency_ms', 0):.0f} ms")
    lines.append(f"- 本次成本：¥{overall.get('total_cost_cny', 0):.4f}")
    lines.append("")

    # ---- 指标对比 ----
    lines.append("## 指标对比")
    lines.append("")
    lines.append("| 指标 | 说明 | 基线 | 当前 | Δ | 状态 |")
    lines.append("| --- | --- | ---: | ---: | ---: | :---: |")
    for detail in details:
        status = "✅" if detail["status"] == "PASS" else "❌"
        lines.append(
            f"| `{detail['metric']}` | {detail['description']} "
            f"| {detail['baseline']:.4f} | {detail['current']:.4f} "
            f"| {_fmt_delta(detail['delta'])} | {status} |"
        )
    lines.append("")

    # ---- 分类别明细 ----
    if by_category:
        lines.append("## 分类别指标")
        lines.append("")
        for category, metrics in by_category.items():
            total = int(metrics.get("total", 0))
            rate = metrics.get("pass_rate", 0)
            lines.append(f"### `{category}`（{total} 条，通过率 {rate:.2%}）")
            lines.append("")
            lines.append("| 指标 | 数值 |")
            lines.append("| --- | ---: |")
            for key, value in metrics.items():
                if key == "total":
                    continue
                lines.append(f"| `{key}` | {value} |")
            lines.append("")

    # ---- 失败用例 ----
    lines.append("## 失败用例")
    lines.append("")
    if not failures:
        lines.append("无失败用例 🎉")
    else:
        lines.append("| 用例 | 类别 | 失败原因 |")
        lines.append("| --- | --- | --- |")
        for failure in failures:
            reasons = ", ".join(failure.get("failures", []))
            lines.append(f"| `{failure['case_id']}` | {failure['category']} | {reasons} |")
    lines.append("")

    # ---- 门禁规则说明 ----
    lines.append("## 门禁规则")
    lines.append("")
    lines.append("安全类指标（`only_up`）只允许上升，不允许用整体质量提升来抵消。")
    lines.append("")
    lines.append("| 指标 | 允许跌幅 | 硬性下限 | 只许上升 |")
    lines.append("| --- | ---: | ---: | :---: |")
    for rule in RULES:
        lines.append(
            f"| `{rule.metric}` | {rule.min_delta:+.2f} | {rule.hard_min:.2f} "
            f"| {'是' if rule.only_up else '否'} |"
        )
    lines.append("")

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description="生成 Lumina 评测报告")
    parser.add_argument(
        "--current",
        default=str(DEFAULT_CURRENT),
        help="本次评测结果 JSON（app.eval.runner --out 的产物）",
    )
    parser.add_argument(
        "--baseline",
        default=str(DEFAULT_BASELINE),
        help="基线快照 JSON",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUTPUT),
        help="Markdown 报告输出路径",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="门禁通过后用本次结果覆盖基线快照（谨慎使用）",
    )
    args = parser.parse_args()

    current_path = Path(args.current)
    baseline_path = Path(args.baseline)
    out_path = Path(args.out)

    try:
        current_raw = _load_json(current_path)
    except FileNotFoundError:
        print(
            f"[error] 评测结果文件不存在：{current_path}\n"
            f"        请先执行：make eval\n"
            f"        或：python -m app.eval.runner --out {current_path}",
            file=sys.stderr,
        )
        sys.exit(2)

    baseline_raw: dict[str, Any] = {}
    if baseline_path.exists():
        baseline_raw = _load_json(baseline_path)
    else:
        print(
            f"[warn] 基线快照不存在：{baseline_path}（本次将按「首次运行」处理）", file=sys.stderr
        )

    current = _flatten(current_raw)
    baseline = _flatten(baseline_raw)
    passed, details = evaluate(current, baseline)

    markdown = render_markdown(current_raw, details, passed)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")
    print(f"报告已生成：{out_path}")
    print(f"门禁结论：{'PASS' if passed else 'FAIL'}")

    # 只有门禁通过才允许更新基线——避免把一次劣化固化成「新标准」
    if args.update_baseline:
        if not passed:
            print("门禁未通过，拒绝更新基线。", file=sys.stderr)
            sys.exit(1)
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(current_raw, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"基线已更新：{baseline_path}")


if __name__ == "__main__":
    main()
