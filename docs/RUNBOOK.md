# Lumina · 运维手册（Runbook）

> 面向"半夜被叫起来"的场景。每个故障场景给出：**症状 → 定位 → 处置 → 复盘要点**。

---

## 1. 服务拓扑

| 服务 | 端口 | 健康检查 | 依赖 |
| --- | --- | --- | --- |
| api | 8000 | `GET /healthz` | postgres, redis, llm |
| worker | — | ARQ 心跳（Redis） | postgres, redis |
| postgres | 5432 | `pg_isready` | 磁盘 |
| redis | 6379 | `redis-cli ping` | 内存 |
| elasticsearch / quickwit | 9200 / 6333 | `/_cluster/health` | 磁盘 |
| nginx | 80/443 | `curl -I` | api |
| vllm (可选) | 8000 | `/health` | GPU |
| embedding (可选) | 8000 | `/health` | CPU |
| prometheus / grafana | 9090 / 3000 | — | — |

---

## 2. 常用排查命令

```bash
# 服务状态
docker compose ps
docker compose logs -f --tail=200 api
docker compose logs --since=1h api | grep -i error

# 健康检查
curl -s localhost:8000/healthz | jq
curl -s localhost:8000/readyz | jq     # 含依赖探活

# 数据库
docker compose exec postgres psql -U lumina -d lumina -c "SELECT now(), count(*) FROM llm_calls;"
docker compose exec postgres psql -U lumina -d lumina -c "SELECT status, count(*) FROM llm_calls WHERE created_at > now() - interval '1 hour' GROUP BY 1;"

# 连接池
docker compose exec postgres psql -U lumina -d lumina -c "SELECT state, count(*) FROM pg_stat_activity GROUP BY 1;"

# 缓存
docker compose exec redis redis-cli --scan --pattern 'sem:*' | head
docker compose exec redis redis-cli info memory | grep used_memory_human

# 模型调用失败率
docker compose exec postgres psql -U lumina -d lumina -c \
  "SELECT model, status, count(*), avg(latency_ms) FROM llm_calls WHERE created_at > now() - interval '1 hour' GROUP BY 1,2 ORDER BY 3 DESC;"
```

---

## 3. 故障场景

### 3.1 对话无响应 / 大量 5xx

**症状**：监控面板 `chat_request` 错误率 > 5%

**定位顺序**：

1. API 自身是否健康：`curl /healthz`
2. 依赖是否健康：`curl /readyz`（会探活 postgres/redis/llm）
3. 模型链路：`llm_calls` 里 `status='error'` 是否激增
4. 降级是否触发：`status='fallback'` 数量

**常见原因与处置**：

| 原因 | 识别特征 | 处置 |
| --- | --- | --- |
| 云端模型 API 限流/故障 | `error_code=429` 激增 | 降级链自动生效；若全部失败，人工扩容：调低并发、把 `LLM_FALLBACK_MODELS` 换更稳的 |
| API Key 失效/欠费 | `401/403` | 立即更新 `.env` 的 `LLM_API_KEY` 并重启 |
| 数据库连接池耗尽 | `pg_stat_activity` 中大量 `idle in transaction` | 检查慢查询；`SELECT state, query FROM pg_stat_activity WHERE state <> 'idle'` |
| Redis 不可用 | `/readyz` 失败 | 降级：关闭语义缓存（`CACHE_ENABLED=false`），重启 redis |
| 磁盘满 | 容器报 no space | 清理 `docker system prune`；检查 WAL 归档 |

**兜底操作**：

```bash
# 熔断某个 provider，避免继续拖慢请求
curl -X POST localhost:8000/admin/llm/circuit/open -d '{"provider":"dashscope"}'
```

---

### 3.2 响应变慢

**症状**：TTFT p95 > 3s

**定位**：

```bash
# 按节点耗时
docker compose exec postgres psql -U lumina -d lumina -c \
  "SELECT trace_id, feature, latency_ms, ttft_ms, prompt_tokens, model
   FROM llm_calls WHERE created_at > now() - interval '10 minutes'
   ORDER BY ttft_ms DESC LIMIT 20;"
```

