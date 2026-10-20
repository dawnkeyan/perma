---
name: prompt-iteration
description: Lumina 项目的 Prompt 迭代技能。当需要修改 packages/prompts/ 下的 Prompt、调整模型输出格式、增强或修复某类回答质量时使用。强制执行「归因→最小改动→评测→版本化」流程，禁止凭感觉改 Prompt。
---

# Prompt 迭代技能

## 一、铁律

> **没有评测数据支撑的 Prompt 改动 = 没改。**

违反以下任一条都算本次迭代失败：

- 凭主观感觉判断「变好了」
- 一次改了多个地方（无法归因）
- 改了 Prompt 但版本号没升
- 改了 Prompt 但没跑评测
- 评测没过却更新了 baseline

## 二、标准流程

```
Step 1  收集失败样本     → 从 golden/e2e + 线上 BadCase
Step 2  归因             → 用下面的归因对照表定位问题层级
Step 3  最小改动         → 一次只改一处
Step 4  跑全量评测       → make eval
Step 5  对比 baseline    → python -m app.eval.gate
Step 6  通过 → 升版本 + 更新 eval_scores + 写 changelog
        不通过 → 回滚，把「此方向无效」写进 changelog
```

## 三、归因对照表（决定改什么）

| 现象 | 归因层 | 改哪里 |
| --- | --- | --- |
| 检索没召回相关内容 | 检索 | `app/rag/` — 不要动 Prompt |
| 召回了但理解错 | 检索/分块 | `app/rag/chunking.py` |
| 理解对但共情假 | Prompt | 加 few-shot / 强化共情段 |
| 说教口吻（"你应该"） | Prompt | 加否定指令 |
| 微行动不可执行 | Prompt | 加「动词 + 完成标准」约束 |
| 输出结构乱/标签缺失 | 格式 | 强化 XML 标签协议 + 降温度 |
| 术语用错 | 知识库 | `packages/knowledge/` 补条目 |
| 偶发（不是必现） | 模型能力 | 换模型 / 加守卫规则 / 降低温度 |

**关键判断**：如果同一问题在多个用例上出现且表现一致 → Prompt 问题；
如果只在特定输入上出现 → 先怀疑检索。

## 四、改动位置

Prompt 在 `packages/prompts/system/*.yaml`：

| 文件 | 用途 | 何时改 |
| --- | --- | --- |
| `coach.yaml` | 主教练对话（核心） | 绝大多数改动 |
| `knowledge.yaml` | 知识问答 | 涉及理论解释时 |
| `medical.yaml` | 医疗越界转介 | 医疗相关话术 |
| `chitchat.yaml` | 闲聊 | 寒暄类 |

### YAML 结构约定

```yaml
name: coach
version: 5                    # 每次改动 +1
status: production            # draft/testing/production/deprecated
model_scope: ["qwen-plus"]    # 该 Prompt 适用哪些模型
changelog:
  - version: 5
    date: 2026-10-20
    reason: 微行动缺少可验证的完成标准
    result: action_specificity 0.78 → 0.86
eval_scores:
  faithfulness: 0.91
  empathy: 0.85
  action_specificity: 0.86
  format_compliance: 1.00
template: |
  ...
```

**`status` 的流转**：`draft` → `testing`（跑通评测）→ `production`（灰度验证）→ 废弃时 `deprecated`。

## 五、七条技巧在本项目中的落点

1. **角色 + 任务 + 约束**三段式 → `coach.yaml` 顶部
2. **XML 标签约束输出** → `<共情><洞察><依据><微行动>` 四段式
3. **Few-shot 用难例** → 特意放「用户说没动力」这类难处理例
4. **明确的否定指令** → 「不要诊断」「不要使用『你应该』」
5. **输出前自检清单** → 「输出前确认是否给了可执行动作」
6. **温度与场景绑定** → 洞察 0.7 / 提取 0.1 / 分类 0.0
7. **版本化 + 评测绑定** → 本文件全流程

## 六、评测怎么跑

```bash
# 全量
make eval

# 只跑安全集（PR 必跑，快）
python -m app.eval.runner --dataset packages/datasets/golden/safety

# 门禁比对
python -m app.eval.gate \
  --current reports/eval/current.json \
  --baseline packages/datasets/golden/snapshot/baseline.json
```

## 七、更新 baseline 的正确姿势

**禁止在 CI 里自动更新 baseline**（会让门禁退化成「记录现状」）。

正确流程：

1. 本地跑评测，拿到 `reports/eval/current.json`
2. 逐项对比 baseline，**确认每一处变化都是可接受的**
3. 安全类指标**不允许任何下跌**
4. 手动更新 `packages/datasets/golden/snapshot/baseline.json`
5. 在 PR 描述里写清：哪些指标变了、变了多少、为什么可接受

## 八、常见反例与正解

| 反例 | 为什么错 | 正解 |
| --- | --- | --- |
| 在 Prompt 里加 20 行新规则 | 无法归因，且可能冲突 | 一次加一条，跑评测 |
| 让模型输出 JSON 以便解析 | JSON 失败率高且难承载语气 | 用 XML 标签 |
| 改完 Prompt 直接发版 | 线上才知道变差 | 门禁 + 灰度 |
| 评测没过就调 baseline | 门禁失去意义 | 先想清楚是改动错了还是评测有问题 |
| 用 few-shot 教模型说"你应该" | 与产品约束冲突 | few-shot 必须是合规输出 |
