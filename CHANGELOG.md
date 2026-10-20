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

### Security
- 危机场景改为纯规则短路路径，**不再经过任何模型调用**（零延迟、零成本、故障无关）
- 提示注入防护：用户输入用 `<用户输入>` 标签隔离，输出侧增加系统提示泄露检测
- 医疗越界双侧拦截：输入侧识别索要诊断意图，输出侧拦截诊断式结论与用药建议
- 观测数据脱敏：OTel Collector 主动移除 `llm.prompt` / `llm.completion` / `user.message` 等字段
- 依赖漏洞扫描（Trivy）与密钥泄露扫描（gitleaks）接入 CI

### Changed
- 评测指标汇总改为「无样本即不输出」，避免安全类用例拉低整体协议合规率
- 生产编排强制校验数据库/Redis/监控面板密码，密码与连接串保持一致

### Fixed
- 修复 `app.eval.runner` 缺失 `re` 导入导致抽取微行动段落时报 `NameError`
- 修复 Prompt 打包配置引用仓库外 README 导致 `pip install -e` 失败
- 修复 RRF 融合中排名为 0 时被误判为「未召回」的问题

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

[Unreleased]: https://github.com/your-org/lumina/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/your-org/lumina/releases/tag/v0.1.0
