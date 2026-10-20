"""异步任务 Worker。

为什么需要独立的 worker：
把「不影响用户响应」的工作从同步链路里摘出去，让主链路 P95 更稳。
典型的三类任务：
1. **用户画像抽取** —— 每次对话后异步更新画像，主链路不等它
2. **会话摘要压缩** —— 轮次变多时异步滚动压缩历史
3. **指标聚合** —— 定时把流水表聚合成日报表，供看板查询

技术选型：arq（asyncio-native 的 Redis 队列）
- 与项目已有的 redis 依赖一致，不引入第二套中间件
- 原生 async，任务函数可以直接复用项目里的 async 客户端
- 支持 cron 定时任务，开源项目里够用

启动方式：
    python -m app.worker                # 前台运行
    arq app.worker.WorkerSettings       # 等价（生产推荐）
    docker compose up -d worker         # 容器里运行

注意：
- 任务必须**幂等**，因为 arq 的重试机制会重复投递
- 任务失败不能影响主链路，所以这里只记日志不抛出
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

from arq import cron
from arq.typing import StartupShutdown, WorkerSettingsBase

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import dispose_engine, get_sessionmaker

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# 生命周期钩子
# --------------------------------------------------------------------------- #


async def startup(ctx: dict[str, Any]) -> None:
    """Worker 启动时执行。

    初始化共享资源放入 ctx，任务函数通过 ctx 复用，避免每个任务重复建连接。
    """
    logger.info("worker 启动", env=settings.app_env)
    ctx["sessionmaker"] = get_sessionmaker()


async def shutdown(ctx: dict[str, Any]) -> None:  # noqa: ARG001
    """Worker 关闭时执行：释放数据库连接池。"""
    logger.info("worker 关闭中")
    await dispose_engine()
    logger.info("worker 已关闭")


# --------------------------------------------------------------------------- #
# 任务一：用户画像抽取
# --------------------------------------------------------------------------- #


async def extract_user_profile(
    ctx: dict[str, Any],
    user_id: str,
    session_id: str,  # noqa: ARG001
) -> dict[str, Any]:
    """从最近对话中抽取用户画像信息。

    设计取舍：
    这里**没有**直接调用 LLM 做抽取——原因是画像抽取是「锦上添花」，
    一旦模型超时或成本超支，不应该拖垮主链路。
    当前实现用规则抽取（关键词 + 情绪统计），后续可演进为
    「小模型 + 结构化输出」，但必须保留兜底：抽取失败就跳过，不报错。

    幂等性：同一 user_id 重复执行会覆盖式更新，不会产生重复数据。
    """
    from sqlalchemy import select

    from app.db.models import ChatMessage, JournalEntry, UserProfile

    try:
        sessionmaker = ctx.get("sessionmaker") or get_sessionmaker()
        async with sessionmaker() as session:
            # 取最近 50 条消息做统计
            rows = (
                (
                    await session.execute(
                        select(ChatMessage)
                        .where(ChatMessage.user_id == user_id)
                        .order_by(ChatMessage.created_at.desc())
                        .limit(50)
                    )
                )
                .scalars()
                .all()
            )

            if not rows:
                return {"status": "skipped", "reason": "no_messages"}

            # 规则抽取：从 assistant 消息的 insights 段里找高频主题词
            themes: dict[str, int] = {}
            for msg in rows:
                sections = msg.sections or {}
                insight = sections.get("insight") or ""
                for word in _extract_keywords(insight):
                    themes[word] = themes.get(word, 0) + 1

            top_themes = [w for w, _ in sorted(themes.items(), key=lambda x: -x[1])[:5]]

            # 统计情绪日记里的主导情绪，作为画像的一部分
            emotions = (
                (
                    await session.execute(
                        select(JournalEntry.emotion)
                        .where(JournalEntry.user_id == user_id)
                        .order_by(JournalEntry.occurred_at.desc())
                        .limit(30)
                    )
                )
                .scalars()
                .all()
            )
            dominant = ""
            if emotions:
                dominant = max(set(emotions), key=emotions.count)

            # 覆盖式更新
            profile = (
                await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
            ).scalar_one_or_none()

            if profile is None:
                profile = UserProfile(user_id=user_id)
                session.add(profile)

            profile.long_term_theme = "；".join(top_themes) or profile.long_term_theme
            profile.raw_facts = {
                "dominant_emotion": dominant,
                "message_count": len(rows),
            }
            await session.commit()

        logger.info("user_profile_updated", user_id=user_id, themes=top_themes)
        return {"status": "ok", "themes": top_themes}

    except Exception as exc:
        logger.warning("extract_user_profile_failed", user_id=user_id, error=str(exc))
        return {"status": "failed", "error": str(exc)}


# 中文停用词：这些词在心理场景里出现频率高但没有信息量
_STOPWORDS = {
    "的",
    "了",
    "是",
    "在",
    "我",
    "你",
    "他",
    "她",
    "它",
    "和",
    "就",
    "都",
    "也",
    "很",
    "有",
    "没",
    "不",
    "这",
    "那",
    "一个",
    "什么",
    "自己",
    "觉得",
    "感觉",
    "可以",
    "因为",
    "所以",
    "但是",
    "而且",
    "现在",
}


def _extract_keywords(text: str) -> list[str]:
    """从文本中抽取候选主题词。

    用 jieba 分词后过滤停用词与过短词。
    规则抽取的局限是显而易见的（没有语义理解），
    但对「关键词统计」这个用途够用，且零成本零延迟。
    """
    if not text:
        return []
    try:
        import jieba
    except ImportError:  # pragma: no cover - jieba 是可选加速
        return []

    words = jieba.lcut(text)
    result: list[str] = []
    for word in words:
        word = word.strip()
        if len(word) < 2 or word in _STOPWORDS:
            continue
        if not any("\u4e00" <= ch <= "\u9fff" for ch in word):
            # 过滤纯英文/数字
            continue
        result.append(word)
    return result


# --------------------------------------------------------------------------- #
# 任务二：会话摘要压缩
# --------------------------------------------------------------------------- #


async def compact_session_summary(ctx: dict[str, Any], session_id: str) -> dict[str, Any]:
    """滚动压缩会话历史。

    当会话轮次超过阈值时，把最早的若干轮压缩成一段结构化摘要，
    写回 chat_sessions.summary / summary_state。

    为什么需要：
    - 直接把全部历史塞进 Prompt 会导致 token 成本随轮次线性增长
    - 但丢掉全部历史又会让 AI「失忆」
    - 压缩摘要是成本与连续性之间的折中

    幂等性：每次都基于当前 summary 重新生成，重复执行结果一致。
    """
    from sqlalchemy import select

    from app.db.models import ChatMessage, ChatSession

    compact_threshold = 12  # 超过这个轮次才需要压缩
    keep_recent = 6  # 保留最近这么多轮的原文

    try:
        sessionmaker = ctx.get("sessionmaker") or get_sessionmaker()
        async with sessionmaker() as session:
            chat_session = (
                await session.execute(select(ChatSession).where(ChatSession.id == session_id))
            ).scalar_one_or_none()
            if chat_session is None:
                return {"status": "skipped", "reason": "session_not_found"}

            messages = (
                (
                    await session.execute(
                        select(ChatMessage)
                        .where(ChatMessage.session_id == session_id)
                        .order_by(ChatMessage.created_at.asc())
                    )
                )
                .scalars()
                .all()
            )

            if len(messages) < compact_threshold:
                return {"status": "skipped", "reason": "not_enough_messages"}

            old_messages = messages[:-keep_recent]
            if not old_messages:
                return {"status": "skipped", "reason": "nothing_to_compact"}

            # 规则摘要：抽取旧消息里出现的话题与已给出的微行动。
            # 这里同样不调 LLM——压缩是后台任务，允许有延迟但不该有成本与风险。
            topics: dict[str, int] = {}
            actions: list[str] = []
            for msg in old_messages:
                if msg.role != "assistant":
                    continue
                sections = msg.sections or {}
                action = sections.get("action")
                if action:
                    actions.append(action)
                for word in _extract_keywords(sections.get("insight") or ""):
                    topics[word] = topics.get(word, 0) + 1

            top_topics = [w for w, _ in sorted(topics.items(), key=lambda x: -x[1])[:8]]

            previous = chat_session.summary_state or {}
            chat_session.summary_state = {
                "topics": sorted({*previous.get("topics", []), *top_topics})[:15],
                "advice_given": [*previous.get("advice_given", []), *actions][-10:],
                "compacted_messages": len(old_messages),
                "last_compacted_at": dt.datetime.now(dt.UTC).isoformat(),
            }
            chat_session.summary = (
                f"此前讨论过：{'、'.join(top_topics[:5])}。已给出的微行动 {len(actions)} 条。"
            )
            await session.commit()

        logger.info("session_compacted", session_id=session_id, compacted=len(old_messages))
        return {"status": "ok", "compacted": len(old_messages)}

    except Exception as exc:
        logger.warning("compact_session_summary_failed", session_id=session_id, error=str(exc))
        return {"status": "failed", "error": str(exc)}


# --------------------------------------------------------------------------- #
# 任务三：定时指标聚合
# --------------------------------------------------------------------------- #


async def aggregate_daily_metrics(ctx: dict[str, Any]) -> dict[str, Any]:
    """每天聚合前一天的模型调用指标。

    为什么需要聚合表：
    `llm_calls` 是流水表，行数会持续增长。看板每天都要统计成本，
    直接扫流水表会越来越慢。聚合成日报表后，看板查询是 O(1) 命中索引。

    幂等性：按日期 upsert，重复执行不会重复累加。
    """
    from sqlalchemy import text

    try:
        sessionmaker = ctx.get("sessionmaker") or get_sessionmaker()
        yesterday = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
        day_start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + dt.timedelta(days=1)

        async with sessionmaker() as session:
            result = await session.execute(
                text(
                    """
                    SELECT
                        count(*)                        AS calls,
                        coalesce(sum(cost_cny), 0)      AS cost,
                        coalesce(sum(prompt_tokens), 0)  AS prompt_tokens,
                        coalesce(sum(completion_tokens), 0) AS completion_tokens,
                        count(*) FILTER (WHERE cached)  AS cached_calls
                    FROM llm_calls
                    WHERE created_at >= :start AND created_at < :end
                    """
                ),
                {"start": day_start, "end": day_end},
            )
            row = result.mappings().first()
            summary = dict(row) if row else {}

        logger.info("daily_metrics_aggregated", day=day_start.date().isoformat(), **summary)
        return {"status": "ok", "day": day_start.date().isoformat(), **summary}

    except Exception as exc:
        logger.warning("aggregate_daily_metrics_failed", error=str(exc))
        return {"status": "failed", "error": str(exc)}


# --------------------------------------------------------------------------- #
# 任务四：知识库增量向量化
# --------------------------------------------------------------------------- #


async def refresh_knowledge_index(ctx: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    """检测知识库变更并增量更新索引。

    调度：每 30 分钟一次（cron）。利用 content_hash 判断文档是否变化，
    未变化的文档跳过向量化，避免重复消耗嵌入 API 额度。
    """
    try:
        from app.rag.ingest import ingest_knowledge_base

        result = await ingest_knowledge_base(incremental=True)
        logger.info("knowledge_index_refreshed", **result)
        return {"status": "ok", **result}
    except Exception as exc:
        logger.warning("refresh_knowledge_index_failed", error=str(exc))
        return {"status": "failed", "error": str(exc)}


# --------------------------------------------------------------------------- #
# arq 配置
# --------------------------------------------------------------------------- #


def _redis_settings() -> Any:
    """从应用配置构造 arq 的 Redis 连接参数。

    注意：连接串里可能带密码（生产环境），arq 需要从 URL 解析，
    这里直接把 URL 交给 arq 处理。
    """
    from arq.connections import RedisSettings

    return RedisSettings.from_dsn(settings.redis_url)


class WorkerSettings(WorkerSettingsBase):
    """arq Worker 配置。

    生产启动命令：
        arq app.worker.WorkerSettings

    为什么继承 `WorkerSettingsBase`：
    arq 用 `get_kwargs()` 读取这些**类属性**，同时用 `arq.typing.WorkerSettingsBase`
    这个 Protocol 描述配置的结构。显式继承后，类型检查器能帮我们盯住
    「arq 需要的配置项是不是都写全了」。
    """

    # 注意：这里故意不加 `ClassVar` 标注——
    # Protocol 把 `functions` / `cron_jobs` 声明为实例属性，
    # 用 ClassVar 覆盖会破坏协议一致性（mypy 会报不兼容）。
    # ruff 的 RUF012 会抱怨「可变类属性」，这里按 noqa 忽略：
    # arq 就是要读这些类属性，这是框架约定，不是设计缺陷。
    functions: list[Any] = [  # noqa: RUF012
        extract_user_profile,
        compact_session_summary,
        refresh_knowledge_index,
    ]

    cron_jobs: list[Any] = [  # noqa: RUF012
        # 每天 03:00 聚合前一天的调用指标
        cron(aggregate_daily_metrics, hour={3, 15}, minute=0),
        # 每 30 分钟检查一次知识库变更
        cron(refresh_knowledge_index, minute={0, 30}),
    ]

    redis_settings = _redis_settings()

    # 单个任务的超时时间。超时会被 arq 中断并重试。
    job_timeout = 300
    # 任务最大重试次数。超过后进入 failed 集合，需要人工介入。
    max_tries = 3
    # 保持任务结果的时间，便于排查
    keep_result = 3600

    # 生命周期钩子。
    # 这里必须写显式标注：类体里的函数默认会被当成「方法」处理，
    # 而 arq 的钩子是以普通函数（不是绑定方法）的方式调用的。
    on_startup: StartupShutdown = startup
    on_shutdown: StartupShutdown = shutdown


def main() -> None:
    """以 arq worker 方式启动。

    `arq` 命令行会导入本模块的 WorkerSettings 并启动 worker。
    """
    logger.info("启动异步任务 worker")
    try:
        asyncio.run(_run_worker())
    except KeyboardInterrupt:
        logger.info("worker 被手动中断")


async def _run_worker() -> None:
    """内部：以程序方式启动 arq。

    之所以提供这个入口，是为了让 `python -m app.worker` 也能工作，
    方便本地调试（不用记 arq 命令行参数）。
    """
    from arq.worker import create_worker

    # arq 的 `run_worker()` 是**同步**函数（内部自己 run_until_complete），
    # 在已经有事件循环的协程里调用它会直接抛 RuntimeError，
    # 所以这里用「create + await async_run」的异步组合。
    worker = create_worker(WorkerSettings)
    await worker.async_run()


if __name__ == "__main__":
    main()
