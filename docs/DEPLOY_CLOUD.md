# 云服务器部署指南

> 目的：把「选哪台机器、怎么装、怎么发、怎么监控」写成可逐条执行的教程。
> 面向：第一次把 LLM 应用部署上云的人。
> 配套文档：[SETUP.md](./SETUP.md)（装什么、注册什么）、[RUNBOOK.md](./RUNBOOK.md)（出事了怎么办）、[SOP.md](./SOP.md)（开发流程）。

---

## 1. 选型推荐

### 1.1 结论先行

**推荐方案：阿里云或腾讯云的「轻量应用服务器」2核4G + 独立数据盘，跑 Docker Compose。**

理由：

| 维度 | 轻量应用服务器 | ECS/CVM 标准型 | K8s 集群 |
| --- | --- | --- | --- |
| 起步成本 | ¥24~¥70/月 | ¥100+/月 | ¥300+/月 + 运维成本 |
| 运维复杂度 | 低（单台 SSH） | 中 | 高（要懂 k8s） |
| 本项目是否需要 K8s | ❌ 不需要 | ❌ 不需要 | ❌ 过度设计 |
| 扩容能力 | 一般 | 好 | 很好 |
| 适合阶段 | MVP → 小规模 | 规模化后 | 大团队/多服务 |

**本项目是模块化单体，不是微服务，不需要 K8s。** 用 K8s 只会增加复杂度，不会带来收益——
这是架构评审里常被追问的判断题，答「不需要 K8s」比硬上 K8s 更能体现工程判断力。

### 1.2 云厂商对比

| 厂商 | 产品 | 2核4G 参考价 | 优势 | 不足 |
| --- | --- | --- | --- | --- |
| **阿里云** | 轻量应用服务器 | ¥34~¥68/月 | 备案一条龙、盘口便宜、文档全 | 活动价波动 |
| **腾讯云** | 轻量应用服务器 | ¥38~¥76/月 | 微信生态协同、镜像丰富 | 同上 |
| **火山引擎** | 轻量云服务器 | ¥30~¥60/月 | 模型服务同厂商、延迟低 | 生态不如前两家 |
| **华为云** | Flexus | ¥40~¥80/月 | 政企合规 | 活动少 |

> 选哪家影响不大，**重要的是能不能备案**。中国大陆服务器对外提供服务必须 ICP 备案。

### 1.3 配置建议

| 阶段 | 配置 | 说明 |
| --- | --- | --- |
| **开发验证** | 2核2G ¥30/月 | 够跑通链路，日活 < 50 |
| **MVP 上线** | **2核4G ¥60/月** | **推荐**。跑 PG+Redis+API+worker+Nginx |
| **小规模运营** | 4核8G ¥150/月 | 加 ES、Prometheus、Grafana |
| **自部署 vLLM** | 4核16G + 1×GPU 24G ¥800+/月 | GPU 实例单独买，不与主服务同机 |

**磁盘**：系统盘 60G SSD + 数据盘 100G（数据库、模型权重、日志）。
模型权重不要放系统盘——系统盘通常只读且容量小。

### 1.4 架构：云上部署拓扑

```
                        ┌───────────────────────────────┐
   微信小程序           │        云服务器（2核4G）        │
   （用户）  ────────►  │                               │
                        │  ┌─────────┐   ┌────────────┐  │
                        │  │ Nginx   │──►│ API (uvicorn)│  │
                        │  │ :443    │   │  :8000      │  │
                        │  │ 限流/TLS │   └──────┬─────┘  │
                        │  └─────────┘          │        │
                        │                      │        │
                        │  ┌─────────┐   ┌──────▼─────┐  │
                        │  │  Redis  │   │   Worker   │  │
                        │  │  缓存/队列│   │  异步任务   │  │
                        │  └─────────┘   └──────┬─────┘  │
                        │                      │        │
                        │  ┌────────────────────▼─────┐  │
                        │  │  PostgreSQL + pgvector   │  │
                        │  │  业务数据 + 向量索引      │  │
                        │  └──────────────────────────┘  │
                        │                               │
                        │  ┌──────────────────────────┐  │
                        │  │ Prometheus / Grafana     │  │  ← 观测栈（可选 profile）
                        │  └──────────────────────────┘  │
                        └───────────────┬───────────────┘
                                        │ 出站 HTTPS
                        ┌───────────────▼───────────────┐
                        │  云端 LLM API                  │
                        │  通义千问 / DeepSeek / 智谱     │
                        └───────────────────────────────┘

   可选：同机或异机部署 vLLM（需 GPU）
                        ┌───────────────────────────────┐
                        │  vLLM :8000  ← LoRA 微调模型   │
                        │  （从 ModelScope 魔搭拉权重）  │
                        └───────────────────────────────┘
```