| 耗时来源 | 判别 | 处置 |
| --- | --- | --- |
| 检索慢 | `rag_inspect` 日志中 retrieve 节点 > 800ms | 检查 BM25 索引是否损坏；调大 `ef_search` 或减少 `candidate_k` |
| 上下文太长 | `prompt_tokens` 突增 | 检查是否有会话未压缩；确认 `summary` 任务在跑 |
| 模型排队 | `ttft_ms` 高但 `latency_ms - ttft_ms` 小 | 云端并发超限，降低 worker 并发 |
| 降级链频繁 | `status='fallback'` 多 | 主 provider 有问题，临时调优先级 |

---

### 3.3 成本异常上升

**症状**：日成本环比 > 50%

```bash
# 找出今天最贵的调用
docker compose exec postgres psql -U lumina -d lumina -c \
  "SELECT user_id, feature, count(*), sum(cost_cny) AS c, sum(prompt_tokens) AS pt
   FROM llm_calls WHERE created_at > now() - interval '1 day'
   GROUP BY 1,2 ORDER BY c DESC LIMIT 20;"
```

**排查**：

1. 是否有刷量/爬虫：看 `user_id` 分布是否集中
2. 缓存是否失效：`cached=true` 占比是否骤降
3. 是否有上下文泄漏：`prompt_tokens` 分布是否右移
4. 是否误用了大模型：看 `model` 分布

**应急**：

```bash
# 开启单用户日预算
export LLM_DAILY_BUDGET_CNY=200
# 关闭语义之外的复杂功能
export FEATURE_FLAG_REFLECTION=0
```

---

### 3.4 数据库连接数打满

**症状**：应用报 `too many connections`

```bash
docker compose exec postgres psql -U lumina -d lumina -c \
  "SELECT count(*), state FROM pg_stat_activity GROUP BY 2;"
```

**处置**：

```bash
# 临时提升上限
docker compose exec postgres psql -U lumina -d postgres -c "ALTER SYSTEM SET max_connections=400;"
docker compose restart postgres

# 找出长事务（常见元凶：忘了 commit 的事务）
docker compose exec postgres psql -U lumina -d lumina -c \
  "SELECT pid, now()-xact_start AS dur, left(query,80) FROM pg_stat_activity
   WHERE xact_start IS NOT NULL AND now()-xact_start > interval '1 min' ORDER BY dur DESC;"
```

**根因修复**：检查 `app/core/db/session.py` 的 session 生命周期，确保异常路径也释放连接。

---

### 3.5 知识库召回异常

**症状**：用户反馈答非所问，且 `rag.inspect` 显示召回内容不相关

```bash
# 检查索引一致性
python -m app.rag.ingest --validate-only --source packages/knowledge

# 对比 chunk 数量与文档数量
docker compose exec postgres psql -U lumina -d lumina -c \
  "SELECT (SELECT count(*) FROM kb_documents) AS docs,
          (SELECT count(*) FROM kb_chunks) AS chunks,
          (SELECT count(*) FROM kb_chunks WHERE embedding IS NULL) AS null_vec;"

# 检查 BM25 索引
curl -s "$ELASTICSEARCH_URL/lumina-kb/_count" | jq
```

**常见原因**：

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| chunk 数为 0 | ingest 未执行 | `make kb-ingest` |
| embedding 全为 NULL | 向量服务未启动 | 启动 embedding 服务后重跑 |
| BM25 与 pgvector 内容不一致 | 增量更新只更新了一侧 | 重建索引（`--rebuild`） |
| 召回内容明显过旧 | 知识库版本落后 | 补充知识并重建 |

**热修**：若召回服务不可用，系统会自动降级为纯向量（`SEARCH_BACKEND=none`）。

---

### 3.6 危机场景误放行（P0 事故）

**症状**：监控告警 `safety_trigger` 中出现 `model_used=true` 且 `type=crisis`

**立即处置**：

