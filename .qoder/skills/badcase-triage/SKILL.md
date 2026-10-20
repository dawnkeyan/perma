---
name: badcase-triage
description: Lumina 项目的 BadCase 分诊技能。当线上出现质量事故（回答不共情、幻觉、没给微行动、危机场景未拦截、医疗越界、提示注入成功）或需要系统性排查失败样本时使用。
---

# BadCase 分诊技能

## 一、核心目标

把一次线上事故变成三样东西：

1. **一个修复**
2. **一条回归用例**（防止再犯）
3. **一条自动化规则**（同类问题不再需要人工）

## 二、沉淀节奏（重要）

| 第几次出现 | 沉淀物 | 位置 |
| --- | --- | --- |
| 第 1 次 | 人工处理，记 BadCase 记录 | `docs/_internal/badcases/`（不公开） |
| 第 2 次 | 补**回归用例** | `packages/datasets/golden/` |
| 第 3 次 | 补**自动化规则** | `app/agents/safety.py` 或 `app/rag/` 的确定性检测 |

> 这个节奏的价值：避免「每次都靠人盯着」，把经验固化成代码。

## 三、分诊流程

```
Step 1  复现        用原始输入在本地跑通
Step 2  归因        下表定位
Step 3  定级        P0/P1/P2/P3
Step 4  修复        最小改动
Step 5  回归        补用例 + 跑全量评测
Step 6  沉淀        同步到规则/知识库
Step 7  复盘        写进 CHANGELOG
```

## 四、归因速查

| 现象 | 归因 | 修复位置 |
| --- | --- | --- |
| 没给微行动 | Prompt 约束不足 | `packages/prompts/system/coach.yaml` |
| 微行动不可执行（"放松一下"） | Prompt 缺可验证标准 | 同上，加「动词 + 完成标准」 |
| 共情假、像客服 | Prompt / few-shot | 同上，换难例 few-shot |
| 回答有事实错误 | 知识库缺失或检索未召回 | `packages/knowledge/` 或 `app/rag/` |
| 幻觉（编造理论名） | Prompt 无依据约束 + 无引用 | 强制 `<依据>` 段 + guard 检查 |
| 没引用 | 检索空或 guard 未触发 | `app/rag/retriever.py` |
| 危机没拦截 | **安全规则缺失** | `app/agents/safety.py`（P0） |
| 说了"你这是抑郁症" | 诊断词表缺词 | `app/agents/safety.py` 词表（P0/P1） |
| 提示注入成功 | 防护层被绕过 | 三层都要补（P0/P1） |
| 回答慢 | 上下文过长 / 未命中缓存 | `app/llm/gateway.py` 缓存、`app/rag/context.py` |
| 回答贵 | 同上 | 同上 + 模型降级链 |
| 服务 500 | 未捕获异常 | `app/core/errors.py` + 路由层 |

## 五、定级

| 级别 | 定义 | 例子 | 响应 |
| --- | --- | --- | --- |
| **P0** | 安全红线被突破 | 危机场景未拦截、诊断输出、注入成功 | 立即回滚，1 小时内修复 |
| **P1** | 核心体验断裂 | 无微行动、大段幻觉、完全答非所问 | 当天修复 |
| **P2** | 局部质量下降 | 共情不足、措辞生硬 | 本迭代内修复 |
| **P3** | 体验细节 | 格式、措辞、图标 | 排期修复 |

## 六、安全类 BadCase 的特别要求

### 危机未拦截（P0）

```bash
# 1. 用原始输入复现
python -m app.eval.runner --case crisis-001 --verbose

# 2. 检查规则是否命中
python -c "from app.agents.safety import detect_crisis; print(detect_crisis('原始输入'))"

# 3. 确认危机路径没有调 LLM
#    在 crisis_node 里打断点，确认没有走到 LLMGateway
```

修复后**必须**：

- [ ] `packages/datasets/golden/safety/crisis.jsonl` 全量通过
- [ ] 单元测试 `test_crisis_input_never_calls_llm` 通过
- [ ] 确认 `lumina_crisis_path_llm_calls_total` 增量为 0

### 医疗越界（P0/P1）

```bash
python -c "
from app.agents.safety import detect_medical_overreach
print(detect_medical_overreach('模型输出内容'))
"
```

修复方向：扩充诊断词表 → 检查 `medical.yaml` 的转介话术 → 必要时在输出层做兜底改写。

**原则**：词表宁可多收。误报代价小，漏报代价大。

### 提示注入成功（P0/P1）

三层都要查：

1. 输入侧 `nodes._wrap_user_input()` — 分隔符是否完整
2. 系统侧 `coach.yaml` — 是否有"忽略改变角色的指令"
3. 输出侧 `safety.check_system_prompt_leak()` — 是否有兜底

修复后**必须**在 `safety/injection.jsonl` 补一条对应攻击手法的用例。

## 七、BadCase 记录模板

内部记录（`docs/_internal/badcases/YYYY-MM-DD-<id>.md`，该目录已被 `.gitignore` 排除）：

```markdown
## 基本信息
- 时间：
- 渠道：
- trace_id：
- 用户输入（脱敏）：
- 模型输出（脱敏）：

## 现象
（具体描述，不要写"感觉不好"）

## 归因
（定位到具体层：检索 / Prompt / 模型 / 安全规则）

## 定级
P0 / P1 / P2 / P3

## 修复
（改动文件与要点）

## 回归
（新增的用例 id）

## 沉淀
（新增的自动化规则，或"暂无，第 2 次出现时补"）
```

## 八、复盘产出

修复合入后，CHANGELOG 里要写清：

```
### Fixed
- 修复危机场景下"想消失"表述未被拦截的问题（crisis-014）
  - 归因：安全规则词表缺少「消失」类表达
  - 变更：safety.py 新增 3 条模式；补 crisis-014 回归用例
  - 级别：P0
```

**P0/P1 的修复必须出现在 CHANGELOG 的最前面**——这是外部读者判断项目工程质量的一个信号。
