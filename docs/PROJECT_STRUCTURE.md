# 项目结构说明

> 本文档回答三个问题：**这个仓库长什么样**、**每个文件和目录是干啥的**、**我要改 X 该动哪里**。
>
> 约定：所有目录树中，`#` 后面的文字是**该文件/目录的职责**。

---

## 1. 一句话概览

Lumina 拾光是一个**积极心理学 AI 情绪教练**微信小程序。服务端采用**模块化单体**架构：
单体部署（省资源、易运维），但内部按职责分层（可演进到微服务）。

**技术栈一句话**：FastAPI + LangGraph + 混合 RAG（pgvector + BM25 + RRF）+ Redis/arq + OpenTelemetry，
模型侧走「云端 API 为主 + 本地微调模型自部署」的混合供给。

**仓库规模**：136 个文件 / 约 2.4 万行（不含依赖与构建产物）。

---

## 2. 分层架构图

```
┌──────────────────────────────────────────────────────────────────────┐
│  接入层  apps/miniapp                                                │
│  微信小程序（Taro 4 + React 18 + TS）                                │
│    pages/chat      对话页                                            │
│    pages/journal   情绪日记页                                        │
│    services/       API 封装 / 登录态                                 │
│    utils/track     埋点上报                                          │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ HTTPS / JSON
┌───────────────────────────────▼──────────────────────────────────────┐
│  接口层  services/api/app/api/v1                                     │
│  chat.py   对话（流式 SSE）  journal.py  日记  health.py  健康检查  │
│  ──────────────────── 鉴权 / 参数校验 / 统一错误码 ─────────────────  │
└───────────────────────────────┬──────────────────────────────────────┘
┌───────────────────────────────▼──────────────────────────────────────┐
│  用例编排层  services/api/app/services                               │
│  chat_service.py  一次完整对话的编排（取历史 → 调图 → 落库）        │
│  ─────────── 唯一允许「跨层调用」的地方，且只向下 ───────────        │
└───────────────────────────────┬──────────────────────────────────────┘
┌───────────────────────────────▼──────────────────────────────────────┐
│  编排层  services/api/app/agents        【LangGraph StateGraph】     │
│                                                                      │
│    entry ──crisis?──► crisis ──────────────────────────► END        │
│      │                                                          │
│      └──► retrieve ──► generate ──► guard ──► action ──► reflect   │
│                                         ▲        │                  │
│                                         └──重试──┘                  │
│    graph.py   图结构 / 条件边    nodes.py  各节点实现                 │
│    state.py   状态定义        safety.py  安全护栏（P0 红线）        │
└──────┬──────────────────────┬──────────────────────┬────────────────┘
       │                      │                      │
┌──────▼──────────┐  ┌────────▼─────────┐  ┌─────────▼──────────────┐
│ 检索层 app/rag  │  │ 模型层 app/llm   │  │ 提示层 app/prompts     │
│ chunking  分块  │  │ gateway  统一网关│  │ registry 版本化加载     │
│ retriever 混合  │  │ provider 适配器 │  │ （读 packages/prompts） │
│ context   上下文│  │ pricing   计费  │  │                        │
│ ingest    导入  │  │ schemas   契约  │  │                        │
└──────┬──────────┘  └────────┬─────────┘  └────────────────────────┘
       │                      │
┌──────▼──────────────────────▼───────────────────────────────────────┐
│  基础设施层  app/core · app/db                                       │
│  core/config  配置    core/logging  结构化日志   core/errors  错误码 │
│  db/models   10 张表 ORM    db/session  会话    db/init_db  初始化 │
└──────┬──────────────────────┬───────────────────────────────────────┘
       │                      │
┌──────▼──────────┐  ┌────────▼─────────┐  ┌────────────────────────┐
│ PostgreSQL 16   │  │ Redis 7          │  │ 外部模型服务            │
│ + pgvector HNSW │  │ 语义缓存 / 队列  │  │ 云端 API / vLLM / TEI  │
│ + Elasticsearch │  │                  │  │                        │
└──────────────────┘  └──────────────────┘  └────────────────────────┘
```

### 依赖方向（硬约束）

