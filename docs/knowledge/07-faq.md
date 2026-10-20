# 07 · FAQ 与故障速查

> 目的：把重复出现的问题固化成答案，把排查动作固化成命令。
> 最后更新：2026-10-20

## 一、环境搭建

### Q1：第一次跑起来该做什么？

```bash
cp .env.example .env        # 填 LLM_API_KEY 等敏感项
make init                   # 起 postgres + redis，建表
make kb-ingest              # 导入心理学知识库
make dev-api                # 起 API
```

小程序端：

```bash
cd apps/miniapp && npm install && npm run dev:weapp
```

微信开发者工具导入 `apps/miniapp/dist`，在 `config/index.ts` 里配置 AppID。

### Q2：`make init` 报 `env file .env not found`

`.env` 未创建。先 `cp .env.example .env`。

### Q3：Docker 拉镜像很慢

配置镜像加速：

```bash
cat > /etc/docker/daemon.json <<'EOF'
{ "registry-mirrors": ["https://<你的镜像源>"] }
EOF
sudo systemctl restart docker
```

### Q4：Python 版本不对

需要 3.12+：

```bash
python3 --version
make bootstrap   # 创建 .venv 并安装依赖
```

## 二、后端开发

### Q5：`pip install -e ".[dev]"` 报依赖冲突

项目用 `uv` 或 `pip` 均可。冲突时新建虚拟环境重装，不要用 `--break-system-packages` 污染系统 Python。

### Q6：mypy 报 `app/core/config.py` 里的 settings 类型错误

`config.py` 用 `pydantic-settings`，改了字段后需要重新生成类型缓存：

```bash
rm -rf .mypy_cache && mypy app
```

### Q7：SSE 流式响应在 Nginx 下不流式

Nginx 必须关缓冲。`ops/docker/nginx.conf` 已配置 `proxy_buffering off;`，若自行改动过请检查。同时确认响应头 `X-Accel-Buffering: no`。

### Q8：请求返回 401

小程序端走微信登录换取 JWT。检查：

1. `JWT_SECRET` 是否在 API 与小程序配置中一致
2. token 是否过期（默认 7 天）
3. 客户端是否带了 `Authorization: Bearer <token>`

### Q9：怎么本地调试 Agent 流程？

```python
from app.agents.graph import get_graph
from app.agents.state import make_initial_state

graph = get_graph()
state = make_initial_state(user_id="u1", session_id="s1", query="我压力好大")
result = await graph.ainvoke(state)
print(result["answer"])
print(result["trace"])   # 每个节点的耗时与用量
```

`result["trace"]` 是排查「为什么慢/为什么贵」的第一入口。

## 三、检索

### Q10：检索结果为空

按顺序查：

```bash
# 1. 知识库有数据吗
docker compose exec postgres psql -U lumina -d lumina -c "SELECT count(*) FROM kb_chunks;"

# 2. BM25 索引有数据吗
curl -s http://localhost:9200/lumina-kb/_count | jq

# 3. 向量维度对得上吗
docker compose exec postgres psql -U lumina -d lumina -c "SELECT vector_dims(embedding) FROM kb_chunks LIMIT 1;"
```

`EMBEDDING_DIM` 必须与嵌入模型一致（`bge-m3` = 1024）。

### Q11：换了嵌入模型后检索全乱

换模型必须**重建索引**：向量维度变了，旧数据不可用。

```bash
python -m app.rag.ingest --source packages/knowledge --recreate
```

### Q12：BM25 检索没生效

`SEARCH_BACKEND` 需设为 `elasticsearch` 或 `quickwit`，且对应服务已启动：

```bash
docker compose --profile search up -d
```

## 四、模型与成本

### Q13：怎么知道一次对话花了多少钱？

`chat_messages` 表有 `cost_cny` 字段，`llm_calls` 表有逐次调用的明细。也可以看 Grafana 的「LLM 调用与成本趋势」面板。

### Q14：成本超预算了怎么办

按顺序：

1. 看 Grafana 找出是哪个模型/哪个环节涨的
2. 检查缓存命中率（低于 15% 说明缓存没生效）
3. 检查上下文长度（历史轮数、检索片段数）
4. 换更便宜的模型做降级链的第一档

### Q15：模型超时/限流怎么办

网关已内置重试 + 熔断。检查：

- `LLM_FALLBACK_MODELS` 是否配置
- 熔断器是否处于打开状态（看 `lumina_llm_circuit_open_total`）
- 供应商配额

### Q16：语义缓存会不会返回错答案？

会。阈值 0.95 偏高，但同义改写仍可能命中。观察 `lumina_llm_cache_hits_total` 与 BadCase 率；若出现缓存导致的错误，降低阈值或只对特定意图开启。

## 五、评测

### Q17：评测要花钱吗？

要。离线评测会真实调用模型。建议：

- 本地迭代用 `qwen-turbo` 级别模型
- 正式评测用与生产一致的模型
- 安全集几乎不花钱（危机路径不调 LLM）

### Q18：CI 评测失败，`baseline` 怎么更新？

**不要**在 CI 里自动更新。正确流程见 [05-evaluation.md §5](./05-evaluation.md)。本地跑评测 → 看差异 → 判断可接受 → 手动更新 `packages/datasets/golden/snapshot/baseline.json` → 在 PR 里写明理由。

### Q19：Judge 打分波动大

见 [05-evaluation.md §4](./05-evaluation.md) 的可靠性控制。核心是「规则能判就别用 Judge」。

## 六、部署

### Q20：部署流程是什么

`release.yml` → 构建镜像推 GHCR → SSH 到服务器 → `deploy.sh` → 备份 → 构建 → 迁移 → 启动 → 健康检查 → 冒烟 → 失败自动回滚。

### Q21：部署失败了怎么回滚

```bash
cd /opt/lumina
./ops/scripts/rollback.sh --to v1.0.0            # 代码回滚
./ops/scripts/rollback.sh --to v1.0.0 --db ops/data/backups/lumina-xxx.sql.gz  # 连数据库一起
```

### Q22：迁移失败导致服务起不来

迁移是向前兼容的，**旧代码在新 schema 上能跑**。所以：

```bash
git checkout tags/<上一个可用版本>
docker compose up -d
```

不需要回滚数据库。详见 `docs/RUNBOOK.md` §7。

### Q23：怎么查线上问题

1. 用用户报的 `trace_id` 查日志：`docker compose logs api | grep <trace_id>`
2. 查链路：Grafana → Tempo（如果部署了）
3. 查数据库：`chat_messages` 按 trace_id 查原始记录
4. 查成本：`llm_calls` 按 trace_id 查逐次调用

## 七、协作

### Q24：Prompt 改了怎么走流程

见 [`docs/SOP.md`](../SOP.md) SOP-3。核心三步：升版本 → 跑评测 → 更新 `eval_scores` 和 changelog。

### Q25：BadCase 怎么沉淀

见 [`docs/SOP.md`](../SOP.md) SOP-4。**同类问题第 2 次沉淀为回归用例，第 3 次沉淀为自动化规则。**

### Q26：贡献代码的流程

见 [`CONTRIBUTING.md`](../../CONTRIBUTING.md)。核心：分支 → 提交（Conventional Commits）→ PR → CI 通过 → review → 合并。

### Q27：哪些文件不会被 Git 跟踪

见 `.gitignore`。重点：`docs/_internal/`、`.env`、`reports/`、`eval_results/`、`ops/data/`、模型权重、真实用户数据。

> `docs/_internal/` 用于存放不对外公开的内部规划与草稿，**不要**把敏感信息写进会被跟踪的文件。