```bash
# 1. 关闭生成链路（只保留硬编码危机响应）
export FEATURE_FLAG_CHAT=0
docker compose up -d api worker

# 2. 核查影响范围
docker compose exec postgres psql -U lumina -d lumina -c \
  "SELECT id, user_id, created_at FROM llm_calls
   WHERE created_at > now() - interval '24 hours'
     AND feature='guard' AND status='ok' ORDER BY created_at DESC LIMIT 50;"

# 3. 定位代码
grep -rn "crisis" services/api/app/agents/ | head -20
```

**复盘要点**（写进 CHANGELOG 的 Incident 小节）：

- 触发路径是什么（意图漏判？关键词漏检？降级链绕过？）
- 评测集里有没有对应用例
- 补一条回归用例 + 一条自动检测规则
- 明确"什么情况下必须再次发布"

---

## 4. 发布与回滚

```bash
# 发布
bash ops/scripts/deploy.sh production --canary 10

# 查看当前版本
cat ops/.current-release

# 回滚到上一版本
bash ops/scripts/rollback.sh production

# 只回滚 Prompt / 功能开关（秒级，不需重新部署）
export FEATURE_FLAG_COACH_V4=0
docker compose up -d api
```

**回滚决策树**：

```
问题是否由本次发布引入？
├─ 是 → 立即回滚镜像
└─ 否 → 评估影响面 → 决定是否热修

回滚后必须做的：
1. 拉取本次发布的 trace 数据，保存在 issue 中
2. 在 staging 复现
3. 补回归用例（阻断同类问题再次发布）
4. 24h 内输出 incident 报告
```

---

## 5. 备份与恢复

```bash
# 全量备份
bash ops/scripts/backup.sh
# 生成：backup/lumina-$(date +%F).dump.gz

# 恢复
gunzip -c backup/lumina-2026-10-20.dump.gz | \
  docker compose exec -T postgres psql -U lumina -d lumina

# 仅恢复向量表
pg_restore --data-only -t kb_chunks ...
```

**恢复演练**：每季度做一次，**演练记录要留档**（这能直接说明是否具备灾备意识且真的演练过）。

**RPO / RTO 目标**：RPO ≤ 1 小时（WAL 归档）；RTO ≤ 2 小时。

---

## 6. 日常巡检清单

### 每日

- [ ] 错误率 < 1%
- [ ] p95 延迟 < 2.5s，TTFT < 1.5s
- [ ] 日成本在预算内
- [ ] P0/P1 BadCase 是否有新增
- [ ] 备份任务是否成功
- [ ] 磁盘使用率 < 80%

### 每周

- [ ] 依赖安全扫描
- [ ] 慢查询 Top 10 分析
- [ ] 知识库召回率抽检
- [ ] 崩溃率（小程序）趋势
- [ ] 评测全量跑一次并对比基线

### 每月

- [ ] 恢复演练
- [ ] 密钥轮换
- [ ] 成本模型复盘
- [ ] 容量规划

---

## 7. 告警规则

| 告警 | 条件 | 级别 | 处置 |
| --- | --- | --- | --- |
| 危机场景调用了 LLM | `safety_trigger.model_used=true` | P0 | §3.6 |
| 错误率突增 | 5xx 比例 > 5% 持续 5min | P1 | §3.1 |
| 延迟劣化 | p95 > 4s 持续 10min | P1 | §3.2 |
| 成本超标 | 日成本 > 预算 120% | P1 | §3.3 |
| 连接池饱和 | 使用率 > 90% 持续 5min | P1 | §3.4 |
| 检索坍塌 | `rag_inspect` 空结果率 > 20% | P1 | §3.5 |
| 磁盘告急 | 使用率 > 85% | P2 | 清理 |
| 备份失败 | 任务非零退出 | P1 | 检查 |

---

## 8. 应急预案联系人

| 角色 | 职责 | 渠道 |
| --- | --- | --- |
| 值班 | 一线处置 | 群内 @oncall |
| 负责人 | 决策与对外沟通 | 电话 |
| 模型/算法 | RAG 与模型问题 | 群内 |

> 单人项目则由同一个人承担全部角色，**但流程照走**——事故时"按手册做"比"临场发挥"可靠得多。