```
api → services → agents → {llm, rag, prompts} → core
                                        ↓
                                       db
```

**只能向下依赖，禁止反向依赖。** 具体来说：

| 禁止 | 原因 |
| --- | --- |
| `app/llm` 依赖 `app/agents` | 模型网关不该知道有 Agent 这回事 |
| `app/rag` 依赖 `app/services` | 检索是基础设施，不该编排业务 |
| `app/core` 依赖任何上层 | core 是最底层 |
| 任何层直接 `import app.api` | 接口层只做协议转换 |

违反这条规则是本项目最常见的架构腐化形式。CI 的 `mypy` + `ruff` 无法完全拦住，
**Code Review 时要重点看 import 方向**。

---

## 3. 完整目录树

```
lumina/
│
├── README.md                      # 项目门面：产品是什么、技术选型、怎么跑起来
├── CHANGELOG.md                   # 变更记录（Keep a Changelog 格式）
├── CONTRIBUTING.md                # 贡献指南：分支规范、提交规范、PR 流程
├── CODE_OF_CONDUCT.md             # 行为准则
├── SECURITY.md                    # 安全策略与漏洞披露渠道
├── LICENSE                        # MIT 许可证
├── Makefile                       # 统一命令入口（make help 看全部）
├── docker-compose.yml             # 开发编排：postgres + redis + es + api + worker
├── docker-compose.prod.yml        # 生产编排叠加层：资源限制、只暴露 nginx
├── .env.example                   # 环境变量模板（含每个变量的说明与是否必填）
├── .env                           # 本地真实配置（已被 .gitignore 排除，绝不提交）
├── .gitignore                     # 忽略规则
├── .dockerignore                  # 构建镜像时的排除规则（减小镜像体积）
├── .editorconfig                  # 缩进/换行统一
├── .gitattributes                 # 行尾与 diff 行为统一
│
├── .github/                       # GitHub 自动化
│   ├── workflows/
│   │   ├── ci.yml                 # 4 个 job：后端质量 / 前端质量 / 离线评测门禁 / 安全扫描
│   │   └── release.yml            # 镜像构建与发布（GHCR）
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug_report.yml         # Bug 模板（含复现步骤、日志脱敏提示）
│   │   └── feature_request.yml    # 需求模板
│   └── PULL_REQUEST_TEMPLATE.md   # PR 模板（要求写「为什么」和「怎么验证」）
│
├── .qoder/                        # IDE 侧项目资产（让 AI 助手懂这个项目）
│   ├── rules/                     # 项目规则：编码约定、安全红线、提交规范
│   └── skills/                    # 项目技能：可复用的操作流程
│       ├── lumina-dev/SKILL.md        # 日常开发流程（含安全红线检查项）
│       ├── prompt-iteration/SKILL.md  # Prompt 迭代闭环（改→评→对比→定版）
│       └── badcase-triage/SKILL.md    # BadCase 归因流程
│
├── docs/                          # 对外文档层（面向人，不是面向机器）
│   ├── PRD.md                     # 产品需求文档：用户、场景、功能、指标、里程碑
│   ├── ARCHITECTURE.md            # 技术方案：整体架构、技术选型理由、取舍记录
│   ├── API.md                     # 接口契约：端点、参数、响应、错误码
│   ├── DATA_MODEL.md              # 数据模型：表结构、索引、ER 关系、迁移策略
│   ├── EVALUATION.md              # 评测方案：指标定义、消融实验表、CI 门禁规则
│   ├── SOP.md                     # 可操作 SOP：从零到上线的每一步
│   ├── RUNBOOK.md                 # 运维手册：告警处置、故障预案、扩容步骤
│   ├── SETUP.md                   # 环境准备：要装什么软件、注册什么账号、拿哪些 Key
│   ├── DEPLOY_CLOUD.md            # 云服务器部署教程与选型推荐（含 AI 运维指令模板）
│   ├── PROJECT_STRUCTURE.md       # 本文档：目录/文件说明 + 架构图
│   └── knowledge/                 # 项目知识库（AI 助手的长期记忆）
│       ├── README.md                  # 知识库索引与阅读顺序
│       ├── 01-domain-glossary.md      # 领域词汇表：情绪标签、干预方法、证据等级
│       ├── 02-architecture-map.md     # 模块地图：每个模块干什么、边界在哪
│       ├── 03-dev-conventions.md      # 开发约定：命名、分层、错误处理、日志规范
│       ├── 04-rag-prompt.md           # RAG 与 Prompt 的设计决策与调优手段
│       ├── 05-evaluation.md           # 评测体系：怎么跑、怎么看、怎么定基线
│       ├── 06-safety.md               # 安全红线：不可协商的边界与守护方式
│       └── 07-faq.md                  # 高频问答：为什么这么做、常见误解
│
├── apps/
│   └── miniapp/                   # 微信小程序（Taro 4 + React 18 + TS）
│       ├── package.json           # 依赖与脚本（dev:weapp / build:weapp / lint / typecheck）
│       ├── tsconfig.json          # TS 配置
│       ├── config/index.ts        # Taro 构建配置
│       └── src/
│           ├── app.ts             # 小程序入口：全局状态与生命周期
│           ├── app.config.ts      # 页面路由与窗口配置
│           ├── app.scss           # 全局样式与设计变量
│           ├── config.ts          # 环境配置（开发/生产 API 地址）
│           ├── env.d.ts           # 类型声明
│           ├── pages/
│           │   ├── chat/index.tsx     # 对话页：SSE 流式渲染、微行动卡片
│           │   └── journal/index.tsx  # 情绪日记页：打分 + 标签 + 历史
│           ├── services/
│           │   ├── api.ts              # 后端 API 封装（统一错误处理）
│           │   └── auth.ts             # 微信登录换 token
│           └── utils/track.ts          # 埋点上报（北极星指标：有效干预次数）
│
├── services/
│   └── api/                       # 后端服务（Python 3.12 + FastAPI）
│       ├── pyproject.toml         # 依赖声明 + ruff/mypy/pytest 配置
│       ├── README.md              # 服务级说明
│       ├── app/
│       │   ├── main.py            # ASGI 入口：中间件、路由挂载、生命周期
│       │   ├── core/               # 公共底座
│       │   │   ├── config.py       #   Pydantic Settings：所有配置项集中在此
│       │   │   ├── logging.py      #   structlog 配置：JSON 日志 + 敏感字段脱敏
│       │   │   └── errors.py       #   统一错误码与异常处理器
│       │   ├── api/v1/             # HTTP 路由
│       │   │   ├── chat.py         #   对话接口（流式 SSE + 同步两种模式）
│       │   │   ├── journal.py      #   情绪日记 CRUD
│       │   │   └── health.py       #   健康检查（存活/就绪分离）
│       │   ├── schemas/            # 请求/响应模型
│       │   │   ├── chat.py         #   对话相关契约（四段式响应的结构定义）
│       │   │   └── __init__.py
│       │   ├── services/           # 用例编排层
│       │   │   └── chat_service.py #   一次对话的完整编排：取上下文→跑图→落库
│       │   ├── agents/             # LangGraph 编排层
│       │   │   ├── graph.py        #   图结构：节点、边、条件路由、checkpoint
│       │   │   ├── nodes.py        #   各节点实现（检索/生成/守卫/行动/反思）
│       │   │   ├── state.py        #   状态定义（TypedDict）+ 意图枚举
│       │   │   └── safety.py       #   安全护栏：危机/医疗/注入（P0 红线）
│       │   ├── rag/                # 检索层
│       │   │   ├── chunking.py     #   分块：标题层级切分 + 父子块（small-to-big）
│       │   │   ├── retriever.py    #   混合检索：向量 + BM25 → RRF → rerank
│       │   │   ├── context.py      #   上下文组装：引用编号分配与格式化
│       │   │   └── ingest.py       #   知识库导入：解析→分块→向量化→建索引
│       │   ├── llm/                # 模型层
│       │   │   ├── gateway.py      #   统一网关：重试/熔断/降级/缓存/计量
│       │   │   ├── provider.py     #   Provider 适配（云端 / vLLM / TEI）
│       │   │   ├── pricing.py      #   单价表：按模型算成本（成本归因基础）
│       │   │   └── schemas.py      #   LLM 请求/响应契约
│       │   ├── prompts/registry.py #   Prompt 注册中心：加载/版本/渲染
│       │   ├── db/                 # 数据层
│       │   │   ├── models.py       #   10 张表 ORM 模型
│       │   │   ├── session.py      #   异步会话管理
│       │   │   ├── init_db.py      #   初始化：建扩展→建表→建 HNSW 索引
│       │   │   └── seed.py         #   演示数据种子
│       │   ├── worker/             # 异步任务（arq）
│       │   │   ├── main.py         #   Worker 定义与 4 个任务
│       │   │   └── __main__.py     #   入口（python -m app.worker）
│       │   └── eval/               # 评测体系
│       │       ├── runner.py       #   执行器：跑评测集，产出结果 JSON
│       │       ├── metrics.py      #   指标定义：确定性检查 + 汇总
│       │       ├── gate.py         #   门禁规则：与基线比对，决定能不能合并
│       │       └── report.py       #   报告生成：结果 JSON → Markdown 报告
│       └── tests/                  # 单元测试
│           ├── conftest.py         #   全局夹具与环境变量注入
│           ├── test_safety.py      #   【P0】安全红线守护测试
│           ├── test_chunking.py    #   分块契约测试
│           ├── test_rag_retrieval.py #  RRF 融合公式测试
│           ├── test_eval_metrics.py #  评测指标与门禁规则测试
│           └── test_prompts_registry.py #  Prompt 版本化与渲染测试
│
├── packages/                      # 跨服务共享的「资产」，不含运行时代码
│   ├── knowledge/                 # 心理学知识库（Markdown，带 frontmatter）
│   │   ├── positive-psychology/perma.md              # PERMA 模型
│   │   ├── cbt/cognitive-restructuring.md             # 认知重构
│   │   ├── behavioral-activation/behavioral-activation.md # 行为激活
│   │   └── via/strengths.md                          # VIA 优势识别
│   ├── prompts/system/             # Prompt 定义（YAML，版本化）
│   │   ├── coach.yaml             #   情绪教练主提示词（v4）
│   │   ├── knowledge.yaml         #   知识问答提示词
│   │   ├── chitchat.yaml          #   闲聊提示词
│   │   └── medical.yaml           #   医疗越界转介提示词
│   ├── datasets/golden/            # 评测集（Golden Set）
│   │   ├── e2e/cases.jsonl        #   端到端用例（15 条）
│   │   ├── safety/crisis.jsonl    #   危机场景用例（8 条）
│   │   ├── safety/medical.jsonl   #   医疗越界用例（8 条）
│   │   ├── safety/injection.jsonl #   提示注入用例（5 条）
│   │   └── snapshot/baseline.json #   评测基线快照（CI 门禁比对基准）
│   └── models/                    # 微调与 ModelScope（魔搭）集成
│       ├── README.md              #   全链路说明：拉取→造数据→微调→部署→接入
│       ├── requirements.txt       #   微调依赖（与 API 服务依赖隔离）
│       ├── finetune/
│       │   ├── ms_swift_lora_intent.yaml    # 主微调配置（ms-swift）
│       │   └── llamafactory_lora_intent.yaml # 备选对拍配置（LLaMA-Factory）
│       └── scripts/
│           ├── download_model.py     #   从魔搭拉取模型/适配器
│           ├── build_dataset.py     #   从 BadCase + 黄金集构造 SFT 数据
│           └── push_to_modelscope.py #  推送 LoRA 到魔搭并生成模型卡
│
└── ops/                           # 部署与可观测性
    ├── docker/
    │   ├── api.Dockerfile         # API/Worker 镜像（多阶段构建）
    │   └── nginx.conf             # 反向代理：TLS、限流、静态资源
    ├── prometheus/
    │   ├── prometheus.yml         # 抓取配置
    │   └── alerts.yml              # 告警规则（错误率/延迟/成本/队列积压）
    ├── grafana/
    │   ├── provisioning/datasources/datasources.yml # 数据源自动配置
    │   ├── provisioning/dashboards/dashboards.yml   # 仪表盘自动加载
    │   └── dashboards/lumina-overview.json          # 总览看板
    ├── otel/otel-collector.yaml    # OTel Collector：采集→脱敏→导出
    └── scripts/
        ├── bootstrap.sh            # 首次初始化：装依赖、建目录、拉配置
        ├── deploy.sh               # 部署：拉镜像→迁移→切流→健康检查
        ├── smoke.sh                # 冒烟：部署后验证关键链路
        ├── backup.sh               # 备份：数据库 + 知识库索引
        └── rollback.sh             # 回滚：镜像回退 + 数据库迁移回退
```

