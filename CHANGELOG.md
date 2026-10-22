# 更新日志

本文件记录 Lumina 拾光的所有重要变更。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased] — 未发布

### Added
- 混合检索：pgvector 向量通道 + Elasticsearch BM25 通道，经 RRF（k=60）融合后交由 reranker 精排
- 知识库分块：标题层级切分 + 父子块（small-to-big），小块 256 token 检索 / 父块 1024 token 返回
- Prompt 版本化：YAML 定义 + 版本号 + changelog + 评测分数，可精确回滚到任意历史版本
- 离线评测体系：确定性规则优先，安全类指标 `only_up` 门禁，基线快照对比
- ModelScope（魔搭）全链路：模型拉取、BadCase 转 SFT 数据集、LoRA 微调、权重推送
- 异步任务（arq）：用户画像抽取、会话摘要滚动压缩、每日指标聚合、知识库增量向量化
- 可观测性：OpenTelemetry 采集与敏感字段脱敏、Prometheus 指标与告警规则、Grafana 总览看板
- 云部署方案：Docker Compose 多 profile 编排 + 5 个 AI 运维指令模板
- 工具目录（Tool Catalog）：服务端定义「心绪导航」等工具（引导流程 + 检索侧重 + 固定话术），
  `GET /v1/tools` 下发卡片文案，首页渲染工具卡片并可展开查看流程步骤
- 小程序首页（今天）、回顾（时间线）、我的三个页面，补齐了此前只在路由表里注册却未实现的页面

### Security
- 危机场景改为纯规则短路路径，**不再经过任何模型调用**（零延迟、零成本、故障无关）
- 提示注入防护：用户输入用 `<用户输入>` 标签隔离，输出侧增加系统提示泄露检测
- 注入响应改为硬编码话术，命中注入后**不再把原始输入交给模型**，避免“模型被说服”
- 医疗越界双侧拦截：输入侧识别索要诊断意图，输出侧拦截诊断式结论与用药建议
- 观测数据脱敏：OTel Collector 主动移除 `llm.prompt` / `llm.completion` / `user.message` 等字段
- 依赖漏洞扫描（Trivy）与密钥泄露扫描（gitleaks）接入 CI

### Changed
- 评测指标汇总改为「按指标分别统计」：有样本就统计、无样本就不出现，绝不补 0
- 评测门禁新增 `injection_detected` / `injection_not_followed` 两项安全指标
- 安全规则扩充：危机表达（了结自己/结束这一切/解脱）、医疗疾病名词表、英文提示注入
- 评测基线快照补齐安全类指标（crisis/medical/injection 三套件为确定性指标，本地重跑刷新）
- `arq` 的 `WorkerSettings` 改为继承 `arq.typing.WorkerSettingsBase`，配置项缺失由类型检查器兜底
- 仓库托管地址统一为 `github.com/dawnkeyan/perma`：替换 README 徽章、CHANGELOG 链接、
  SECURITY 报告入口、部署脚本与镜像名里的 `your-org` 占位符
- SOP 补充「SOP-0 · 代码托管与协作（GitHub）」：远程仓库配置、SSH 公钥注册、
  分支推送、PR 流程、CI 四个 job 说明与发布前自检清单

### Fixed
- 修复 RRF 融合时丢失另一通道排名（同一文档只在两路出现时不再残缺）
- 修复精排调用参数名写错（`top_k` → `top_n`）导致精排一直静默降级到 RRF
- 修复 `gateway.py` 使用 `ModelUnavailableError` 但未导入，全 provider 降级时抛 `NameError` 而非 503
- 修复 `aggregate()` 只输出固定 4 个指标，导致危机/医疗/注入类指标从汇总里消失
- 修复注入类评测用例读取 `expect` 却从不校验——“注入被识别但仍被照做”也能计满分
- 修复 `python -m app.worker` 启动路径 `await` 了一个非 awaitable 对象（arq 的 `run_worker` 是同步函数），启动必然抛 TypeError
- 修复 `KBChunk.metadata` 属性名与 SQLAlchemy `DeclarativeBase.metadata` 冲突（改为 `chunk_metadata`，数据库列名不变，无需迁移）
- 修复工具检索侧重为空时 `list(None)` 抛 TypeError
- 修复 SSE 的 `citation` 事件直接下发 Pydantic 模型、`sections` 按字典取值导致客户端拿不到内容
- 修复就绪探针里 Redis 与 HTTP 客户端共用同名变量，类型混乱且 Redis 连接未显式关闭
- 修复小程序 SSE 解析每次重新切分累计内容，导致同一帧被重复触发（流式回复内容重复）
- 修复小程序缺少 `babel.config.js`（TS 预设未启用）与 `@tarojs/webpack5-runner` 等构建依赖
- 修复 `app.eval.runner` 缺失 `re` 导入导致抽取微行动段落时报 `NameError`
- 修复 Prompt 打包配置引用仓库外 README 导致 `pip install -e` 失败
- 修复 RRF 融合中排名为 0 时被误判为「未召回」的问题
- 移除过时的 `types-redis` 桩包（停留在 4.6，缺 redis-py 5.x 的异步 `aclose`，会触发 mypy 假报错）
- 清理 ruff 34 项 + mypy 27 项（CI 后端门禁此前必挂）

---

## [0.1.0] — 2026-10-20

首个可运行版本。

### Added
- 后端服务骨架：FastAPI + SQLAlchemy 2.0 (async) + Alembic
- LangGraph 编排图：entry → retrieve → generate → guard → action → reflect
- 模型网关：OpenAI 兼容协议适配、重试、熔断、语义缓存、逐次成本归因
- 数据层：10 张业务表（用户、画像、日记、会话、消息、调用记录、知识库文档与块、BadCase、埋点）
- 微信小程序骨架：对话页、情绪日记页、登录态、埋点
- 部署编排：开发/生产两套 Docker Compose、Prometheus、Grafana、OTel Collector
- CI/CD：后端质量门禁、前端质量门禁、离线评测门禁、安全扫描四个 job

[Unreleased]: https://github.com/dawnkeyan/perma/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/dawnkeyan/perma/releases/tag/v0.1.0
