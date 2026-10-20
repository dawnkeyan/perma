># 02 · 架构地图

> 目的：给出一张「文件级」的地图，改代码前先知道自己站在哪。
> 最后更新：2026-10-20

## 一、仓库全景

```
perma/
├── services/api/          # 后端服务（Python / FastAPI）
│   ├── app/
│   │   ├── core/          # 配置、日志、错误
│   │   ├── db/            # 数据访问与会话
│   │   ├── llm/           # 模型网关、供应商适配、计费
│   │   ├── rag/           # 分块、检索、上下文组装
│   │   ├── agents/        # LangGraph 状态与节点
│   │   ├── prompts/       # Prompt 注册表（加载 YAML）
│   │   ├── services/      # 用例编排
│   │   ├── api/v1/        # HTTP 路由
│   │   ├── schemas/       # Pydantic 契约
│   │   ├── eval/          # 评测执行、指标、门禁
│   │   └── worker/        # 异步任务进程
│   └── tests/             # 单元测试
├── apps/miniapp/          # 微信小程序（Taro 4 + React 18 + TS）
├── packages/
│   ├── prompts/system/    # 版本化 Prompt YAML
│   ├── knowledge/         # 心理学知识库（Markdown + frontmatter）
│   ├── datasets/golden/   # 黄金评测集 + baseline 快照
│   └── models/            # 微调数据构造与训练脚本
├── ops/                   # 镜像、编排、监控、脚本
├── docs/                  # 产品与技术文档
├── docs/knowledge/        # 本知识库
└── .qoder/                # AI 助手项目级技能与规则
```

## 二、分层与依赖方向

```
api/v1  ──►  services/  ──►  agents/  ──►  llm/  ──►  provider
   │             │              │            │
   │             │              └─► rag/ ───┘
   │             │                   │
   └──► schemas/ └───────────────────┴──► db/
```

**依赖只能从上往下**，禁止反向导入：

| 禁止 | 原因 | 正确做法 |
| --- | --- | --- |
| `agents/` 导入 `api/v1/` | 领域层依赖传输层，换协议就要改领域逻辑 | 领域层返回纯数据结构，路由层负责序列化 |
| `rag/` 导入 `agents/` | 检索层被编排逻辑污染，无法单独评测 | 检索层只接收 query，返回 chunks |
| `llm/` 导入 `db/` | 网关被存储细节绑死，难以做 mock 测试 | 需要落库时由调用方（nodes）做 |
| 任何层导入 `core.config` 之外的具体实现 | 硬编码依赖 | 通过 `core.config.settings` 读取配置 |

> `services/chat_service.py` 里用**局部 import**（函数体内 `from sqlalchemy import text`）是有意为之：避免模块级加载重量依赖，也方便测试时替换。

## 三、一次对话的完整链路

以 `POST /api/v1/chat` 为例：

| 步 | 位置 | 职责 | 关键点 |
| --- | --- | --- | --- |
| 1 | `api/v1/chat.py` | 鉴权、参数校验、限流 | Pydantic 校验失败统一 422 |
| 2 | `services/chat_service.py` | 读历史 / 情绪上下文 / 用户画像 | 只读最近 6 轮，控制 token |
| 3 | `agents/state.py::make_initial_state` | 构造 `CoachState` | 状态是 TypedDict，可序列化 |
| 4 | `agents/graph.py` | LangGraph 编排 | 图已编译成单例 |
| 5 | `agents/nodes.py::entry_node` | 意图识别 + 安全检测 | **安全检测最先执行** |
| 6 | `agents/graph.py::_route_from_entry` | 危机短路路由 | 危机 → `crisis` 节点 → END |
| 7 | `agents/nodes.py::retrieve_node` | 混合检索 | 向量 + BM25 + RRF + 重排 |
| 8 | `agents/nodes.py::generate_node` | 渲染 Prompt、调用网关 | 渲染后校验不泄露 system prompt |
| 9 | `agents/nodes.py::guard_node` | 输出守卫 | 诊断词 / 引用缺失 / 提示注入 |
| 10 | `agents/graph.py::_route_from_guard` | 失败回环补检索 | 最多重试 1 次 |
| 11 | `agents/nodes.py::action_node` | 提取微行动 | 必须落到 ≤5 分钟动作 |
| 12 | `agents/nodes.py::reflect_node` | 反思与埋点 | 记录 trace、cost、usage |
| 13 | `services/chat_service.py::_persist` | 落库 | 落库失败不阻断响应 |