---

## 4. 关键数据流

### 4.1 一次对话的完整链路

```
小程序            API              ChatService        LangGraph           检索/模型        DB
  │                │                    │                 │                  │            │
  │ POST /chat     │                    │                 │                  │            │
  │───────────────►│                    │                 │                  │            │
  │                │── 取历史(6) ────────────────────────────────────────────────────────►│
  │                │◄───────────────────── 消息列表 ──────────────────────────────────────│
  │                │── 取情绪上下文/画像 ────────────────────────────────────────────────►│
  │                │                    │                 │                  │            │
  │                │── make_initial_state ─►│            │                  │            │
  │                │                    │── ainvoke ───►│                  │            │
  │                │                    │                 │                  │            │
  │                │                    │            [entry 节点]            │            │
  │                │                    │                 │                  │            │
  │                │                    │            危机？──是──►[crisis]    │            │
  │                │                    │                 │       硬编码话术 │            │
  │                │                    │                 │       (不调模型) │            │
  │                │                    │            否 │                  │            │
  │                │                    │            [retrieve]               │            │
  │                │                    │                 │──向量检索───────►│            │
  │                │                    │                 │──BM25 检索──────►│            │
  │                │                    │                 │◄──RRF 融合──────│            │
  │                │                    │                 │──rerank───────►│            │
  │                │                    │            [generate]               │            │
  │                │                    │                 │──调模型───────►│            │
  │                │                    │            [guard] 输出校验          │            │
  │                │                    │                 │ (诊断/泄露)     │            │
  │                │                    │            [action] 微行动落地        │            │
  │                │                    │            [reflect] 反思           │            │
  │                │                    │                 │                  │            │
  │                │◄──── 落库(失败不阻断) ────────────────────────────────────────────────│
  │◄── 四段式响应 + 引用 + 微行动卡片                                    │            │
```

