# Lumina API

Lumina 拾光的后端服务。一个 FastAPI 应用，承载「积极心理学 AI 情绪教练」的
全部服务端能力：混合 RAG 检索、LangGraph 编排、安全护栏、评测体系与异步任务。

## 它在整个仓库里的位置

```
lumina/
├── apps/miniapp/          # 微信小程序（Taro 4 + React 18）
├── services/api/          # ← 你在这里：后端服务
├── packages/
│   ├── knowledge/          # 心理学知识库（Markdown）
│   ├── prompts/            # Prompt 定义（YAML，版本化）
│   ├── datasets/           # 评测集（golden set）
│   └── models/             # 微调脚本与 ModelScope 集成
└── ops/                    # 部署编排与监控
```

## 技术栈

| 领域 | 选型 |
| --- | --- |
| Web 框架 | FastAPI + Uvicorn |
| 数据校验 | Pydantic v2 |
| ORM / 迁移 | SQLAlchemy 2.0 (async) + Alembic |
| 编排 | LangGraph（StateGraph） |
| 检索 | pgvector (HNSW) + Elasticsearch BM25 + RRF 融合 + reranker |
| 队列 | Redis + arq |
| 模型接入 | OpenAI 兼容协议（云端 API / vLLM 自部署 / TEI） |
| 观测 | structlog + Prometheus + OpenTelemetry |

## 目录结构

| 目录 | 职责 |
| --- | --- |
| `app/core/` | 配置、日志、错误码——所有模块的公共底座 |
| `app/db/` | ORM 模型、会话管理、初始化与种子数据 |
| `app/llm/` | 模型网关、Provider 适配、用量计费 |
| `app/rag/` | 分块、检索、融合、知识库导入 |
| `app/agents/` | LangGraph 图、节点、状态、安全护栏 |
| `app/prompts/` | Prompt 注册中心（加载 / 版本 / 渲染） |
| `app/services/` | 用例编排层（把各层串成完整用例） |
| `app/api/v1/` | HTTP 路由与契约 |
| `app/schemas/` | 请求/响应 Pydantic 模型 |
| `app/worker/` | arq 异步任务（画像抽取、摘要压缩、指标聚合） |
| `app/eval/` | 评测执行、门禁、报告生成 |
| `tests/` | 单元测试（安全测试是 P0 红线守护） |

详细说明见 [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md)。

## 本地运行

```bash
# 1. 起依赖（Postgres + Redis）
make init

# 2. 装依赖（会连带安装 dev extras）
pip install -e ".[dev]"

# 3. 起服务
make dev-api      # API，热重载
make dev-worker   # 异步任务
```

## 常用命令

```bash
make lint        # ruff check + format
make typecheck   # mypy
make test        # pytest + 覆盖率
make eval        # 离线评测
make kb-ingest   # 导入知识库
```

## 安全红线

本服务对「积极心理学情绪教练」这个定位有**硬性边界**，由 `app/agents/safety.py`
实现、由 `tests/test_safety.py` 守护：

1. **不诊断**——不给出任何疾病诊断或用药建议
2. **危机场景绝不调用模型**——命中危机信号时直接返回硬编码话术与求助渠道
3. **不承诺**——不做「一定会好」这类虚假承诺

改动 `app/agents/safety.py` 前，**必须先让 `tests/test_safety.py` 通过**。

## 依赖边界

- 服务运行依赖见 `pyproject.toml` 的 `dependencies`
- 微调相关依赖（torch / peft / transformers）**不在**这里，见 `packages/models/requirements.txt`
- 评测扩展依赖（ragas / datasets）见 `pyproject.toml` 的 `eval` extras
