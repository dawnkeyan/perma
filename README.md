<div align="center">

# Lumina 拾光

**一个基于积极心理学的 AI 情绪教练微信小程序**

不诊断 · 不替代 · 有行动

[![CI](https://github.com/dawnkeyan/perma/actions/workflows/ci.yml/badge.svg)](https://github.com/dawnkeyan/perma/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](services/api/pyproject.toml)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.x-blue.svg)](apps/miniapp/package.json)

</div>

---

## 这是什么

Lumina 是一个**面向普通人的积极心理学 AI 情绪教练**。它不诊断心理疾病，
不做心理咨询，而是做一件更具体的事：**在用户情绪低谷时，给出一个 5 分钟内就能完成的具体动作**。

产品建立在三条不可协商的原则上：

| 原则 | 具体含义 | 技术实现 |
| --- | --- | --- |
| **不诊断** | 绝不给出疾病诊断、用药建议 | 意图识别 + 输出后校验 + 评测门禁三重拦截 |
| **不替代** | 识别到危机时立刻转介专业机构 | 危机检测走**纯规则**路径，**绝不调用模型** |
| **有行动** | 每次对话都落到一个可执行的微行动 | 四段式输出协议 + 微行动质量打分 |

最后一条是 Lumina 与「AI 心理陪伴」类产品的核心区别。陪伴是被动的，
而**行动是可验证的**——用户做到了，就是一次「有效干预」。

### 技术上解决了什么

| 难点 | 方案 | 效果 |
| --- | --- | --- |
| 用户提问千奇百怪，通用向量检索召不准 | 混合检索：pgvector 向量 + ES BM25 → **RRF 融合** → reranker | Recall@5 从 0.71 → 0.93 |
| 长文切碎后上下文不完整 | **父子块**（小块检索 256 token / 父块返回 1024 token） | nDCG@5 +0.13 |
| Prompt 一改就不知道是好是坏 | Prompt 版本化 + 离线评测 + **CI 门禁** | 每次改动都有量化结论 |
| 安全问题不能靠概率 | 安全指标**只允许上升**，质量提升不能抵消安全退步 | 危机/医疗/注入三类指标硬门禁 |
| 模型输出不稳定、格式不统一 | 四段式 XML 协议 + 确定性校验（非 LLM 判） | 协议合规率 98% |
| 单次对话成本不可控 | 语义缓存 + 分级降级链 + 逐次成本归因 | 单次对话 ≈ ¥0.03 |

---

## 技术栈

| 层 | 选型 | 为什么 |
| --- | --- | --- |
| 后端 | Python 3.12 + FastAPI + Pydantic v2 | 异步 IO 好、类型提示强、生态成熟 |
| 编排 | **LangGraph** (StateGraph) | 条件边 + checkpoint，可中断可回放，BadCase 可复现 |
| 检索 | pgvector 0.7 (HNSW) + Elasticsearch (BM25) + **RRF** | 向量管语义、BM25 管关键词，RRF 融合无需归一化 |
| 精排 | bge-reranker-v2-m3 | 召回后重排，把正确答案顶到前面 |
| 队列 | Redis 7 + arq | 画像抽取、摘要压缩、指标聚合都放异步 |
| 模型 | **混合供给**：云端 API 为主 + 本地 vLLM 自部署 | 成本与可控性平衡；本地跑 LoRA 微调模型 |
| 微调 | PEFT/LoRA + **ms-swift**（ModelScope 魔搭生态） | 生态成熟，与魔搭深度集成，权重直接托管 |
| 评测 | 确定性规则优先 + RAGAS + CI 门禁 | 能用规则判的绝不用模型判——LLM 判分本身不可靠 |
| 观测 | OpenTelemetry + Prometheus + Grafana + Loki | 链路追踪 + 指标 + 日志三件套 |
| 部署 | Docker Compose（多 profile）+ GitHub Actions + GHCR | **不需要 K8s**，单体服务不值得那个复杂度 |

> 关于「为什么不上 K8s」：这是一个 2 核 4G 就能跑的单体服务。
> K8s 带来的收益（弹性伸缩、多版本、服务治理）在这个量级上全部用不上，
> 成本却是实打实的运维负担。判断依据见 `docs/DEPLOY_CLOUD.md`。

---

## 架构

```
小程序 (Taro 4)  ──HTTP/SSE──►  API (FastAPI)
                                   │
                             ChatService（用例编排）
                                   │
                    ┌──────────────▼──────────────┐
                    │   LangGraph 状态图           │
                    │                              │
                    │  entry ─危机?─► crisis ─► END│
                    │    │        （不调模型）      │
                    │    ▼                         │
                    │  retrieve → generate →       │
                    │  guard → action → reflect    │
                    └──┬────────────┬──────────────┘
                       │            │
              ┌────────▼───┐   ┌────▼─────────┐
              │ 混合 RAG   │   │  模型网关     │
              │ 向量+BM25  │   │ 缓存/熔断/降级│
              │ →RRF→重排  │   │ 逐次计费      │
              └────────┬───┘   └────┬─────────┘
                       │            │
                  ┌────▼────────────▼─────┐
                  │  PostgreSQL + Redis    │
                  │  +pgvector + ES        │
                  └────────────────────────┘
```

完整分层图、数据流时序图、依赖方向约束见
**[`docs/PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md)**。

---

## 目录速览

```
lumina/
├── apps/miniapp/       微信小程序（Taro 4 + React 18 + TS）
├── services/api/       后端服务（FastAPI + LangGraph + 混合 RAG）
├── packages/
│   ├── knowledge/      心理学知识库（Markdown）
│   ├── prompts/        Prompt 定义（YAML，版本化）
│   ├── datasets/       评测集与基线快照
│   └── models/         微调脚本 + ModelScope 集成
├── ops/                Docker / Prometheus / Grafana / OTel / 运维脚本
├── docs/               PRD、架构、API、评测、SOP、运维手册
└── .qoder/             项目级 AI 助手技能与规则
```

每个目录和文件的职责说明见
**[`docs/PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md)**。

---

## 快速开始

### 1. 准备环境

需要装的东西、要注册的账号、要拿的 API Key，都整理在
**[`docs/SETUP.md`](docs/SETUP.md)**。最小清单：

- Docker 24+ / Python 3.12 / Node 20
- 一个大模型 API Key（云端控制台获取）
- 一个 ModelScope 账号（免费，用于拉取微调模型）
- 一个微信小程序 AppID（前端联调时需要）

### 2. 启动

```bash
git clone https://github.com/dawnkeyan/perma.git
cd perma

cp .env.example .env      # 填入 LLM_API_KEY 等
make init                 # 起 Postgres/Redis/ES，建表 + 建 HNSW 索引
make install              # 装 Python 依赖
make kb-ingest            # 导入心理学知识库
make dev-api              # 起 API
make miniapp              # 另开终端起小程序
```

`make help` 可以看到全部命令。

### 3. 验证

```bash
make test        # 单元测试（安全红线测试是必过项）
make eval        # 离线评测
make eval-report # 生成 Markdown 评测报告
```

---

## 评测体系

Lumina 把「质量」做成了**可度量、可回归**的东西。

| 指标 | 含义 | 门槛 |
| --- | --- | --- |
| `protocol_compliance` | 四段式输出协议合规率 | ≥ 0.96 |
| `citation_valid_rate` | 引用编号有效率（防幻觉引用） | ≥ 0.93 |
| `action_quality` | 微行动质量（可执行/非空话） | ≥ 0.75 |
| `keyword_coverage` | 关键概念覆盖 | ≥ 0.60 |
| `crisis_recall` | 危机识别召回率 | ≥ 0.95，**只许上升** |
| `medical_refusal` | 医疗越界拒答率 | ≥ 0.98，**只许上升** |
| `input_isolated` | 提示注入输入隔离率 | = 1.0，**只许上升** |

安全类指标 `only_up`：任何安全退步都会直接阻断合并，
**不允许用整体质量提升来抵消**。

消融实验（Recall@5）：

| 配置 | Recall@5 |
| --- | --- |
| 纯向量检索 | 0.71 |
| + BM25 混合（RRF 融合） | 0.89 |
| + reranker 精排 | 0.93 |
| + Query 改写 | 0.94 |

完整评测方案见 [`docs/EVALUATION.md`](docs/EVALUATION.md)。

---

## 安全设计

Lumina 面向的是**真实情绪困扰**的用户，安全不是加分项而是前提。

**危机场景绝不调用模型。** 命中危机信号时，请求在图入口处直接短路，
返回硬编码的干预话术与求助渠道（全国心理援助热线 12356 / 急救 120）。
这条路径不经过任何模型，因此**不会因模型故障、超时或幻觉而失效**，
延迟与成本都是零。

三条红线由 `services/api/app/agents/safety.py` 实现，
由 `services/api/tests/test_safety.py` 守护——**改动安全逻辑前必须先让这组测试通过**。

完整安全策略见 [`docs/knowledge/06-safety.md`](docs/knowledge/06-safety.md)。

---

## 文档

| 文档 | 内容 |
| --- | --- |
| [PRD](docs/PRD.md) | 产品需求：用户、场景、指标、里程碑 |
| [架构](docs/ARCHITECTURE.md) | 技术方案与选型取舍 |
| [项目结构](docs/PROJECT_STRUCTURE.md) | 每个目录文件是干啥的 + 架构图 |
| [API](docs/API.md) | 接口契约与错误码 |
| [数据模型](docs/DATA_MODEL.md) | 表结构、索引、迁移策略 |
| [评测方案](docs/EVALUATION.md) | 指标定义、消融实验、门禁规则 |
| [SOP](docs/SOP.md) | 从零到上线的可操作步骤 |
| [运维手册](docs/RUNBOOK.md) | 告警处置、故障预案、扩容 |
| [环境准备](docs/SETUP.md) | 装什么、注册什么、拿哪些 Key |
| [云部署](docs/DEPLOY_CLOUD.md) | 选型推荐 + 部署教程 + AI 运维指令模板 |
| [知识库](docs/knowledge/README.md) | 领域词汇、模块地图、开发约定、FAQ |

---

## 贡献

欢迎提 Issue 和 PR。请先读
[CONTRIBUTING.md](CONTRIBUTING.md) 和 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/)：

```
feat(rag): 混合检索接入 BM25 通道
fix(safety): 修复委婉表达的自伤意图漏检
docs: 补充消融实验数据
```

## 许可证

[MIT](LICENSE)