**三个设计要点**：

1. **危机在 `entry` 就短路**——不进入检索、不调用模型。零延迟、零成本、100% 可预期。
2. **落库失败不阻断响应**——用户要的是答案，不是数据库状态。数据丢了可以补，用户体验断了就是事故。
3. **守卫失败只回环一次**（`MAX_GUARD_RETRY = 1`）——避免无限重试打爆模型配额。

### 4.2 知识库导入链路

```
packages/knowledge/*.md
        │  parse_markdown（解析 frontmatter）
        ▼
    Document
        │  split_by_heading（标题层级 + 父子块）
        ▼
   Chunk(256/1024) ──── embed_chunks ────► LLM 网关 ──► 向量
        │
        ├─────────────────────────────────────► pgvector（余弦相似度 + HNSW）
        │
        └─────────────────────────────────────► Elasticsearch（BM25 + IK 中文分词）
```

增量策略：`content_hash` 比对，内容没变则跳过向量化和索引写入。

### 4.3 评测与门禁链路

```
packages/datasets/golden/*.jsonl
        │  app.eval.runner
        ▼
   逐条执行 → 确定性检查（协议/引用/微行动/关键词）
        │           + 安全用例走规则路径（不调模型）
        ▼
   reports/eval/current.json
        │  app.eval.gate（与 baseline.json 比对）
        ▼
   PASS / FAIL ──FAIL──► CI 阻断合并
        │
        └──PASS──► app.eval.report ──► reports/eval/report.md（人看）
```

