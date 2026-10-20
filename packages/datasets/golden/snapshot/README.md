# 评测基线快照（snapshot）

这个目录存放**离线评测的基线快照**，是 CI 回归门禁的比对基准。

## 目录内容

| 文件 | 说明 |
| --- | --- |
| `baseline.json` | 基线指标快照。包含 overall、by_category 与已知失败用例。 |

## 为什么要有基线

只看「绝对分数高不高」是不够的。比如把 `action_quality` 的门槛从 0.75 降到 0.60，
绝对分数依然「达标」，但质量其实退化了。

基线快照记录的是**上一次被认可的质量水位**。CI 每次跑评测都和它比：

- 质量类指标（`protocol_compliance` / `citation_valid_rate` / `action_quality` / `keyword_coverage`）
  允许小幅下跌（阈值见 `app/eval/gate.py` 的 `RULES`），但有硬性下限。
- 安全类指标（`crisis_recall` / `medical_refusal` / `input_isolated`）
  **只允许上升**，不允许用整体质量提升来抵消安全退步。

## 如何更新基线

```bash
# 1. 跑一次完整评测
make eval

# 2. 生成报告并对比基线
python -m app.eval.report

# 3. 确认门禁通过、且你确实认可这次提升后，再更新基线
python -m app.eval.report --update-baseline
```

> ⚠️ `--update-baseline` 只在门禁 PASS 时才会写文件。**不要为了让 CI 变绿而随手更新基线**——
> 基线是「质量水位」，不是「当前水位」。

## 基线里有什么、没有什么

**有**：指标数值、分类别指标、已知失败用例。

**没有**：逐条用例明细（答案全文、延迟、成本）。这些体积大且含用户语义，
不适合进 Git；需要时看 CI artifact `eval-report` 或本地 `reports/eval/current.json`。

## 相关文件

- 评测执行器：`services/api/app/eval/runner.py`
- 门禁规则：`services/api/app/eval/gate.py`
- 报告生成：`services/api/app/eval/report.py`
- 指标定义：`services/api/app/eval/metrics.py`
- 评测集：`packages/datasets/golden/`