**关键点**：

- 只有 Nginx 的 80/443 对公网开放，其余服务**只走 Docker 内网**。
- 云端 LLM API 走**出站 HTTPS**，需要在安全组放行出站 443。
- 向量库和主库同实例（pgvector），MVP 阶段省一套运维。

---

## 2. 前期准备清单

### 2.1 必须完成

| # | 事项 | 在哪做 | 耗时 |
| --- | --- | --- | --- |
| 1 | 买云服务器 | 阿里云/腾讯云控制台 | 5 分钟 |
| 2 | 申请域名 | 阿里云万网/腾讯云 | 10 分钟 |
| 3 | **ICP 备案** | 云厂商备案系统 | **7~20 天** ⚠️ |
| 4 | 解析域名到服务器 IP | DNS 控制台 | 5 分钟 |
| 5 | 配置安全组（只开 22/80/443） | 云控制台 | 5 分钟 |
| 6 | 申请 API Key | 百炼/DeepSeek 控制台 | 10 分钟 |
| 7 | 申请魔搭 Token | modelscope.cn | 5 分钟 |
| 8 | 准备 `.env` | 密钥管理工具 | 15 分钟 |

> ⚠️ **备案是最大的时间黑洞**。如果急着上线，可以先用香港/海外节点跑通，
> 但国内用户体验与小程序合规性会受影响。小程序正式发布也要求域名已备案。

### 2.2 安全组配置

| 方向 | 端口 | 来源 | 说明 |
| --- | --- | --- | --- |
| 入站 | 22 | **你的固定 IP** 或 0.0.0.0/0 | SSH，限制来源更安全 |
| 入站 | 80 | 0.0.0.0/0 | HTTP（跳转到 HTTPS + 证书校验） |
| 入站 | 443 | 0.0.0.0/0 | HTTPS |
| 入站 | 3000 | **仅运维 IP** | Grafana，不要公网暴露 |
| 出站 | 443 | 0.0.0.0/0 | 调用云端 LLM API |
| 出站 | 80 | 0.0.0.0/0 | 软件源、镜像拉取 |

> ❌ **不要**开放 5432（PostgreSQL）、6379（Redis）、8000（API）。
> 这些服务只在 Docker 内网可达。

### 2.3 登录方式

```bash
# 用密钥登录（推荐）
ssh-keygen -t ed25519 -C "lumina-deploy"
cat ~/.ssh/id_ed25519.pub   # 粘贴到云控制台「SSH 密钥对」

ssh -i ~/.ssh/id_ed25519 root@<你的服务器IP>
```

创建部署用户（`bootstrap.sh` 会自动做）：

```bash
useradd -m -s /bin/bash deploy
usermod -aG docker deploy
mkdir -p /opt/lumina && chown deploy:deploy /opt/lumina
```

---

## 3. 首次部署（完整流程）

### 3.1 第 0 步：初始化服务器

```bash
# 用 root 或 sudo 执行
sudo ./ops/scripts/bootstrap.sh
```

这个脚本会做：装 Docker + Compose v2、创建 swap、调内核参数、建 `deploy` 用户、建目录、配防火墙、配置日志轮转、配置免密 SSH。

### 3.2 第 1 步：拉代码

```bash
sudo -u deploy git clone https://github.com/dawnkeyan/perma.git /opt/lumina
cd /opt/lumina
```

### 3.3 第 2 步：配置环境变量

```bash
cd /opt/lumina
cp .env.example .env
```

**至少要改这些**（生产环境默认值全部要换掉）：

```bash
# 用命令生成强密码
openssl rand -hex 32

vim .env
```

```bash
# ---- 必须修改 ----
POSTGRES_PASSWORD=<强密码>
JWT_SECRET=<openssl rand -hex 32 的结果>
REDIS_PASSWORD=<强密码>
REDIS_URL=redis://:<同一个密码>@redis:6379/0
GRAFANA_PASSWORD=<强密码>

# ---- 填入你的 Key ----
LLM_API_KEY=sk-你的百炼Key
MODELSCOPE_TOKEN=你的魔搭Token

# ---- 生产环境标记 ----
APP_ENV=production
LOG_FORMAT=json
TRUSTED_HOSTS=api.你的域名.com
```