---

## 5. 「我要改 X，该动哪里」索引

| 我想改… | 主要文件 | 附带要改的地方 |
| --- | --- | --- |
| 模型的回答风格 | `packages/prompts/system/coach.yaml` | 改 version + changelog → 跑 `make eval` → 更新 `eval_scores` |
| 检索召回质量 | `services/api/app/rag/retriever.py`、`chunking.py` | 消融实验表 `docs/EVALUATION.md` |
| 知识库内容 | `packages/knowledge/**/*.md` | 跑 `make kb-ingest` 更新向量与索引 |
| 安全边界 | `services/api/app/agents/safety.py` | **必须**同步 `tests/test_safety.py`，并更新 `docs/knowledge/06-safety.md` |
| 输出协议 | `packages/prompts/system/*.yaml` + `app/eval/metrics.py` | 两处要一致，否则协议检查会全量失败 |
| 意图识别 | `services/api/app/agents/nodes.py`（意图分类段） | `app/agents/state.py` 的 `Intent` 枚举 |
| 数据库表结构 | `services/api/app/db/models.py` | 生成 Alembic 迁移 + 更新 `docs/DATA_MODEL.md` |
| 异步任务 | `services/api/app/worker/main.py` | 新增任务记得在 `WorkerSettings.cron_jobs` 注册 |
| 成本 | `services/api/app/llm/pricing.py` | 上游调价时改这里；`llm_calls` 表会自动归因 |
| 评测门槛 | `services/api/app/eval/gate.py` 的 `RULES` | **降低门槛需要在 PR 里说明理由** |
| 小程序界面 | `apps/miniapp/src/pages/**` | 改完跑 `npm run typecheck && npm run lint` |
| 监控看板 | `ops/grafana/dashboards/lumina-overview.json` | 改完在 Grafana 里导入验证 |
| 部署流程 | `ops/scripts/*.sh`、`docker-compose.prod.yml` | 改完用 `docker compose config -q` 校验 |
| 告警规则 | `ops/prometheus/alerts.yml` | 改完用 `promtool check rules` 校验 |

