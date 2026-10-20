# 项目上下文规则

> AI 助手在 Lumina 仓库工作时**必须**遵守。本文件是「在这个项目里什么是对的」的最高层约束。

## 一、项目是什么

Lumina 拾光是一个**积极心理学 AI 情绪教练**微信小程序。

后端：Python 3.12 + FastAPI + LangGraph + 混合 RAG（pgvector + ES BM25 + RRF + rerank）。
前端：Taro 4 + React 18 + TypeScript。
形态：模块化单体（不是微服务，**不需要 K8s**）。

## 二、三条产品原则（不可动摇）

1. **不诊断**——不给疾病诊断、用药建议
2. **不替代**——识别到危机立刻转介专业机构
3. **有行动**——每次对话落到一个 ≤5 分钟可完成的微行动

## 三、分层与依赖方向

```
api → services → agents → {llm, rag, prompts} → core
                                        ↓
                                       db
```

**只能向下依赖。** 以下为硬性禁止：

| 禁止 | 原因 |
| --- | --- |
| `app/llm` 导入 `app/agents` | 模型网关不该知道有 Agent |
| `app/rag` 导入 `app/services` | 检索是基础设施，不该编排业务 |
| `app/core` 导入任何上层 | core 是最底层 |
| `app/api/v1` 里写业务逻辑 | 接口层只做协议转换 |
| 绕过 `LLMGateway` 直接调供应商 SDK | 会绕过缓存、熔断、计费、观测 |

## 四、目录职责

| 目录 | 职责 | 不该出现 |
| --- | --- | --- |
| `app/api/v1/` | 路由、鉴权、参数校验、序列化 | 业务逻辑、DB 查询 |
| `app/schemas/` | 请求/响应 Pydantic 模型 | 业务规则 |
| `app/services/` | 用例编排（读数据→调 Agent→落库） | HTTP 对象、直接调 DB 之外的副作用 |
| `app/agents/` | 状态机、节点、路由、安全判定 | 依赖传输层或存储实现 |
| `app/llm/` | 模型调用、重试、熔断、缓存、计费 | 感知业务场景 |
| `app/rag/` | 分块、检索、融合、导入 | 直接写业务结论 |
| `app/prompts/` | Prompt 加载、版本、渲染 | 业务逻辑 |
| `app/core/` | 配置、日志、错误码 | 任何业务规则 |
| `app/db/` | ORM、会话、迁移、种子 | 业务编排 |
| `app/worker/` | 异步任务 | 处理同步请求 |
| `app/eval/` | 评测、门禁、报告 | 被业务代码调用 |

## 五、配置读取规范

- **所有配置从 `app.core.config.settings` 读**，禁止 `os.getenv` / `os.environ`
- 新增配置项：加到 `app/core/config.py` 的对应分组 → 同步 `.env.example` → 同步 `docs/SETUP.md`
- 敏感配置（Key、密码、Secret）必须有默认值 `""` 且在启动时校验必填项

## 六、代码风格

- Python 3.12，全量 type hints，文件头 `from __future__ import annotations`
- 行宽 100，格式化交给 `ruff format`
- 每个模块有 docstring 说明**负责什么**；每个公开函数有 docstring 说明**为什么这么写**
- 日志用 `structlog`（`get_logger(__name__)`），key=value 形式
- 异常捕获具体类型，禁止裸 `except: pass`
- 注释写「为什么」，不写「是什么」

## 七、跨仓库的资产

`packages/` 放**资产**（内容 + 离线工具），不放运行时代码：

| 目录 | 内容 | 消费方 |
| --- | --- | --- |
| `packages/knowledge/` | 心理学知识库 Markdown | 导入脚本 → 向量库 + ES |
| `packages/prompts/` | Prompt YAML（版本化） | 后端 `app/prompts/registry.py` |
| `packages/datasets/golden/` | 评测集 + 基线快照 | 评测器 + CI 门禁 |
| `packages/models/` | 微调脚本 + ModelScope 集成 | 离线微调流程（不依赖 API 服务） |

## 八、常用命令

```bash
make help          # 全部命令
make init          # 首次初始化（起依赖 + 建表 + 建索引）
make install       # 装 Python 依赖
make kb-ingest     # 导入知识库
make eval          # 离线评测
make lint typecheck test
```

## 九、改动前必答的三个问题

1. **这个改动该落在哪一层？**（对照第四节表格）
2. **它是否违反依赖方向？**
3. **它是否触碰安全红线？**（见 `safety-and-privacy.md`）

任何一条答不上来，先别写代码。