> 💡 **.env 的管理**：不要提交到 Git（已被 `.gitignore` 排除）。
> 生产环境建议放在云厂商的 KMS / 密钥管理，或用 `ansible-vault` / `sops` 加密后入库。

### 3.4 第 3 步：申请 HTTPS 证书

用 `certbot`（Let's Encrypt 免费，90 天自动续期）：

```bash
# 装 certbot
sudo apt install -y certbot certbot-nginx

# 申请证书（需要 80 端口已放行、域名已解析到本机）
sudo certbot --nginx -d api.你的域名.com -d 你的域名.com

# 验证自动续期
sudo certbot renew --dry-run
```

证书会落在 `/etc/letsencrypt/live/<域名>/`。把 Nginx 配置里的证书路径指过去，
并加一条 crontab：

```bash
# 每天 3 点检查续期
0 3 * * * /usr/bin/certbot renew --quiet && systemctl reload nginx
```

### 3.5 第 4 步：执行部署

```bash
cd /opt/lumina
sudo -u deploy bash ops/scripts/deploy.sh production
```

`deploy.sh` 会自动完成：

```
拉代码 → 迁移前备份数据库 → 构建镜像 → 执行迁移 → 启动容器
      → 健康检查 → 冒烟测试 → 失败自动回滚
```

**看到这行就是成功**：

```
[12:00:00] 部署完成：production
```

### 3.6 第 5 步：验证

```bash
# 冒烟测试（包含危机场景断言）
cd /opt/lumina && bash ops/scripts/smoke.sh production

# 手动检查
curl -s http://127.0.0.1:8000/healthz
curl -s https://api.你的域名.com/health | jq

# 看日志
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f api
```

### 3.7 第 6 步：接入监控

```bash
cd /opt/lumina
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --profile observability up -d
```

- Prometheus：`http://<服务器IP>:9090`（建议用 SSH 隧道访问，不要公网暴露）
- Grafana：`http://<服务器IP>:3000`，账号 `admin`，密码为 `.env` 里的 `GRAFANA_PASSWORD`
- 仪表盘已预置：`ops/grafana/dashboards/lumina-overview.json`

**必须验证的告警**（在 Prometheus 的 Alerts 页面应能看到）：

| 告警 | 级别 | 含义 |
| --- | --- | --- |
| `CrisisPathCalledLLM` | **P0** | 危机路径调了 LLM —— 安全事故 |
| `MedicalOverreachRate` | P1 | 医疗越界率超标 |
| `DailyCostBudgetExceeded` | P1 | 日成本超预算 |
| `RetrievalEmptyRate` | P1 | 检索空结果率异常 |
| `LLMLatencyHigh` | P2 | 响应变慢 |

**告警通知**：在 Prometheus 里配置 Alertmanager，或直接用云厂商的监控告警对接
（阿里云 Prometheus 是托管的，可以直接配钉钉/飞书/短信）。

### 3.8 第 7 步：配置自动备份

```bash
# 每天 4 点备份，保留 14 天
crontab -e
```

```cron
0 4 * * * cd /opt/lumina && /bin/bash ops/scripts/backup.sh >> /var/log/lumina-backup.log 2>&1
```

**定期验证备份可恢复**（没验证过的备份等于没有备份）：

```bash
# 每月抽一次，在测试机上恢复
gunzip -c ops/data/backups/lumina-xxxx.sql.gz | psql -h <test机> -U lumina -d lumina_test
```

---

## 4. 日常发布流程

### 4.1 触发条件

| 情况 | 动作 |
| --- | --- |
| 合并到 `main` 的 PR | 自动构建镜像 + 部署到 staging |
| 打 tag `v*` | 自动构建 + 部署到 production |
| 手动触发 | GitHub Actions → Release → Run workflow |

### 4.2 手动发布

```bash
# 在服务器上
cd /opt/lumina
DEPLOY_TAG=v1.0.0 bash ops/scripts/deploy.sh production
```

### 4.3 回滚

```bash
# 代码回滚（迁移向后兼容，通常不用回滚数据库）
cd /opt/lumina
./ops/scripts/rollback.sh --to v1.0.0

# 连数据库一起回滚（破坏性迁移时需要）
./ops/scripts/rollback.sh --to v1.0.0 --db ops/data/backups/lumina-20261020-040000.sql.gz
```

