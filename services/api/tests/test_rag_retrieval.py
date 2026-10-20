"""混合检索与 RRF 融合单元测试。

RRF（Reciprocal Rank Fusion）是本项目混合检索的核心：
向量检索擅长语义、BM25 擅长关键词，两者的分数完全不可比。
RRF 只用**排名**不用分数，天然绕开了归一化问题。

消融实验里，纯向量 Recall@5 = 0.71，加上 BM25 + RRF 后到 0.89。
这个提升完全依赖 RRF 公式的正确性，所以公式本身必须有测试守护。
"""

from __future__ import annotations

from app.rag.retriever import HybridRetriever, RetrievedChunk


def _chunk(
    chunk_id: str,
    *,
    vector_rank: int | None = None,
    bm25_rank: int | None = None,
) -> RetrievedChunk:
    """构造一个检索结果。

    `vector_rank` / `bm25_rank` 是 1-based 的排名，None 表示该路检索没召回它。
    """
    return RetrievedChunk(
        id=chunk_id,
        doc_id=f"doc_{chunk_id}",
        content=f"内容 {chunk_id}",
        score=0.0,
        source="vector" if bm25_rank is None else "bm25",
        vector_rank=vector_rank,
        bm25_rank=bm25_rank,
    )


class TestRRFFusion:
    """RRF 融合。"""

    def test_empty_inputs(self) -> None:
        assert HybridRetriever._rrf_fuse([], [], limit=10) == []

    def test_single_source_keeps_order(self) -> None:
        """只有一路召回时，融合结果应保持原排名顺序。"""
        vector = [_chunk("a", vector_rank=1), _chunk("b", vector_rank=2)]
        fused = HybridRetriever._rrf_fuse(vector, [], limit=10)
        assert [c.id for c in fused] == ["a", "b"]

    def test_chunk_in_both_sources_scores_higher(self) -> None:
        """**核心性质**：两路都召回的块，融合分必须高于只被一路召回的块。

        这正是 RRF 相对「只取向量 Top-K」的价值所在。
        """
        vector = [_chunk("both", vector_rank=1), _chunk("only_v", vector_rank=2)]
        bm25 = [_chunk("both", bm25_rank=1), _chunk("only_b", bm25_rank=2)]
        fused = HybridRetriever._rrf_fuse(vector, bm25, limit=10)
        assert fused[0].id == "both"

    def test_formula_matches_paper(self) -> None:
        """分数必须严格等于 Σ 1/(k + rank_i)，k=60。"""
        k = 60
        vector = [_chunk("x", vector_rank=1)]
        bm25 = [_chunk("x", bm25_rank=3)]
        fused = HybridRetriever._rrf_fuse(vector, bm25, limit=10)
        expected = round(1.0 / (k + 1) + 1.0 / (k + 3), 6)
        assert fused[0].score == expected

    def test_default_k_is_60(self) -> None:
        """k=60 是原论文经验值，改动需要同步更新消融实验表。"""
        vector = [_chunk("x", vector_rank=1)]
        fused = HybridRetriever._rrf_fuse(vector, [], limit=10)
        assert fused[0].score == round(1.0 / 61, 6)

    def test_results_sorted_descending(self) -> None:
        vector = [
            _chunk("a", vector_rank=1),
            _chunk("b", vector_rank=2),
            _chunk("c", vector_rank=3),
        ]
        bm25 = [_chunk("c", bm25_rank=1), _chunk("b", bm25_rank=2)]
        fused = HybridRetriever._rrf_fuse(vector, bm25, limit=10)
        scores = [c.score for c in fused]
        assert scores == sorted(scores, reverse=True)
        assert fused[0].id == "c", "c 在两路都排第一，应当排最前"

    def test_limit_is_respected(self) -> None:
        vector = [_chunk(f"c{i}", vector_rank=i) for i in range(1, 11)]
        fused = HybridRetriever._rrf_fuse(vector, [], limit=3)
        assert len(fused) == 3

    def test_source_marked_as_fusion(self) -> None:
        """融合后的结果要标记来源，便于线上排查是哪一路贡献的。"""
        vector = [_chunk("a", vector_rank=1)]
        bm25 = [_chunk("b", bm25_rank=1)]
        fused = HybridRetriever._rrf_fuse(vector, bm25, limit=10)
        assert all(c.source == "fusion" for c in fused)

    def test_ranks_preserved_for_diagnosis(self) -> None:
        """原始排名必须保留——BadCase 归因要靠它判断「是召回问题还是排序问题」。"""
        vector = [_chunk("a", vector_rank=1)]
        bm25 = [_chunk("a", bm25_rank=5)]
        fused = HybridRetriever._rrf_fuse(vector, bm25, limit=10)
        assert fused[0].vector_rank == 1
        assert fused[0].bm25_rank == 5

    def test_duplicate_ids_merged(self) -> None:
        """同一个 id 在两路都出现只能算一条，不能重复占位。"""
        vector = [_chunk("dup", vector_rank=1)]
        bm25 = [_chunk("dup", bm25_rank=1)]
        fused = HybridRetriever._rrf_fuse(vector, bm25, limit=10)
        assert len(fused) == 1
