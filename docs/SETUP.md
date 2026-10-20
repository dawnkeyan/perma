# 环境准备与账号清单

> 目的：把「要装什么、要注册什么、要拿哪些 Key」一次列清楚，避免中途卡住。
> 目标读者：第一次接触本项目的人。

**阅读顺序建议**：先看 [§0 速查表](#0-速查表)，确定自己要做的是「本地开发」还是「完整部署」，再按需往下读。

---

## 0. 速查表

### 我要做本地开发（最小集）

| 类别 | 需要什么 | 必需 | 获取方式 | 费用 |
| --- | --- | --- | --- | --- |
| 运行时 | Docker Desktop / Colima | ✅ | 官网下载 | 免费 |
| 运行时 | Python 3.12 | ✅ | `brew install python@3.12` | 免费 |
| 运行时 | Node.js 20 | ✅ | `brew install node@20` | 免费 |
| 工具 | 微信开发者工具 | ✅ | 官网下载 | 免费 |
| 账号 | 微信小程序 AppID / AppSecret | ✅ | mp.weixin.qq.com 注册 | 免费（个人主体） |
| 账号 | ModelScope 魔搭 Token | ⭕ | modelscope.cn 注册 | 免费 |
| Key | 大模型 API Key | ✅ | 见 [§4.1](#41-大模型-api-key必填) | 按量，几元起 |
| Key | 嵌入模型 API Key | ⭕ | 同上，或本地跑 | 同上 |

### 我要部署到云服务器（完整集）

在上表基础上追加：

| 类别 | 需要什么 | 必需 | 说明 |
| --- | --- | --- | --- |
| 云主机 | ECS / CVM / 轻量服务器 | ✅ | 选型见 [DEPLOY_CLOUD.md](./DEPLOY_CLOUD.md) |
| 数据库 | 云数据库 PostgreSQL（可选） | ⭕ | 用云厂商 RDS 可省运维 |
| 对象存储 | OSS / COS（可选） | ⭕ | 存用户语音、日记附件 |
| 域名 + 备案 | ICP 备案 | ✅ | 中国大陆服务器**必须** |
| HTTPS 证书 | Let's Encrypt | ✅ | 脚本自动签发 |
| 监控告警 | 云监控 / Grafana Cloud | ⭕ | 自建 Grafana 也可 |
| 密钥管理 | 云厂商 KMS 或 Vault | ⭕ | 生产建议用 |

---

## 1. 需要安装的软件

### 1.1 Docker（必需）

**干什么用**：跑 PostgreSQL、Redis、Elasticsearch、Prometheus、Grafana 等中间件，以及打包部署后端服务。

| 系统 | 安装方式 |
| --- | --- |
| macOS | 装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)（含图形界面与镜像加速配置） |
| macOS（轻量） | `brew install colima docker && colima start --cpu 4 --memory 8` |
| Ubuntu | `sudo bash ops/scripts/bootstrap.sh`（自动装 Docker + Compose v2） |
| Windows | 装 Docker Desktop + WSL2 后端 |

验证：

```bash
docker --version
docker compose version
```

> 国内拉镜像慢：在 Docker Desktop → Settings → Docker Engine 里配 `registry-mirrors`。

### 1.2 Python 3.12（必需）

```bash
# macOS
brew install python@3.12
python3.12 --version

# Ubuntu
sudo apt install python3.12 python3.12-venv
```

项目还需要一个虚拟环境（**不要**污染系统 Python）：

```bash
cd services/api
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 1.3 Node.js 20（必需，仅小程序端需要）

```bash
brew install node@20     # macOS
nvm install 20           # 通用方案，推荐

node --version            # 应为 v20.x
```

小程序端依赖：

```bash
cd apps/miniapp
npm install
```

### 1.4 微信开发者工具（必需，仅小程序端需要）

下载：https://developers.weixin.qq.com/miniprogram/dev/devtools/download.html

配置要点：

- 导入目录选 `apps/miniapp/dist`
- AppID 填你在 mp.weixin.qq.com 申请的
- 开发设置里勾选「不校验合法域名」（仅开发阶段；生产必须配 HTTPS 域名）

### 1.5 可选但推荐

| 工具 | 用途 | 安装 |
| --- | --- | --- |
| `uv` | 更快的 Python 包管理 | `pip install uv` |
| `ruff` | 代码格式化与 lint | 已在 `.[dev]` 里 |
| `postgresql-client` | 查数据库 | `brew install libpq` |
| `redis-cli` | 查缓存 | `brew install redis` |
| `jq` | 处理 JSON | `brew install jq` |
| `direnv` | 自动加载 .env | `brew install direnv` |

---

## 2. 中间件逐个说明：要不要装、干什么用

> 全部可以用 Docker 起，**不需要手工安装**。下表说明的是「为什么需要它」和「什么情况下可以不要」。

| 服务 | 镜像 | 干什么用 | 能不要吗 | 生产环境建议 |
| --- | --- | --- | --- | --- |
| **PostgreSQL + pgvector** | `pgvector/pgvector:pg16` | 主数据库：用户、日记、对话记录；pgvector 扩展存知识库向量 | ❌ 不能 | 用云 RDS，或主备 + 自动备份 |
| **Redis** | `redis:7-alpine` | ① 语义/精确缓存，节省 LLM 调用成本 ② 限流计数 ③ 异步任务队列（ARQ）④ 会话存储 | ❌ 不能（成本会失控） | 云 Redis 或自建，**必须设密码** |
| **Elasticsearch** | `elastic/elasticsearch:8.13` | BM25 关键词检索，补足向量检索在专业术语上的短板 | ⭕ 可用 Quickwit 替代 | 内存占用大（≥4G），资源紧张用 Quickwit |
| **Quickwit** | `quickwit/quickwit` | 同上，但内存占用约为 ES 的 1/10，Rust 实现，ES 兼容 API | ⭕ 与 ES 二选一 | 资源紧张时首选 |
| **MinIO** | `minio/minio` | S3 兼容对象存储，存日记附件、语音、导出文件 | ⭕ 早期可不要 | 用云 OSS/COS 替代 |
| **Prometheus** | `prom/prometheus` | 采集指标（延迟、错误率、成本、缓存命中） | ⭕ | 必装，线上问题排查靠它 |
| **Grafana** | `grafana/grafana` | 可视化仪表盘 | ⭕ | 必装 |
| **OpenTelemetry Collector** | `otel/opentelemetry-collector` | 收集链路追踪（trace_id 串联前后端日志） | ⭕ | 建议装 |
| **TEI 嵌入服务** | `huggingface/text-embeddings-inference` | 本地跑嵌入模型（bge-m3），把向量计算放在自己服务器 | ⭕ | 数据敏感时用；否则用云 API 更省事 |
| **vLLM** | `vllm/vllm-openai` | 本地跑微调后的 LoRA 模型（OpenAI 兼容接口） | ⭕ | **需要 GPU**；无 GPU 可用 CPU 版但很慢 |
| **Nginx** | `nginx:1.25-alpine` | 反向代理、HTTPS 终止、限流 | ❌ 生产必需 | 必装 |

### 最小资源占用参考

| 场景 | CPU | 内存 | 磁盘 | GPU |
| --- | --- | --- | --- | --- |
| 本地开发（PG+Redis+API+小程序） | 4 核 | 8G | 20G | 不需要 |
| 本地开发 + ES + 观测栈 | 8 核 | 16G | 40G | 不需要 |
| 云服务器（生产，调用云端 LLM） | 2 核 | 4G | 40G | 不需要 |
| 云服务器（自部署 vLLM，7B 量化） | 4 核 | 16G | 60G | 1× 24G（如 RTX 4090 / A10） |

---

## 3. 需要注册的账号

### 3.1 微信小程序（必需）

| 项 | 说明 | 获取方式 |
| --- | --- | --- |
| 主体 | 个人 / 企业 / 个体工商户 | https://mp.weixin.qq.com 注册 |
| AppID | 小程序唯一标识 | 登录后台 → 开发管理 → 开发设置 |
| AppSecret | 调用微信服务端 API 的密钥 | 开发管理 → 开发设置 → 生成 |
| 备案 | 中国大陆必须 | 后台「备案」入口，审核 1~20 天 |

**个人主体 vs 企业主体**：

| 能力 | 个人主体 | 企业主体 |
| --- | --- | --- |
| 微信登录 | ✅ | ✅ |
| 内容安全审核 | ✅ | ✅ |
| 客服消息 | ❌ | ✅ |
| 认证（可选） | ❌ | ✅（300 元/年） |
| 备案 | ✅ | ✅ |

> **AI 生成内容**在微信小程序需要走「内容安全」接口审核（`msgSecCheck`），仓库里的 `app/api/v1/` 已预留位置，接入需在 `app/core/config.py` 配 `WECHAT_APPID` / `WECHAT_SECRET`。

### 3.2 ModelScope 魔搭社区（推荐，微调必备）

**它是干什么的**：国内最主流的模型社区，作用类似 HuggingFace，但**国内下载不用梯子**、提供**免费 GPU 算力**做微调实验、模型可以直接下载到自己的云服务器。

| 项 | 说明 | 获取方式 |
| --- | --- | --- |
| 账号 | 手机号注册 | https://modelscope.cn 注册 |
| Access Token | 拉取/推送模型的凭证 | 右上角头像 → 访问令牌 → 新建 Token |
| 模型仓库 | 存放微调后的 LoRA 权重 | 创建模型时选「LoRA」类型 |
| 创空间 Studio | 在线 Demo（可挂到产品介绍页） | 在模型页点「创空间」 |
| Notebook 算力 | 免费/低价 GPU 环境跑微调实验 | 魔搭 → 创空间 → Notebook |

**Token 用法**：

```bash
pip install modelscope
modelscope login --token $MODELSCOPE_TOKEN
# 之后 modelscope download / push 都会自动带上凭证
```

项目里已封装：

```bash
# 下载基座模型
python packages/models/scripts/download_model.py --model Qwen/Qwen2.5-1.5B-Instruct

# 推送微调产物到魔搭
python packages/models/scripts/push_to_modelscope.py --adapter lora_out --repo your-org/lumina-intent-lora
```

详见 [DEPLOY_CLOUD.md §4](./DEPLOY_CLOUD.md) 与 `packages/models/README.md`。

### 3.3 云厂商（部署必需）

| 厂商 | 推荐规格 | 优势 | 注册 |
| --- | --- | --- | --- |
| **阿里云** | 轻量应用服务器 2核4G | 备案一条龙、盘口便宜 | https://free.aliyun.com |
| **腾讯云** | 轻量应用服务器 2核4G | 同上，微信生态协同好 | https://cloud.tencent.com |
| **火山引擎** | 轻量 2核4G | 模型服务（豆包）同厂商，延迟低 | https://www.volcengine.com |

详细选型见 [DEPLOY_CLOUD.md §2](./DEPLOY_CLOUD.md)。

### 3.4 大模型 API（必需，二选一或多选）

| 服务商 | 变量 | 价格参考 | 获取 |
| --- | --- | --- | --- |
| **阿里云百炼（通义千问）** | `LLM_BASE_URL` + `LLM_API_KEY` | qwen-turbo ≈¥0.002/1K token；qwen-plus ≈¥0.008/1K token | https://bailian.console.aliyun.com |
| **DeepSeek** | 同上（OpenAI 兼容） | deepseek-chat ≈¥0.002/1K token | https://platform.deepseek.com |
| **智谱 GLM** | 同上 | glm-4-flash 免费/低价 | https://open.bigmodel.cn |
| **OpenAI / Azure** | 同上 | 较贵，国内访问需代理 | https://platform.openai.com |
| **本地 vLLM** | `LLM_LOCAL_BASE_URL` | 硬件成本，无调用费 | 自建（需 GPU） |

> 全部走 **OpenAI 兼容协议**，所以换厂商只改 `LLM_BASE_URL` 和 `LLM_API_KEY` 两个变量，代码零改动。

**建议**：至少申请两个厂商的 Key，用于**降级链**（主服务挂了自动切备用）。

### 3.5 GitHub（必需）

| 项 | 用途 |
| --- | --- |
| 账号 | 代码托管、CI/CD、Release |
| PAT (classic) | CI 推送到 GHCR，需要 `write:packages` 权限 |
| SSH Key | 部署到服务器的免密登录 |
| Actions Secrets | 存放 `EVAL_LLM_API_KEY`、`DEPLOY_SSH_KEY` 等 |
| Actions Variables | 存放 `EVAL_LLM_MODEL` 等非敏感值 |

### 3.6 可选账号

| 账号 | 用途 | 不需要它会怎样 |
| --- | --- | --- |
| Sentry | 前端错误与性能监控 | 用自建 Grafana + 埋点代替 |
| Grafana Cloud | 免费版 Grafana 托管 | 自建 Grafana |
| 阿里云 OSS / 腾讯云 COS | 对象存储 | 用 MinIO 代替 |
| 腾讯云短信 | 手机验证码登录 | 只用微信登录 |
| PostHog | 产品埋点分析 | 用自建埋点表代替 |

---

## 4. API Key 清单

> 全部写在 `.env`（已被 `.gitignore` 排除，**绝不要提交**）。模板见 `.env.example`。

### 4.1 大模型 API Key（必填）

```bash
# 主模型
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=sk-xxxxxxxxxxxxxxxx
LLM_MODEL=qwen-plus

# 降级链（主服务异常时按顺序尝试）
LLM_FALLBACK_MODELS=qwen-turbo,deepseek-chat
LLM_FALLBACK_BASE_URLS=https://dashscope.aliyuncs.com/compatible-mode/v1,https://api.deepseek.com/v1
LLM_FALLBACK_API_KEYS=sk-xxx,sk-yyy
```

### 4.2 嵌入模型 Key（可选，本地跑就留空）

```bash
# 用云端嵌入
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_API_KEY=sk-xxxxxxxx
EMBEDDING_MODEL=text-embedding-v3
EMBEDDING_DIM=1024

# 或用本地 TEI（推荐，数据不出内网）
EMBEDDING_BASE_URL=http://embedding:8000/v1
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DIM=1024
```

> ⚠️ **换了嵌入模型必须重建索引**：`make kb-ingest-recreate`。维度不一致会导致检索全乱。

### 4.3 重排模型 Key（可选）

```bash
RERANK_ENABLED=true
RERANK_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
RERANK_API_KEY=sk-xxxxxxxx
RERANK_MODEL=gte-rerank
```

### 4.4 数据库与缓存

```bash
POSTGRES_USER=lumina
POSTGRES_PASSWORD=<生成强密码>
POSTGRES_DB=lumina
DATABASE_URL=postgresql+psycopg://lumina:<密码>@postgres:5432/lumina

REDIS_URL=redis://redis:6379/0
# 生产启用密码时：
# REDIS_PASSWORD=<强密码>
# REDIS_URL=redis://:<强密码>@redis:6379/0
```

生成强密码：

```bash
openssl rand -base64 24
```

### 4.5 应用安全

```bash
# JWT 签名密钥，生产必须换掉默认值
JWT_SECRET=<openssl rand -hex 32>
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=10080     # 7 天
```

### 4.6 微信小程序

```bash
WECHAT_APPID=wx1234567890abcdef
WECHAT_SECRET=<小程序后台获取>
WECHAT_CODE_SESSION_URL=https://api.weixin.qq.com/sns/jscode2session
```

### 4.7 ModelScope

```bash
MODELSCOPE_TOKEN=<访问令牌页面获取>
MODELSCOPE_ENDPOINT=https://www.modelscope.cn/api/v1
MODELSCOPE_CACHE=~/.cache/modelscope
```

### 4.8 观测（可选）

```bash
OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
GRAFANA_PASSWORD=<强密码>
```

### 4.9 Key 清单速查表

| 变量 | 必需 | 来源 | 泄露后果 |
| --- | --- | --- | --- |
| `LLM_API_KEY` | ✅ | 云厂商控制台 | **直接产生费用** |
| `EMBEDDING_API_KEY` | ⭕ | 同上 | 同上 |
| `MODELSCOPE_TOKEN` | ⭕ | 魔搭访问令牌 | 可读写你的模型仓库 |
| `POSTGRES_PASSWORD` | ✅ | 自己生成 | 数据泄露 |
| `REDIS_PASSWORD` | 生产 ✅ | 自己生成 | 缓存/任务队列被篡改 |
| `JWT_SECRET` | ✅ | 自己生成 | **可伪造任意用户 token** |
| `WECHAT_SECRET` | ✅ | 小程序后台 | 可冒用小程序调用微信接口 |
| `DEPLOY_SSH_KEY` | 部署 ✅ | 自己生成 | **服务器沦陷** |
| `GITHUB_TOKEN` | CI ✅ | GitHub 设置 | 可推包/改代码 |

---

## 5. 一条龙安装流程

### 5.1 本地开发

```bash
# 1. 装软件（macOS 示例）
brew install python@3.12 node@20 jq
brew install --cask docker

# 2. 拉代码
git clone <your-repo-url> perma && cd perma

# 3. 配置环境
cp .env.example .env
vim .env          # 至少填 LLM_API_KEY、JWT_SECRET、POSTGRES_PASSWORD

# 4. 起依赖 + 建表
make init

# 5. 装后端依赖
make bootstrap

# 6. 导入知识库
make kb-ingest

# 7. 起后端
make dev-api

# 8. 起小程序（另开终端）
cd apps/miniapp && npm install && npm run dev:weapp
```

### 5.2 部署到云服务器

```bash
# 1. 在服务器上执行初始化（装 Docker、建用户、配防火墙）
sudo ./ops/scripts/bootstrap.sh

# 2. 克隆仓库
sudo -u deploy git clone <your-repo-url> /opt/lumina

# 3. 下发 .env（从密钥库取，不要在 git 里）
cd /opt/lumina && cp .env.example .env && vim .env

# 4. 部署
cd /opt/lumina && bash ops/scripts/deploy.sh production

# 5. 验证
bash ops/scripts/smoke.sh production
```

完整教程见 [DEPLOY_CLOUD.md](./DEPLOY_CLOUD.md)。

---

## 6. 成本估算

### 6.1 月度固定成本（最小可用配置）

| 项 | 规格 | 月费用（估） |
| --- | --- | --- |
| 云服务器 | 2核4G 轻量 | ¥30 ~ ¥70 |
| 域名 | .com 首年 | ¥60 ~ ¥80 |
| SSL 证书 | Let's Encrypt | ¥0 |
| 对象存储 | 按量 | ¥0 ~ ¥5 |
| **合计** | | **约 ¥100 ~ ¥160 / 月** |

### 6.2 LLM 调用成本

单次对话的 token 消耗（典型值）：

| 项 | token |
| --- | --- |
| System Prompt | ~800 |
| 历史对话（6 轮） | ~1200 |
| 检索上下文（5 块） | ~1500 |
| 用户输入 | ~100 |
| **输入合计** | **~3600** |
| 输出（四段式） | ~400 |

按 qwen-plus（¥0.008/1K token）估算：

```
单次成本 ≈ 3.6 × 0.008 + 0.4 × 0.008 ≈ ¥0.032
```

对应 PRD 里的成本目标 **≤ ¥0.035/次对话**。

**成本优化手段**（按收益排序）：

| 手段 | 预期降幅 | 实现位置 |
| --- | --- | --- |
| 语义缓存 | 20% ~ 35% | `app/llm/gateway.py` |
| 主模型降级到 turbo | 60% ~ 75% | `LLM_MODEL` + 降级链 |
| 减少历史轮数（6→4） | 10% ~ 15% | `app/services/chat_service.py` |
| 减少检索块数（5→3） | 8% ~ 12% | `app/rag/context.py` |
| 微调小模型做意图识别 | 意图环节近乎归零 | `packages/models/` |

### 6.3 评测成本

跑一次全量评测（36 条用例，含 Judge 二次调用）约 **¥0.5 ~ ¥2**。
本地迭代建议用 turbo 级模型，发布前用生产模型跑一次。

---

## 7. 常见安装问题

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| `docker compose up` 拉镜像超时 | 国内网络 | 配 registry-mirrors |
| `pgvector` 镜像找不到向量扩展 | 用了官方 postgres 镜像 | 必须用 `pgvector/pgvector:pg16` |
| 端口 5432/6379 被占 | 本机装了服务 | 改 `.env` 里 `POSTGRES_PORT` / `REDIS_PORT` |
| `npm install` 报 `ERESOLVE` | npm 版本冲突 | 删 `package-lock.json` 重装，或用 `npm install --legacy-peer-deps` |
| 小程序真机预览白屏 | 域名未配置 | 开发阶段勾选「不校验合法域名」 |
| `pip install` 编译报错 | 缺 C 编译器 | `brew install build-essential` |
| 内存不足导致容器被杀 | Docker 内存设置太小 | Docker Desktop 调至 8G+ |
| `modelscope download` 卡住 | 网络 | 换镜像源或用 `MODELSCOPE_ENDPOINT` 指向可用节点 |

---

## 8. 检查清单

**本地开发开工前**：

- [ ] Docker 可用，`docker compose version` 有输出
- [ ] Python 3.12，虚拟环境已激活
- [ ] Node 20
- [ ] 微信开发者工具已装、AppID 已申请
- [ ] `.env` 已创建，`LLM_API_KEY` 已填
- [ ] `make init` 成功
- [ ] `make kb-ingest` 成功（`kb_chunks` 有数据）
- [ ] `make dev-api` 起来，`/healthz` 返回 200
- [ ] 小程序能对话

**上云前**：

- [ ] 云服务器已购买，SSH 可达
- [ ] 域名已解析，ICP 备案通过
- [ ] HTTPS 证书已签发
- [ ] `.env` 里所有生产密码已替换默认值
- [ ] `JWT_SECRET` 已换成随机值
- [ ] Redis 已设密码
- [ ] `bootstrap.sh` 执行成功
- [ ] `deploy.sh production` 成功
- [ ] `smoke.sh` 全部通过（含危机场景断言）
- [ ] 备份任务已配置（`ops/scripts/backup.sh` + crontab）
- [ ] 监控告警已接入（至少 `CrisisPathCalledLLM`）