### 4.4 灰度（可选）

单机部署做灰度的方式是**双机 + Nginx 上游切换**：

```bash
# 1. 起第二台（new-release）
docker compose up -d

# 2. 在 Nginx 里把 upstream 切到新机
upstream lumina_api {
    server 10.0.0.2:8000;  # 旧
    # server 10.0.0.3:8000;  # 新（取消注释即切换）
}
nginx -s reload

# 3. 观察 10 分钟，异常就切回
```

---

## 5. 用 AI 助手完成部署

> 你提到「云服务器相关服务的部署也用 AI 操作」。下面是**可直接复制给 AI 助手**的指令模板。
> 前提：本地已经 clone 仓库，且 AI 助手有终端权限。

### 5.1 首次部署指令模板

````text
我有一个 Python/FastAPI 项目需要部署到云服务器，请按以下步骤操作，
每一步执行前先说明将要做什么，等我确认后再执行。

项目路径：/opt/lumina
目标环境：production
服务器信息：<IP>，SSH 用户 deploy，密钥在 ~/.ssh/id_ed25519

步骤：
1. 检查服务器系统版本、CPU/内存/磁盘、已开放的端口
2. 检查 Docker 与 Docker Compose 是否安装，版本是否满足要求
3. 执行 ops/scripts/bootstrap.sh 完成初始化（需要 root 权限，用 sudo）
4. 确认 /opt/lumina/.env 存在，且所有生产必需的密码项都不是默认值
   —— 只检查，不打印密码值，只报告"已设置/未设置"
5. 执行 ops/scripts/deploy.sh production
6. 执行 ops/scripts/smoke.sh production，把每一项的通过/失败结果列出来
7. 汇总：部署是否成功、容器状态、访问地址、后续需要我手动做的事项

约束：
- 任何会删除数据的操作（rm、drop、truncate、down -v）执行前必须先问我
- 不要修改 .env 里我没提到的配置项
- 如果某一步失败，停下来把错误原文给我，不要自己猜测原因继续往下做
````

### 5.2 故障排查指令模板

````text
线上服务出现故障：<现象描述>
请按以下顺序排查，每一步都告诉我你的判断依据：

1. docker compose ps 看容器状态，列出所有不健康的容器
2. docker compose logs --tail=200 api 看最近 200 行日志，找出异常堆栈
3. curl http://127.0.0.1:8000/healthz 和 /health，确认依赖健康状况
4. 检查数据库连接、Redis 连接是否正常
5. 检查容器资源占用（docker stats）和宿主机内存/磁盘
6. 检查最近的部署记录（git log、docker image 创建时间）

最后给我：
- 最可能的根因（按可能性排序）
- 建议的处置动作（区分「立即执行」和「需要我确认」）
- 根因确认后应该补什么监控或测试来防止复发

约束：
- 只读操作可以直接执行
- 任何写操作、尤其是重启容器或回滚，先问我
````

### 5.3 加监控/告警指令模板

````text
我需要为这个服务增加监控，现状是：
- 已有 Prometheus（/etc/prometheus/prometheus.yml）和 Grafana
- Grafana 预置仪表盘在 ops/grafana/dashboards/

请：
1. 读现有的 ops/prometheus/alerts.yml，列出已覆盖的告警和它们的触发条件
2. 指出这个 LLM 应用**还缺哪些关键告警**，按重要性排序
   （重点考虑：成本、延迟、错误率、检索质量、安全红线）
3. 为每个缺失项写出具体的 PromQL 表达式
4. 直接把新增规则写进 alerts.yml，遵循现有文件的格式与命名风格
5. 检查 Prometheus 配置语法是否正确（promtool check config）

不要改动已有的告警规则。
````

### 5.4 成本优化指令模板

````text
这个 LLM 应用的调用成本偏高，请分析并提出优化方案。

请：
1. 查数据库里的 llm_calls 表（或等价的调用记录表），统计：
   - 过去 7 天每天的总 token 数与总成本
   - 按模型分组的成本占比
   - 按调用类型（chat / embedding / rerank / intent）分组的成本占比
   - 缓存命中率
2. 找出成本最高的 3 类请求，并给出每类的单次平均成本
3. 结合代码（app/llm/gateway.py 的缓存实现、app/rag/context.py 的上下文组装、
   app/services/chat_service.py 的历史长度）提出具体优化点
