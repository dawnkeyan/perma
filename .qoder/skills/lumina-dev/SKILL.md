---
name: lumina-dev
description: Lumina（拾光）项目的通用开发技能。当需要在 Lumina 仓库中新增功能、修改后端/小程序代码、调整 Agent 流程或检索链路时使用。提供分层约束、文件定位表、提交前检查清单与常见任务的标准改动路径。
---

# Lumina 项目开发技能

> 面向 AI 助手与新成员。执行任何代码改动前先读本文件，再读 `docs/knowledge/02-architecture-map.md`。

## 一、项目一句话

Lumina 是一个**积极心理学 AI 情绪教练**微信小程序。后端用 Python/FastAPI/LangGraph，
检索走自研混合 RAG（向量 + BM25 + RRF + 重排），每次对话必须落到一个 ≤5 分钟的**微行动**。

三条不可动摇的产品约束：**不诊断、不替代、有行动**。

## 二、改动前必做的三件事

1. **定位文件**：对照 `docs/knowledge/02-architecture-map.md` 的「关键组件定位」表。
2. **确认依赖方向**：只能 `api → services → agents → llm/rag → infra`，禁止反向导入。
3. **确认安全影响**：对照 `docs/knowledge/06-safety.md` 的自查清单。

## 三、分层职责

| 层 | 目录 | 能做什么 | 不能做什么 |
| --- | --- | --- | --- |
| 传输层 | `app/api/v1/` | 解析请求、鉴权、限流、序列化 | 写业务逻辑 |
| 用例层 | `app/services/` | 编排「读数据→调 Agent→落库」 | 直接碰 HTTP 对象 |
| 领域层 | `app/agents/` | 状态机、节点、路由、安全判定 | 依赖传输层或存储实现 |
| 能力层 | `app/llm/`、`app/rag/`、`app/prompts/` | 通用能力，**不感知业务场景** | 直接访问数据库 |
| 基础层 | `app/core/`、`app/db/` | 配置、日志、错误、数据访问 | 包含业务规则 |

## 四、常见任务的标准改动路径

### 新增一个 API 接口

1. `app/schemas/<domain>.py` — 定义 Pydantic Request/Response
2. `app/services/<domain>_service.py` — 写用例逻辑
3. `app/api/v1/<domain>.py` — 挂路由
4. `app/core/errors.py` — 如需新错误码
5. `services/api/tests/` — 补契约测试
6. `docs/API.md` — 同步契约文档

### 新增一种意图

1. `app/agents/state.py` — `Intent` 枚举加一项
2. `app/agents/nodes.py` — `entry_node` 的识别逻辑加分支
3. `packages/prompts/system/*.yaml` — 加对应 Prompt
4. `app/agents/graph.py` — 如需独立节点则加边
5. `packages/datasets/golden/e2e/cases.jsonl` — 补评测用例

### 修改 Prompt

**永远不要直接改了就提交。** 完整流程见 `.qoder/skills/prompt-iteration/SKILL.md`。

### 调整检索策略

见 `.qoder/skills/rag-tuning/SKILL.md`。

### 线上出现 BadCase

见 `.qoder/skills/badcase-triage/SKILL.md`。

## 五、代码风格速查

- Python 3.12，全量 type hints，`from __future__ import annotations`
- 行宽 100，ruff 格式化，mypy 严格
- 日志用结构化 key=value，**禁止打印用户原文**
- 配置只从 `app.core.config.settings` 读，禁止 `os.getenv`
- 异常捕获具体类型，禁止裸 `except: pass`
- 注释写「为什么」不写「是什么」

## 六、提交前检查清单

- [ ] `make lint && make typecheck && make test` 全绿
- [ ] 新逻辑有对应单测
- [ ] 改了 Prompt/检索 → 补了回归用例、贴了评测对比表
- [ ] 改了数据库 → 迁移向后兼容
- [ ] 没有密钥、用户数据、模型权重进仓库
- [ ] 文档已同步（`docs/` + `docs/knowledge/`）
- [ ] PR 模板里的「回滚方式」已填写

## 七、常用命令

```bash
make help          # 全部命令
make init          # 首次初始化
make kb-ingest     # 导入知识库
make eval          # 跑评测
make lint typecheck test
make up-prod       # 生产编排
```

## 八、禁止事项

1. 绕过 `LLMGateway` 直接调供应商 SDK
2. 在危机路径里调用 LLM
3. 改 Prompt 不升版本、不跑评测
4. 修改已发布的 Alembic 迁移
5. 在 `api/v1` 写业务逻辑
6. 提交运行产物（`reports/`、`eval_results/`、`ops/data/`）
