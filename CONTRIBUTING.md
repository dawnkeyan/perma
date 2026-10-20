# 贡献指南

感谢你愿意为 Lumina 拾光投入时间。这份文档说明**怎么提 PR、代码要写成什么样、
哪些红线不能碰**。

---

## 目录

- [快速开始](#快速开始)
- [分支与提交](#分支与提交)
- [代码规范](#代码规范)
- [测试要求](#测试要求)
- [安全红线（不可协商）](#安全红线不可协商)
- [Prompt 变更流程](#prompt-变更流程)
- [PR 流程](#pr-流程)
- [报告问题](#报告问题)

---

## 快速开始

```bash
# 1. Fork 并克隆
git clone https://github.com/<你的账号>/lumina.git
cd lumina

# 2. 装依赖
python3 -m venv .venv && source .venv/bin/activate
make install          # 等价于 pip install -e "services/api[dev]"

# 3. 起依赖服务
make init

# 4. 改代码前先跑一遍测试，确认基线是绿的
make test
```

所有可用命令见 `make help`。

---

## 分支与提交

### 分支命名

| 类型 | 命名 | 例子 |
| --- | --- | --- |
| 功能 | `feat/<简述>` | `feat/bm25-channel` |
| 修复 | `fix/<简述>` | `fix/crisis-missed-soft-phrasing` |
| 重构 | `refactor/<简述>` | `refactor/llm-gateway-provider` |
| 文档 | `docs/<简述>` | `docs/eval-ablation-table` |
| 运维 | `ops/<简述>` | `ops/grafana-cost-panel` |

主干分支：`main`（生产）、`develop`（集成）。**不要直接向 `main` 提 PR。**

### 提交信息

遵循 [Conventional Commits](https://www.conventionalcommits.org/)：

```
<type>(<scope>): <简短描述>

<可选正文：为什么这么改，而不只是改了什么>

<可选脚注：BREAKING CHANGE / Closes #123>
```

| type | 用于 |
| --- | --- |
| `feat` | 新功能 |
| `fix` | 缺陷修复 |
| `refactor` | 不改变外部行为的重构 |
| `perf` | 性能优化 |
| `test` | 只改测试 |
| `docs` | 只改文档 |
| `build` / `ci` | 构建与流水线 |
| `chore` | 杂项（依赖更新、格式化） |

scope 用模块名：`rag` / `llm` / `agents` / `safety` / `prompts` / `eval` / `api` / `miniapp` / `ops`。

**反例**（这些提交信息会让历史失去可读性）：

```
learn rag
update
fix bug
test
临时改一下
```

---

## 代码规范

### Python（`services/api`）

由 `ruff` + `mypy` 强制，不靠人工 Review：

```bash
make lint       # ruff check . && ruff format --check .
make fmt        # 自动修复
make typecheck  # mypy app
```

要点：

- **类型注解必须完整**——`mypy` 配置了 `disallow_untyped_defs = true`，所有公开函数都要标注。
- **行宽 100**，格式化交给 `ruff format`，不要手动对齐。
- **模块顶部必须有 docstring**，说明这个模块负责什么。
- **公开函数必须有 docstring**，说明**为什么**这么写（不是复述代码）。
- 日志用 `structlog`（`from app.core.logging import get_logger`），不要用 `print`。
- 业务异常继承 `app/core/errors.py` 里的基类，不要裸抛 `Exception`。

### TypeScript（`apps/miniapp`）

```bash
cd apps/miniapp && npm run lint && npm run typecheck
```

- 组件用函数式 + Hooks，不要用 class 组件
- 状态用 Zustand，不要塞进 Context 层层传递
- 请求统一走 `src/services/api.ts`，不要在组件里直接 `wx.request`

### 通用

- 注释写**为什么**，不写**是什么**。`# 增加计数器` 是废话，`# 重试次数用指数退避，避免雪崩` 才有价值。
- 魔法数字要提成常量，并注明来源（论文、协议、配置项）。
- 不要提交调试代码、注释掉的代码块、`print`/`console.log`。

---

## 测试要求

| 改动范围 | 必须补的测试 |
| --- | --- |
| `app/agents/safety.py` | `tests/test_safety.py`——**全部**，且必须新增覆盖场景 |
| `app/eval/metrics.py` | 指标边界值（0、空、异常输入） |
| `app/rag/chunking.py` | 父子关系、token 预算、标题路径 |
| `app/rag/retriever.py` | 融合公式、排序、limit、边界 |
| `app/prompts/registry.py` | 版本选择、渲染、缺变量报错 |
| 新增 API 端点 | 请求校验失败、越权、未授权三种情况 |
| 任何逻辑 | 至少一条**失败路径**的断言（只测 happy path 等于没测） |

```bash
make test         # 全部单元测试 + 覆盖率
make test-e2e     # 需要外部依赖的集成测试
```

CI 的覆盖率门槛是 **70%**，`app/eval/` 除外（评测代码靠真实跑评测验证）。

---

## 安全红线（不可协商）

以下三条是这个产品**存在的前提**。任何改动不得削弱它们：

### 1. 危机场景绝不调用模型

`services/api/app/agents/safety.py` 的 `crisis_response()` 必须是**纯规则**实现。
在 `app/agents/graph.py` 中，危机分支必须在 `entry` 节点直接短路。

```python
# ❌ 绝对不允许：让模型生成危机回应
answer = await gateway.chat(crisis_prompt)

# ✅ 必须是：硬编码话术 + 求助渠道
return crisis_response(user_text)   # model_used == False
```

`tests/test_safety.py::test_crisis_response_never_uses_model` 会守住这条线。

### 2. 不做医疗诊断

不允许出现疾病判断、用药建议、剂量建议。
「建议你去医院」是**正确**引导，可以有；「你这是抑郁症」是**违规**。

### 3. 不做虚假承诺

不允许出现「一定会好」「保证你」「会过去的」这类表达。

### 隐私

- 日志与链路追踪中**不得出现**用户原文、模型完整输入输出
  （由 `ops/otel/otel-collector.yaml` 的 scrub 处理器兜底）
- 数据库查询日志**不得打印**用户内容
- `app/db/models.py` 中所有 `__repr__` 都刻意不输出用户原文，**不要改回去**

---

## Prompt 变更流程

Prompt 是模型行为的一部分，**它的变更等同于代码变更**：

```bash
# 1. 改 packages/prompts/system/coach.yaml
# 2. 一定要改 version（+1），并在 changelog 里写清楚改了什么、为什么
# 3. 跑评测，看指标变化
make eval
python -m app.eval.report
# 4. 把新分数写回 yaml 的 eval_scores
# 5. 评测门禁通过后，才可以合并
```

**不要把 `version` 留在原地不 bump。** 没有版本号的 Prompt 变更无法回滚，
线上出问题时你无法知道该回到哪一版。

### 评测门禁

CI 会把本次评测结果与 `packages/datasets/golden/snapshot/baseline.json` 对比：

- 质量类指标允许小幅下跌（各自有阈值和硬性下限）
- 安全类指标**只允许上升**

门禁失败时，**先理解为什么退化**，再决定是改代码还是显式更新基线。
不要为了让 CI 变绿而随手 `--update-baseline`——基线是「质量水位」，不是「当前水位」。

---

## PR 流程

1. **开分支**：`git checkout -b feat/xxx`
2. **改代码**：小步提交，每个提交都能独立跑通测试
3. **本地验证**：
   ```bash
   make lint && make typecheck && make test
   ```
4. **推送**：`git push -u origin feat/xxx`
5. **开 PR**：填模板里的「为什么」和「怎么验证」两栏
6. **等 CI**：四个 job 全绿才允许合并
7. **合并**：`develop` 用 squash merge；`main` 用 merge commit 保留历史

### PR 描述模板

```markdown
## 改了什么
（做了什么，一两句话）

## 为什么改
（**重点**：不写清楚这个 PR 大概率会被打回。说明问题现象、根因、为什么用这个方案）

## 怎么验证
（ reviewer 怎么确认它是对的。给出可复现的步骤或命令 )

## 影响面
- [ ] 只影响后端 / 只影响前端 / 涉及知识库 / 涉及 Prompt
- [ ] 是否需要同步更新文档
- [ ] 是否改变了 API 契约
```

### Code Review 看什么

- 依赖方向有没有被破坏（见 `docs/PROJECT_STRUCTURE.md`）
- 错误路径处理是否完整（别只处理 happy path）
- 是否有硬编码的密钥、地址、用户数据
- 日志里会不会漏出用户内容
- 新增的断言是否真的能失败（写完把条件改坏试试）

---

## 报告问题

请用 [Bug 模板](.github/ISSUE_TEMPLATE/bug_report.yml)，务必包含：

- 复现步骤
- 实际结果 vs 预期结果
- 相关日志（**先脱敏**：去掉 openid、token、手机号、用户原文）
- 环境信息（本地 / 测试环境 / 生产；模型版本；Prompt 版本）

安全问题请不要开公开 Issue，参照 [SECURITY.md](SECURITY.md)。

---

## 行为准则

参与即表示同意 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