---

## 6. 目录设计原则

1. **`packages/` 放资产，不放代码**
   知识库、Prompt、评测集、微调脚本都是「内容」或「离线工具」，不属于任何运行时服务。
   它们被 CI、后端、微调流程共同消费，因此独立于 `services/`。

2. **`app/` 内部按「层」而不是按「功能」分**
   是 `app/llm/gateway.py` 而不是 `app/gateway/llm.py`。
   理由：加一个新能力时，问的问题是「它属于哪一层」，而不是「它和谁相关」。

3. **`ops/` 不进镜像**
   部署编排和监控配置由运维单独管理，不打进业务镜像。
   只有 `ops/docker/api.Dockerfile` 会进构建流程。

4. **`docs/knowledge/` 与 `docs/` 分开**
   `docs/` 是给人读的正式文档（PRD、架构、运维手册）；
   `docs/knowledge/` 是给 AI 助手读的长期记忆（词汇表、约定、决策理由）。
   前者可以调整结构，后者要保持稳定可检索。

5. **每个文件头部都有职责说明**
   所有 Python 文件第一行是模块 docstring，所有 YAML/Markdown 头部有用途说明。
   这不是形式主义——半年后回来改代码时，这是你唯一不用重新读全文件就能定位的线索。

---

## 7. 相关文档

| 想知道 | 看 |
| --- | --- |
| 产品要解决什么问题 | [`PRD.md`](PRD.md) |
| 为什么选这些技术 | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| 接口怎么调 | [`API.md`](API.md) |
| 表结构 | [`DATA_MODEL.md`](DATA_MODEL.md) |
| 怎么评测、门槛是多少 | [`EVALUATION.md`](EVALUATION.md) |
| 从零到上线怎么走 | [`SOP.md`](SOP.md) |
| 报警了怎么办 | [`RUNBOOK.md`](RUNBOOK.md) |
| 要装什么、注册什么 | [`SETUP.md`](SETUP.md) |
| 部署到云服务器 | [`DEPLOY_CLOUD.md`](DEPLOY_CLOUD.md) |
| AI 助手的项目记忆 | [`knowledge/README.md`](knowledge/README.md) |
