"""数据库初始化脚本。

职责：
1. 创建 PostgreSQL 扩展（pgvector）
2. 创建所有表（幂等，可重复执行）
3. 创建向量索引（HNSW）与业务索引
4. 输出可读的初始化结果

与 Alembic 的关系：
- **Alembic** 负责「版本化迁移」（生产环境用，`make db-migrate`）
- **本脚本** 负责「首次建库」（开发环境用，`make db-init`）
两者都保留：前者管演进，后者管冷启动。

用法：
    python -m app.db.init_db              # 建表 + 建索引
    python -m app.db.init_db --drop       # 先删表再建（危险，仅本地用）
    python -m app.db.init_db --no-index   # 只建表不建索引（大库导入前用）
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text

from app.core.logging import get_logger
from app.db.models import Base
from app.db.session import dispose_engine, get_engine

logger = get_logger(__name__)


# HNSW 索引参数说明（见 docs/DATA_MODEL.md §2.5）：
#   m=16                 —— 图的出度，召回率与内存的平衡点，官方默认 16
#   ef_construction=64   —— 构建时的候选队列长度，越大召回越高、建索引越慢
# 查询时 ef_search=200（见 config.hnsw_ef_search）远大于默认 40，
# 因为检索场景更看重召回率。
HNSW_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_kb_chunks_embedding
    ON kb_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
"""

# 业务索引：与 models.py 里的 Index 声明互补（这里处理跨表的常用查询）
EXTRA_INDEX_SQL = [
    # BadCase 工作台：按状态筛选待处理
    "CREATE INDEX IF NOT EXISTS idx_badcases_open "
    "ON bad_cases (priority, created_at DESC) WHERE status IN ('open', 'processing')",
    # 成本分析：按天聚合
    "CREATE INDEX IF NOT EXISTS idx_llm_calls_created_date "
    "ON llm_calls (date_trunc('day', created_at))",
    # 情绪趋势：按用户取最近记录
    "CREATE INDEX IF NOT EXISTS idx_journal_recent "
    "ON journal_entries (user_id, occurred_at DESC)",
]


async def create_extensions() -> None:
    """创建 pgvector 扩展。

    必须在建表之前——kb_chunks 的 embedding 列依赖 vector 类型。
    权限不足时（非超级用户）会报错，此时应改用带扩展的迁移模板创建库。
    """
    engine = get_engine()
    async with engine.begin() as conn:
        # CREATE EXTENSION IF NOT EXISTS 是幂等的
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    logger.info("pgvector 扩展就绪")


async def create_tables(drop: bool = False) -> None:
    """创建或重建所有表。

    Args:
        drop: 是否先删除所有表。**生产环境绝对不能为 True**。
    """
    engine = get_engine()
    async with engine.begin() as conn:
        if drop:
            logger.warning("正在删除所有表（--drop）")
            # CASCADE 会连带删除依赖的索引与视图
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))

        # create_all 只创建不存在的表，不会修改已存在的表结构——
        # 所以表结构变更必须走 Alembic，这是本项目的一条硬规则。
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据表就绪")


async def create_indexes() -> None:
    """创建向量索引与业务索引。"""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text(HNSW_INDEX_SQL))
        for stmt in EXTRA_INDEX_SQL:
            await conn.execute(text(stmt))
    logger.info("索引就绪")


async def verify() -> dict[str, int]:
    """校验初始化结果，返回各表行数。

    用途：让「初始化成功」不是一句空话，而是有可核对的事实。
    """
    engine = get_engine()
    counts: dict[str, int] = {}
    async with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            result = await conn.execute(text(f"SELECT count(*) FROM {table.name}"))
            counts[table.name] = int(result.scalar_one())
    return counts


async def init_db(drop: bool = False, with_index: bool = True) -> int:
    """完整初始化流程。"""
    logger.info("开始初始化数据库")
    try:
        await create_extensions()
        await create_tables(drop=drop)
        if with_index:
            await create_indexes()
        counts = await verify()
    except Exception as exc:  # noqa: BLE001 - 顶层入口需要给出清晰错误
        logger.error("数据库初始化失败", error=str(exc))
        return 1
    finally:
        await dispose_engine()

    logger.info("初始化完成")
    for table, count in counts.items():
        logger.info(f"  {table}: {count} 行")
    return 0


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="初始化 Lumina 数据库")
    parser.add_argument(
        "--drop",
        action="store_true",
        help="先删除所有表再建（危险，仅本地开发使用）",
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="跳过索引创建（大库批量导入前可提速）",
    )
    args = parser.parse_args()

    if args.drop:
        logger.warning("⚠️  --drop 会清空数据库，确认这是本地开发环境")

    return asyncio.run(init_db(drop=args.drop, with_index=not args.no_index))


if __name__ == "__main__":
    sys.exit(main())
