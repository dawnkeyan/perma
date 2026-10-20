---
name: rag-tuning
description: Lumina 混合 RAG 检索链路的调优技能。当需要提升召回率/排序质量、调整分块策略、接入或修改 BM25 通道、调整 RRF 融合参数、排查「答得不对但检索看起来正常」的问题时使用。提供分块契约、参数含义、诊断决策树与消融实验方法。
---

# Lumina 混合 RAG 调优技能

> 检索质量是回答质量的**上游天花板**。回答看起来不好时，
> 先判断是「没检索到」还是「检索到了但排序不对」——两者的修法完全不同。

---

## 一、检索链路全貌

```
用户 query
   │
   ├─→ ① Query 改写（LLM + 规则兜底，最多 3 个子查询）
   │
   ├─→ ② 向量检索  ──→ pgvector HNSW（余弦相似度，top = candidate_k）
   │                  擅长语义相似（「撑不住了」↔「压力太大」）
   │
   ├─→ ③ BM25 检索 ──→ Elasticsearch（IK 中文分词，top = candidate_k）
   │                  擅长关键词精确匹配（「PERMA」「行为激活」「认知重构」）
   │
   ├─→ ④ RRF 融合  ──→ score(d) = Σ 1 / (60 + rank_i)
   │                  只用排名不用分数 → 不需要归一化
   │
   ├─→ ⑤ rerank    ──→ bge-reranker-v2-m3，取 top_k
   │                  交叉编码器，能理解 query 与文档的细粒度对应
   │
   └─→ ⑥ 证据等级过滤（可回退）
```

**关键点**：向量和 BM25 的分数**完全不可比**（余弦相似度 0~1，BM25 无上界）。
这就是必须用 RRF 而不是「加权求和」的原因。

---

## 二、参数速查

| 参数 | 位置 | 默认 | 调整方向 |
| --- | --- | --- | --- |
| `child_max_tokens` | `split_by_heading` | 256 | 调小→召回更准但上下文更碎；调大→上下文更全但噪音多 |
| `parent_max_tokens` | `split_by_heading` | 1024 | 返回给模型的上下文上限 |
| `overlap_sections` | `split_by_heading` | 1 | 章节重叠数，防止边界信息丢失 |
| `candidate_k` | `search` | 配置项 | 每路召回的候选数。**调大→rerank 有更多选择，但延迟线性增加** |
| `top_k` | `search` | 配置项 | 最终返回给模型的条数 |
| `k`（RRF） | `_rrf_fuse` | 60 | 原论文经验值。**不要随手改**，改了要重跑消融实验 |
| `min_evidence_level` | `search` | 0 | 提高可过滤掉低证据等级条目，但过滤后为空会回退 |

---

## 三、分块契约（改分块前必须知道）

`split_by_heading` 产出**父子块**：

- **父块**：`parent_id is None`，一个 H2 章节（超长则按 token 预算二次切）
- **子块**：`parent_id` 指向父块，段落级小块（超长则按句子边界再切）

**检索用子块，返回用父块**（small-to-big）。原因：
小块语义集中 → 相似度更准；父块上下文完整 → 模型不会只看到半句话。

### 改分块时的硬性要求

1. 子块的 `parent_id` 必须指向真实存在的父块（`tests/test_chunking.py` 守着）
2. 子块 `token_count` 不得超过 `child_max_tokens`（允许少量误差）
3. `heading_path` 必须完整（`["第二节", "补充"]`）——用于回显「依据来自哪一节」
4. 文档级 metadata（如 `evidence_level`）必须传到块上——否则证据等级过滤会失效

---

## 四、诊断决策树

**「回答不好」时按这个顺序排查：**

```
回答不好
   │
   ├─ ① 检索结果里根本没有正确内容？
   │     → 是：召回问题
   │         ├─ 向量通道也没召回？→ 知识库缺这条内容 / 分块把它切碎了
   │         │                        → 跑 make kb-ingest 确认索引已更新
   │         ├─ BM25 召回了但向量没召回？
   │         │   → 语义鸿沟：query 与文档用词差异大
   │         │   → 解法：Query 改写、加同义词、或补知识库
   │         └─ 两路都没有 → 知识库内容缺失（不是检索问题）
   │
   └─ ② 检索结果里有正确内容，但排序靠后？
         → 是：排序问题
             ├─ rerank 前排名靠后 → RRF 权重问题 / 某一路排名太靠后
             ├─ rerank 后排名靠后 → reranker 效果问题，或 query 太长太泛
             └─ 根本没跑 rerank → 检查 `rerank` 参数与服务可用性
```

### 诊断信息从哪来

`SearchDiagnostics`（`app/rag/retriever.py`）记录了：

| 字段 | 含义 |
| --- | --- |
| `vector_count` / `bm25_count` | 各自召回了多少条 |
| `fused_count` | 融合后多少条 |
| `rerank_count` | 精排后多少条 |
| `vector_ms` / `bm25_ms` / `rerank_ms` | 各路耗时 |
| `degraded` | 降级原因（如 `bm25_unavailable`） |
| `top_scores` | 融合后的 top 分数分布 |

BadCase 归因时**先看这个**，不要凭感觉猜。

---

## 五、改动后的验证流程

```bash
# 1. 改代码或配置

# 2. 重建索引（分块策略变了必须重建）
make kb-ingest

# 3. 跑评测，看指标
make eval
python -m app.eval.report

# 4. 对比消融实验表
#    填进 docs/EVALUATION.md 的消融表，并写清这次改了什么

# 5. 确认门禁通过
python -m app.eval.gate \
  --current reports/eval/current.json \
  --baseline packages/datasets/golden/snapshot/baseline.json
```

### 消融实验的做法

**一次只改一个变量。** 每次改动记录：

| 改动 | Recall@5 | nDCG@5 | 延迟 P95 | 结论 |
| --- | --- | --- | --- | --- |
| 基线 | 0.93 | 0.81 | 1200ms | — |
| + 加大 candidate_k 到 50 | ? | ? | ? | ? |

不记录消融数据的「优化」等于没做——下次没人知道这个参数为什么是这个值。

---

## 六、常见坑

| 现象 | 原因 | 解法 |
| --- | --- | --- |
| 改完代码检索结果没变 | 索引没重建 | `make kb-ingest` |
| ES 查不到中文 | 分词器没配好 | 检查 mapping 里的 `ik_max_word` |
| rerank 结果没变 | 缓存命中 | 清语义缓存，或用新 query 测 |
| 引用编号对不上内容 | 上下文组装时编号分配错位 | 查 `app/rag/context.py` |
| 两路都召回但排名很靠后 | RRF 只看排名，两路都排第 10 也会靠后 | 正常现象，rerank 会救 |
| 知识库新增内容检索不到 | 增量导入的 `content_hash` 判断失误 | `--no-incremental` 强制重建 |
| 延迟突然变高 | rerank 服务超时在重试 | 查 `rerank_ms` 与熔断状态 |

---

## 七、修改清单

改动检索链路时，逐条确认：

- [ ] 没有绕过 `HybridRetriever` 直接查向量库
- [ ] RRF 的 `k` 没被随手改（改了要重跑消融并更新文档）
- [ ] 分块的父子关系仍然自洽
- [ ] 检索结果条数没有超过 prompt 预算（`top_k` × `parent_max_tokens` 要放得进上下文）
- [ ] 新增了对应的测试（`tests/test_rag_retrieval.py`）
- [ ] 评测跑过，消融表已更新
- [ ] 延迟变化已记录（检索是延迟大户）