### 状态对象 `CoachState`

定义在 `agents/state.py`，关键字段：

| 字段 | 含义 | 谁写 |
| --- | --- | --- |
| `query` | 用户本轮输入 | 初始 |
| `intent` | 意图枚举（含 `CRISIS`） | entry |
| `history` | 历史对话 | 初始 |
| `emotion_context` | 近期情绪记录摘要 | 初始 |
| `chunks` / `citations` | 检索结果 | retrieve |
| `answer` | 最终回答 | generate |
| `sections` | 四段式解析结果 | generate |
| `usage` | token / 成本 / guard flags | 多节点累加 |
| `retry_count` | guard 重试次数 | 路由函数 |
| `safety` | 安全判定结果 | entry / guard |

## 四、关键组件定位

| 组件 | 文件 | 什么时候需要改它 |
| --- | --- | --- |
| 模型网关 | `app/llm/gateway.py` | 换供应商、加缓存策略、改重试/熔断 |
| 供应商适配 | `app/llm/provider.py` | 接入新的 OpenAI 兼容端点 |
| 计费 | `app/llm/pricing.py` | 模型价格变化、加新模型 |
| 检索管线 | `app/rag/retriever.py` | 改融合策略、加重排、改召回数量 |
| 分块 | `app/rag/chunking.py` | 改切分粒度、父子块比例 |
| 上下文组装 | `app/rag/context.py` | 改情绪摘要与引用拼装 |
| 安全检测 | `app/agents/safety.py` | 加规则、调整阈值、危机话术更新 |
| Prompt 注册 | `app/prompts/registry.py` | 加模板变量、改渲染策略 |
| 评测执行 | `app/eval/runner.py` | 加评测类型、改数据集加载 |
| 门禁 | `app/eval/gate.py` | 改阈值、改只升不降规则 |

## 五、环境与编排

| 文件 | 作用 |
| --- | --- |
| `.env.example` | 全部配置项的样例，**改配置项必须同步这里** |
| `docker-compose.yml` | 开发编排，`profiles` 控制可选服务 |
| `docker-compose.prod.yml` | 生产编排：强制密码、限制资源、只暴露 nginx |
| `Makefile` | 所有常用命令的统一入口 |
| `ops/docker/api.Dockerfile` | 三阶段构建，非 root 运行 |
| `ops/scripts/deploy.sh` | 部署（构建→迁移→启动→健康→冒烟→失败回滚） |
| `ops/scripts/rollback.sh` | 回滚（代码 + 可选数据库） |
| `ops/scripts/smoke.sh` | 冒烟测试，含危机场景断言 |

### 常用 Profiles

```bash
docker compose --profile search up -d          # 启用 Elasticsearch
docker compose --profile quickwit up -d        # 启用 Quickwit（轻量 BM25）
docker compose --profile observability up -d   # 启用 Prometheus / Grafana / OTel
docker compose --profile local-models up -d    # 启用本地嵌入与 vLLM
```

## 六、扩展点：新增一个功能要动哪些文件

以「新增一种意图类型」为例：

1. `agents/state.py` — 意图枚举加一项
2. `agents/safety.py` — 如需专属安全规则则加
3. `agents/nodes.py` — entry 的意图识别加分支
4. `agents/graph.py` — 如需新节点则加边
5. `prompts/system/*.yaml` — 加对应 Prompt
6. `packages/datasets/golden/` — 补评测用例
7. `app/eval/metrics.py` — 如需新指标则加
8. `docs/API.md` / `docs/ARCHITECTURE.md` — 同步文档

完整流程见 [`docs/SOP.md`](../SOP.md) 的 SOP-2。