4. 每个优化点给出：预期节省多少、风险是什么、怎么验证效果

先出方案，我确认后再改代码。
````

### 5.5 备份恢复演练指令模板

````text
我需要做一次备份恢复演练，验证备份真的能恢复。

请：
1. 列出 ops/data/backups/ 下所有备份文件及其大小、日期
2. 检查备份任务（crontab）是否正常配置、最近一次备份是否成功
3. 在**临时数据库**上恢复最近一次备份并验证数据完整性
   （不要碰生产库！用 docker 起一个临时 postgres 容器）
4. 恢复后统计关键表的行数，与生产库对比
5. 输出演练报告：备份是否可用、恢复耗时、有无问题

约束：
- 严禁对生产数据库执行任何写操作
- 临时容器用完要清理
````

---

## 6. 成本优化清单

| 手段 | 做法 | 预期节省 | 风险 |
| --- | --- | --- | --- |
| **降级链分层** | 日常用 `qwen-turbo`，复杂场景才用 `qwen-plus` | 60~75% | 质量下降，需评测验证 |
| **语义缓存** | 已在网关实现，调阈值 | 20~35% | 可能返回相似但不完全合适的答案 |
| **减少历史轮数** | 6 轮 → 4 轮 | 10~15% | 上下文变浅，个性感下降 |
| **减少检索块数** | 5 块 → 3 块 | 8~12% | 忠实度下降 |
| **自部署小模型做意图识别** | LoRA 微调 1.5B | 意图环节归零 | 需要 GPU 投入 |
| **提示词缓存** | 云厂商的 prompt cache | 20~40% | 依赖厂商支持 |
| **异步化非关键路径** | 反思、埋点走 worker | 不省钱但降延迟 | 无 |

**监控成本的动作**：

```bash
# 每日成本
curl -s http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=sum(increase(lumina_llm_cost_cny_total[1d]))'
```

超预算时的处置顺序：先查缓存命中率 → 再查上下文长度 → 再调模型档位 → 最后才考虑限流。

---

## 7. 常见问题

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| 域名打不开 | 安全组没放行 80/443 | 云控制台加安全组规则 |
| HTTPS 证书报错 | 域名未备案 / DNS 未生效 | 备案 + 等 DNS 生效（最长 24h） |
| 小程序请求被拒 | 域名未在小程序后台配置 | 后台 → 开发管理 → 服务器域名，加 HTTPS 域名 |
| 容器起不来 | 内存不足 / 端口冲突 | `docker stats` 看占用；`dmesg` 看 OOM |
| 拉镜像超时 | 服务器在境外 / 网络受限 | 配 registry-mirrors 或改用 GHCR |
| 数据库连不上 | 密码错 / 未设密码 | 核对 `.env` 里 `DATABASE_URL` 与 `POSTGRES_PASSWORD` 一致 |
| 首次部署卡在迁移 | 迁移脚本缺 Alembic 配置 | 检查 `alembic.ini` 与 `migrations/` 是否随镜像复制 |
| 磁盘满 | 日志 / 镜像 / 备份堆积 | `docker system prune`；配置日志轮转；检查备份保留策略 |
| 时区不对 | 容器默认 UTC | compose 里已设 `TZ=Asia/Shanghai` |
| SSE 流式不生效 | Nginx 缓冲 | 确认 `proxy_buffering off` |

---

## 8. 部署完成检查清单

- [ ] `docker compose ps` 全部 healthy
- [ ] `smoke.sh production` 全部通过（含危机场景断言）
- [ ] `https://<域名>/healthz` 返回 200
- [ ] 小程序能正常对话
- [ ] `.env` 里所有密码已替换默认值
- [ ] `JWT_SECRET` 已是随机值
- [ ] Redis 已设密码且 `REDIS_URL` 带凭证
- [ ] 只有 22/80/443 对公网开放
- [ ] Grafana 密码已改
- [ ] 备份 crontab 已配置
- [ ] 备份**已验证可恢复**
- [ ] 证书自动续期已配置并验证
- [ ] Prometheus 告警已接入通知渠道
- [ ] `CrisisPathCalledLLM` 告警已确认可达
- [ ] 云监控已配置基础告警（CPU/内存/磁盘/带宽）
- [ ] 日志轮转已生效（`docker inspect --format='{{.HostConfig.LogConfig}}' lumina-api`）
