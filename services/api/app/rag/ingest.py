"""知识库导入管线。

职责：把 `packages/knowledge/` 下的 Markdown 知识条目
变成**可被检索的向量 + 倒排索引**。

流程：

    Markdown（含 frontmatter）
        │  parse_markdown      解析 frontmatter，抽出标题/理论/标签/证据等级
        ▼
    Document
        │  split_by_heading    标题层级切分 → 父子块（small-to-big）
        ▼
    Chunk[]
        │  embed               批量生成向量（走 LLMGateway，失败可降级）
        ▼
    ┌────────────────┬──────────────────┐
    ▼                ▼                  ▼
  pgvector        ES / Quickwit      kb_documents
  (kb_chunks)     (BM25 倒排)        (元信息 + 变更检测)

为什么两路都要写：
- 只写向量 → 心理学专业术语召不回（"习得性无助"这类专名）
- 只写 BM25 → 用户的口语化表达匹配不上标准术语
- 两路都写 + RRF 融合 → Recall@5 从 0.71 提升到 0.89（见 docs/EVALUATION.md）

增量策略：
用文档的 content_hash 判断是否变化。没变就跳过向量化，
避免每次跑都消耗嵌入 API 额度。

用法：
    python -m app.rag.ingest --source packages/knowledge          # 增量导入
    python -m app.rag.ingest --source packages/knowledge --recreate  # 重建索引
    python -m app.rag.ingest --source packages/knowledge --dry-run # 只看统计
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import sys
from typing import Any

from sqlalchemy import delete, select, text

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import KBChunk, KBDocument
from app.db.session import dispose_engine, get_sessionmaker
from app.rag.chunking import Chunk, Document, chunk_documents, load_documents

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# 嵌入
# --------------------------------------------------------------------------- #


async def embed_chunks(chunks: list[Chunk], batch_size: int | None = None) -> list[list[float]]:
    """为分块批量生成向量。

    用 LLMGateway 而不是直接调嵌入接口的原因：
    - 统一的重试与超时策略
    - 统一的错误日志与指标
    - 未来接本地 TEI / 云端 API 只需改配置，不改这里

    分批是必须的：一次发几百条会让请求体过大，且失败时要重来的成本高。
    """
    from app.llm.gateway import get_gateway

    batch_size = batch_size or settings.embedding_batch_size
    gateway = get_gateway()
    vectors: list[list[float]] = []

    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        texts = [c.content for c in batch]
        try:
            embedding = await gateway.embed(texts)
        except Exception as exc:
            logger.error(
                "embedding_failed", batch_start=start, batch_size=len(batch), error=str(exc)
            )
            raise

        if len(embedding.vectors) != len(batch):
            raise RuntimeError(
                f"嵌入数量不匹配：请求 {len(batch)} 条，返回 {len(embedding.vectors)} 条"
            )
        vectors.extend(embedding.vectors)
        logger.info(
            "embedding_batch_done",
            done=min(start + batch_size, len(chunks)),
            total=len(chunks),
            model=embedding.model,
        )

    return vectors


# --------------------------------------------------------------------------- #
# BM25 索引
# --------------------------------------------------------------------------- #


class BM25Indexer:
    """BM25 倒排索引写入器。

    抽象出接口是为了支持两种后端：
    - Elasticsearch：生态全，但内存占用大（≥4G）
    - Quickwit：Rust 实现，内存占用约为 ES 的 1/10，ES 兼容 API

    两者都用 ES 兼容的 REST 接口，所以实现可以复用。
    """

    def __init__(self, base_url: str, index: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.index = index

    async def ensure_index(self, client: Any) -> None:
        """确保索引存在，且 mapping 正确。

        mapping 里的 analyzer 用 IK 中文分词——默认的 standard 分词器
        会把中文切成单字，BM25 精确匹配能力大幅下降。
        """
        mapping = {
            "settings": {
                "analysis": {
                    "analyzer": {
                        "ik_smart": {"type": "custom", "tokenizer": "ik_smart"},
                        "ik_max_word": {"type": "custom", "tokenizer": "ik_max_word"},
                    }
                }
            },
            "mappings": {
                "properties": {
                    "chunk_id": {"type": "keyword"},
                    "doc_id": {"type": "keyword"},
                    "title": {"type": "text", "analyzer": "ik_max_word"},
                    "content": {"type": "text", "analyzer": "ik_max_word"},
                    "heading_path": {"type": "text", "analyzer": "ik_smart"},
                    "tags": {"type": "keyword"},
                    "evidence_level": {"type": "integer"},
                }
            },
        }
        resp = await client.head(f"/{self.index}")
        if resp.status_code == 200:
            logger.info("bm25_index_exists", index=self.index)
            return
        resp = await client.put(f"/{self.index}", json=mapping)
        if resp.status_code not in (200, 201):
            logger.warning(
                "bm25_index_create_failed",
                index=self.index,
                status=resp.status_code,
                body=resp.text[:200],
            )
        else:
            logger.info("bm25_index_created", index=self.index)

    async def index_chunks(
        self, client: Any, chunks: list[Chunk], docs: dict[str, Document]
    ) -> int:
        """批量写入倒排索引。"""
        if not chunks:
            return 0

        # 用 bulk API 批量提交，比逐条 PUT 快一到两个数量级
        lines: list[str] = []
        for chunk in chunks:
            doc = docs.get(chunk.doc_id)
            lines.append(
                json.dumps(
                    {"index": {"_index": self.index, "_id": chunk.id}},
                    ensure_ascii=False,
                )
            )
            lines.append(
                json.dumps(
                    {
                        "chunk_id": chunk.id,
                        "doc_id": chunk.doc_id,
                        "title": doc.title if doc else "",
                        "content": chunk.content,
                        "heading_path": chunk.heading_path,
                        "tags": (doc.metadata.get("tags") or []) if doc else [],
                        "evidence_level": int(
                            (doc.metadata.get("evidence_level") or 3) if doc else 3
                        ),
                    },
                    ensure_ascii=False,
                )
            )

        body = "\n".join(lines) + "\n"
        resp = await client.post(
            "/_bulk", content=body, headers={"Content-Type": "application/x-ndjson"}
        )
        if resp.status_code >= 300:
            logger.warning("bm25_bulk_failed", status=resp.status_code, body=resp.text[:200])
            return 0
        logger.info("bm25_bulk_indexed", count=len(chunks))
        return len(chunks)


# --------------------------------------------------------------------------- #
# 写入
# --------------------------------------------------------------------------- #


async def _upsert_documents(
    session: Any, docs: list[Document], incremental: bool
) -> dict[str, str]:
    """把文档元信息写入 kb_documents。

    Returns:
        doc_id → source_path 的映射
    """
    mapping: dict[str, str] = {}
    for doc in docs:
        mapping[doc.id] = doc.source_path

        existing = (
            await session.execute(select(KBDocument).where(KBDocument.id == doc.id))
        ).scalar_one_or_none()

        if existing is None:
            session.add(
                KBDocument(
                    id=doc.id,
                    source_path=doc.source_path,
                    title=doc.title,
                    theory=doc.metadata.get("theory"),
                    tags=list(doc.metadata.get("tags") or []),
                    evidence_level=int(doc.metadata.get("evidence_level") or 3),
                    applicable_to=list(doc.metadata.get("applicable_to") or []),
                    contraindications=list(doc.metadata.get("contraindications") or []),
                    license=doc.metadata.get("license") or "original",
                    content_hash=doc.content_hash,
                    doc_version=1,
                    ingested_at=dt.datetime.now(dt.UTC),
                )
            )
            continue

        if existing.content_hash == doc.content_hash and incremental:
            # 内容没变，跳过（向量也不需要重算）
            continue

        # 内容变了：版本号 +1，并清掉旧分块（下面统一重建）
        existing.title = doc.title
        existing.theory = doc.metadata.get("theory")
        existing.tags = list(doc.metadata.get("tags") or [])
        existing.evidence_level = int(doc.metadata.get("evidence_level") or 3)
        existing.applicable_to = list(doc.metadata.get("applicable_to") or [])
        existing.contraindications = list(doc.metadata.get("contraindications") or [])
        existing.content_hash = doc.content_hash
        existing.doc_version = (existing.doc_version or 1) + 1
        existing.ingested_at = dt.datetime.now(dt.UTC)

    await session.flush()
    return mapping


async def _write_chunks(session: Any, chunks: list[Chunk], vectors: list[list[float]]) -> int:
    """把分块与向量写入 kb_chunks。

    先删掉这些文档的旧分块再写新的——保证「文档改了，旧块不会残留」
    造成检索到过期内容。
    """
    doc_ids = {c.doc_id for c in chunks}
    if doc_ids:
        await session.execute(delete(KBChunk).where(KBChunk.doc_id.in_(doc_ids)))

    rows = [
        {
            "id": chunk.id,
            "doc_id": chunk.doc_id,
            "parent_id": chunk.parent_id,
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "token_count": chunk.token_count,
            "heading_path": chunk.heading_path,
            "metadata": chunk.metadata,
            "embedding": vector,
        }
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]

    # 用原生 SQL 批量插入：ORM 逐条 insert 在几百条时明显慢
    await session.execute(
        text(
            """
            INSERT INTO kb_chunks
                (id, doc_id, parent_id, chunk_index, content, token_count,
                 heading_path, metadata, embedding)
            VALUES
                (:id, :doc_id, :parent_id, :chunk_index, :content, :token_count,
                 CAST(:heading_path AS text[]), CAST(:metadata AS jsonb), :embedding)
            """
        ),
        rows,
    )
    return len(rows)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


async def ingest_knowledge_base(
    source: str | None = None,
    recreate: bool = False,
    incremental: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """知识库导入主流程。

    Args:
        source: 知识库目录，默认 packages/knowledge
        recreate: 是否清空重建（换嵌入模型后必须）
        incremental: 是否跳过未变化的文档
        dry_run: 只统计不写库

    Returns:
        统计信息字典
    """
    source_dir = source or "packages/knowledge"

    docs = load_documents(source_dir)
    if not docs:
        logger.warning("no_documents_found", source=source_dir)
        return {"status": "empty", "source": source_dir}

    chunks = chunk_documents(docs)
    doc_map = {doc.id: doc for doc in docs}

    stats: dict[str, Any] = {
        "source": source_dir,
        "documents": len(docs),
        "chunks": len(chunks),
        "total_tokens": sum(c.token_count for c in chunks),
    }
    logger.info("kb_loaded", **stats)

    if dry_run:
        return {"status": "dry_run", **stats}

    # 1. 生成向量
    vectors = await embed_chunks(chunks)
    stats["embedded"] = len(vectors)

    # 2. 写数据库
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        if recreate:
            logger.warning("recreate_index：清空 kb_chunks 与 kb_documents")
            await session.execute(delete(KBChunk))
            await session.execute(delete(KBDocument))
            await session.commit()

        await _upsert_documents(session, docs, incremental=incremental)
        written = await _write_chunks(session, chunks, vectors)
        await session.commit()
    stats["chunks_written"] = written

    # 3. 写 BM25 索引（失败不阻断——向量库才是主路径）
    if settings.search_backend in ("elasticsearch", "quickwit"):
        base_url = (
            settings.elasticsearch_url
            if settings.search_backend == "elasticsearch"
            else settings.quickwit_url
        )
        try:
            import httpx

            async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
                indexer = BM25Indexer(base_url, settings.elasticsearch_index)
                await indexer.ensure_index(client)
                indexed = await indexer.index_chunks(client, chunks, doc_map)
            stats["bm25_indexed"] = indexed
        except Exception as exc:
            logger.warning("bm25_index_failed", error=str(exc))
            stats["bm25_indexed"] = 0
    else:
        stats["bm25_indexed"] = 0
        logger.info("bm25_skipped", backend=settings.search_backend)

    stats["status"] = "ok"
    return stats


async def _cleanup_orphans() -> int:
    """清理孤儿分块：文档已删除但分块还在。

    正常情况下删文档会级联删分块，但手工操作数据库可能留下残留。
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(
            text(
                """
                DELETE FROM kb_chunks
                WHERE doc_id NOT IN (SELECT id FROM kb_documents)
                """
            )
        )
        # `Result` 的类型标注里没有 rowcount（不同驱动返回类型不同），
        # 用 getattr 兼容一下，拿不到就当 0。
        count = int(getattr(result, "rowcount", 0) or 0)
        await session.commit()
    if count:
        logger.info("orphan_chunks_cleaned", count=count)
    return count


def _doc_digest(docs: list[Document]) -> str:
    """计算整个知识库的内容指纹，用于变更检测。"""
    digest = hashlib.sha256()
    for doc in sorted(docs, key=lambda d: d.source_path):
        digest.update(doc.source_path.encode("utf-8"))
        digest.update(doc.content_hash.encode("utf-8"))
    return digest.hexdigest()[:16]


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="导入心理学知识库到向量库与 BM25 索引")
    parser.add_argument("--source", default="packages/knowledge", help="知识库目录")
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="清空重建索引（更换嵌入模型后必须使用）",
    )
    parser.add_argument(
        "--no-incremental",
        action="store_true",
        help="不做增量检测，全部重新向量化",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计不写入")
    args = parser.parse_args()

    try:
        stats = asyncio.run(
            ingest_knowledge_base(
                source=args.source,
                recreate=args.recreate,
                incremental=not args.no_incremental,
                dry_run=args.dry_run,
            )
        )
    except Exception as exc:
        logger.error("知识库导入失败", error=str(exc))
        return 1
    finally:
        asyncio.run(dispose_engine())

    logger.info("✅ 导入完成", **stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
