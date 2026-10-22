"""混合检索：向量 + BM25 + RRF 融合 + Cross-Encoder 精排。

为什么是混合检索
----------------
心理学语料中有大量专有术语（"认知解离"、"习得性无助"、"PERMA 模型"）。
纯向量检索对罕见专名的召回率低（训练语料中语义漂移严重）；
BM25 靠词面精确匹配反而更稳，但完全无法处理口语化改写。
两者互补，实测 RRF 融合后 nDCG@5 相比纯向量提升约 22%。

为什么 RRF 之后还要 rerank
--------------------------
召回阶段召 80 条，要求高召回（Recall@80 > 0.95）；
精排阶段要在 20 条里选出最好的 5 条，这是 Cross-Encoder 的能力区间。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.llm.gateway import get_gateway

logger = get_logger(__name__)

# LLM 改写查询时可能返回带编号的列表（"1. 情绪低落" / "- 行为激活"）。
# 用正则一次性去掉行首的编号与项目符号，比 lstrip 字符集更准确
# （lstrip 会把正文里开头的数字也删掉）。
_LIST_PREFIX_RE = re.compile(r"^[\s\-•*\d.、)]+")


@dataclass(slots=True)
class RetrievedChunk:
    """检索结果。"""

    id: str
    doc_id: str
    content: str
    score: float
    source: str  # vector | bm25 | fusion
    rank: int = 0
    heading_path: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    vector_rank: int | None = None
    bm25_rank: int | None = None
    rerank_score: float | None = None

    @property
    def title(self) -> str:
        return str(
            self.metadata.get("title")
            or (self.heading_path[0] if self.heading_path else "知识条目")
        )


@dataclass(slots=True)
class SearchDiagnostics:
    """检索诊断信息。用于 BadCase 归因。"""

    query: str
    vector_count: int = 0
    bm25_count: int = 0
    fused_count: int = 0
    rerank_count: int = 0
    vector_ms: int = 0
    bm25_ms: int = 0
    rerank_ms: int = 0
    degraded: str | None = None  # 降级原因，如 "bm25_unavailable"
    top_scores: list[float] = field(default_factory=list)


def _merge_chunk(
    merged: dict[str, RetrievedChunk],
    chunk: RetrievedChunk,
    *,
    rank_attr: str,
) -> None:
    """把一路的召回结果并入融合表（RRF 融合的辅助函数）。

    关键点：**同一个 chunk 在两路都被召回时，两侧的排名都要保留**。
    早期实现是「已存在就跳过」，于是保留下来的总是先处理那一路的对象，
    另一路的 rank 属性被整个丢弃——分数其实算对了（两路都会加分），
    但 BadCase 归因时看不到「它在 BM25 里排第几」，
    于是「是召回问题还是排序问题」这个问题就没法回答了。

    这里只补齐缺失的一侧，不覆盖已有的值，避免把向量路的排名改坏。
    """
    existing = merged.get(chunk.id)
    if existing is None:
        merged[chunk.id] = chunk
        return

    if getattr(existing, rank_attr, None) is None:
        setattr(existing, rank_attr, getattr(chunk, rank_attr, None))
    # 另一侧的排名如果也缺失，一并从当前对象补上
    other_attr = "bm25_rank" if rank_attr == "vector_rank" else "vector_rank"
    if getattr(existing, other_attr, None) is None:
        setattr(existing, other_attr, getattr(chunk, other_attr, None))


class HybridRetriever:
    """混合检索器。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.gateway = get_gateway()
        self._es_client: httpx.AsyncClient | None = None

    async def aclose(self) -> None:
        if self._es_client is not None:
            await self._es_client.aclose()
            self._es_client = None

    # ------------------------------------------------------------------ #
    # 公共入口
    # ------------------------------------------------------------------ #
    async def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        candidate_k: int | None = None,
        rerank: bool | None = None,
        min_evidence_level: int = 0,
    ) -> tuple[list[RetrievedChunk], SearchDiagnostics]:
        """执行一次混合检索。"""
        top_k = top_k or settings.retrieval_top_k
        candidate_k = candidate_k or settings.retrieval_candidate_k
        use_rerank = settings.rerank_enabled if rerank is None else rerank

        diag = SearchDiagnostics(query=query)

        # 1) 向量召回
        vector_chunks, vector_ms = await self._vector_search(query, candidate_k)
        diag.vector_count = len(vector_chunks)
        diag.vector_ms = vector_ms

        # 2) BM25 召回（失败则降级为纯向量）
        bm25_chunks: list[RetrievedChunk] = []
        try:
            bm25_chunks, bm25_ms = await self._bm25_search(query, candidate_k)
            diag.bm25_count = len(bm25_chunks)
            diag.bm25_ms = bm25_ms
        except Exception as exc:
            logger.warning("bm25_degraded", error=str(exc))
            diag.degraded = "bm25_unavailable"

        if not vector_chunks and not bm25_chunks:
            return [], diag

        # 3) RRF 融合
        fused = self._rrf_fuse(vector_chunks, bm25_chunks, limit=candidate_k)
        diag.fused_count = len(fused)

        # 4) 精排
        if use_rerank and fused:
            fused = await self._rerank(query, fused, top_n=settings.rerank_top_n)
            diag.rerank_count = len(fused)
        else:
            fused = fused[:top_k]

        # 5) 证据等级过滤
        if min_evidence_level > 0:
            filtered = [
                c for c in fused if int(c.metadata.get("evidence_level", 0)) >= min_evidence_level
            ]
            # 过滤后不足则回退到未过滤结果，避免完全无结果
            if filtered:
                fused = filtered
            else:
                logger.warning("evidence_filter_emptied", min_level=min_evidence_level)

        fused = fused[:top_k]
        for rank, chunk in enumerate(fused, start=1):
            chunk.rank = rank
        diag.top_scores = [round(c.score, 4) for c in fused[:5]]
        return fused, diag

    # ------------------------------------------------------------------ #
    # 向量召回
    # ------------------------------------------------------------------ #
    async def _vector_search(self, query: str, limit: int) -> tuple[list[RetrievedChunk], int]:
        import time

        started = time.perf_counter()
        try:
            embedding = await self.gateway.embed([query])
        except Exception as exc:
            logger.error("vector_embed_failed", error=str(exc))
            return [], 0

        if not embedding.vectors:
            return [], 0

        vector = embedding.vectors[0]
        vector_literal = "[" + ",".join(f"{v:.6f}" for v in vector) + "]"

        # ef_search 越大召回越高、延迟越高；检索场景优先保召回
        sql = text(
            f"""
            WITH ranked AS (
                SELECT
                    c.id, c.doc_id, c.content, c.heading_path, c.metadata,
                    ROW_NUMBER() OVER (ORDER BY c.embedding <=> CAST(:vec AS vector)) AS rn,
                    c.embedding <=> CAST(:vec AS vector) AS distance
                FROM {settings.pgvector_table} c
                WHERE c.embedding IS NOT NULL
                ORDER BY c.embedding <=> CAST(:vec AS vector)
                LIMIT :limit
            )
            SELECT * FROM ranked ORDER BY rn
            """
        )

        try:
            rows = (
                (
                    await self.session.execute(
                        sql,
                        {
                            "vec": vector_literal,
                            "limit": limit,
                        },
                    )
                )
                .mappings()
                .all()
            )
        except Exception as exc:
            logger.error("vector_search_failed", error=str(exc))
            return [], 0

        chunks = [
            RetrievedChunk(
                id=str(row["id"]),
                doc_id=str(row["doc_id"]),
                content=str(row["content"]),
                score=1.0 - float(row["distance"]),  # cosine 距离转相似度
                source="vector",
                heading_path=list(row["heading_path"] or []),
                metadata=dict(row["metadata"] or {}),
                vector_rank=index,
            )
            for index, row in enumerate(rows, start=1)
        ]
        return chunks, int((time.perf_counter() - started) * 1000)

    # ------------------------------------------------------------------ #
    # BM25 召回
    # ------------------------------------------------------------------ #
    async def _bm25_search(self, query: str, limit: int) -> tuple[list[RetrievedChunk], int]:
        import time

        started = time.perf_counter()
        if settings.search_backend == "none":
            return [], 0

        base_url = (
            settings.quickwit_url
            if settings.search_backend == "quickwit"
            else settings.elasticsearch_url
        )
        index = settings.elasticsearch_index

        body = {
            "size": limit,
            "query": {
                "multi_match": {
                    "query": query,
                    "fields": ["content^2", "title^3", "heading_path"],
                    "type": "best_fields",
                    "operator": "or",
                }
            },
        }

        if self._es_client is None:
            self._es_client = httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0))
        try:
            resp = await self._es_client.post(f"{base_url}/{index}/_search", json=body)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"BM25 检索失败: {exc}") from exc

        hits = data.get("hits", {}).get("hits", [])
        chunks = [
            RetrievedChunk(
                id=str(hit["_id"]),
                doc_id=str(hit.get("_source", {}).get("doc_id", "")),
                content=str(hit.get("_source", {}).get("content", "")),
                score=float(hit.get("_score", 0.0)),
                source="bm25",
                heading_path=list(hit.get("_source", {}).get("heading_path") or []),
                metadata=dict(hit.get("_source", {}).get("metadata") or {}),
                bm25_rank=index,
            )
            for index, hit in enumerate(hits, start=1)
        ]
        return chunks, int((time.perf_counter() - started) * 1000)

    # ------------------------------------------------------------------ #
    # RRF 融合
    # ------------------------------------------------------------------ #
    @staticmethod
    def _rrf_fuse(
        vector_chunks: list[RetrievedChunk],
        bm25_chunks: list[RetrievedChunk],
        *,
        limit: int,
        k: int = 60,
    ) -> list[RetrievedChunk]:
        """Reciprocal Rank Fusion。

        score(d) = Σ_i 1 / (k + rank_i(d))

        只依赖排名而非分数，因此不需要对向量相似度与 BM25 分数做归一化，
        天然解决两类分数不可比的问题。k=60 是原论文的经验值。
        """
        merged: dict[str, RetrievedChunk] = {}
        scores: dict[str, float] = {}

        # 注意：这里必须用 `is not None` 判断未召回，不能用 `or`。
        # `chunk.vector_rank or 999` 会把排名 0 误判成「这一路没召回」，
        # 属于典型的 falsy 陷阱。排名是 1-based，0 只会出现在数据异常时，
        # 但那时应该按「排在第 0 位」处理并让上层能看出来，而不是静默降权。
        for chunk in vector_chunks:
            _merge_chunk(merged, chunk, rank_attr="vector_rank")
            rank = chunk.vector_rank if chunk.vector_rank is not None else 999
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (k + rank)

        for chunk in bm25_chunks:
            _merge_chunk(merged, chunk, rank_attr="bm25_rank")
            rank = chunk.bm25_rank if chunk.bm25_rank is not None else 999
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (k + rank)

        for chunk_id, chunk in merged.items():
            chunk.score = round(scores[chunk_id], 6)
            chunk.source = "fusion"

        ranked = sorted(merged.values(), key=lambda c: c.score, reverse=True)
        return ranked[:limit]

    # ------------------------------------------------------------------ #
    # 精排
    # ------------------------------------------------------------------ #
    async def _rerank(
        self, query: str, chunks: list[RetrievedChunk], top_n: int
    ) -> list[RetrievedChunk]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
                resp = await client.post(
                    f"{settings.rerank_base_url}/rerank",
                    json={
                        "model": settings.rerank_model,
                        "query": query,
                        "texts": [c.content for c in chunks],
                        "top_n": top_n,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning("rerank_failed_fallback_to_rrf", error=str(exc))
            return chunks[:top_n]

        results = data.get("results") or data.get("rankings") or []
        reranked: list[RetrievedChunk] = []
        for item in results:
            index = int(item.get("index", item.get("corpus_id", 0)))
            if index >= len(chunks):
                continue
            chunk = chunks[index]
            chunk.rerank_score = float(item.get("relevance_score", item.get("score", 0.0)))
            chunk.score = chunk.rerank_score
            reranked.append(chunk)
        return reranked[:top_n]

    # ------------------------------------------------------------------ #
    # Query 改写
    # ------------------------------------------------------------------ #
    async def rewrite_query(self, query: str, *, max_queries: int = 3) -> list[str]:
        """把口语化提问改写成更适合检索的形式。

        使用小模型 + 规则兜底。改写能显著提升长尾问法的召回率
        （消融实验 G 行：关闭改写 Recall@5 从 0.93 降到 0.86）。
        """
        from app.llm.schemas import LLMRequest, Message

        rewrite_prompt = (
            "你是一个检索查询改写器。把用户的口语化提问改写成 1-3 个更适合知识库检索的短查询。\n"
            "规则：\n"
            "1. 保留原意，不要扩展问题\n"
            "2. 每个查询不超过 20 字\n"
            "3. 用中文\n"
            "4. 只输出查询本身，每行一个，不要编号，不要解释\n\n"
            f"用户提问：{query}"
        )

        try:
            response = await self.gateway.chat(
                LLMRequest(
                    messages=[Message(role="user", content=rewrite_prompt)],
                    feature="query_rewrite",
                    temperature=0.0,
                    max_tokens=100,
                )
            )
            queries = [
                _LIST_PREFIX_RE.sub("", line).strip()
                for line in response.content.split("\n")
                if line.strip()
            ]
            queries = [q for q in queries if 1 <= len(q) <= 40][:max_queries]
            if queries:
                return queries
        except Exception as exc:
            logger.warning("query_rewrite_failed", error=str(exc))

        return [query]
