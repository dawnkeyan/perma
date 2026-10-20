"""数据库种子数据。

用途：为本地开发与演示提供**最小可用**的初始数据。
原则：
- 只写**虚构**数据，绝不写入任何真实用户数据
- 幂等：重复执行不会产生重复数据
- 不写入向量数据（向量由 `make kb-ingest` 负责）

用法：
    python -m app.db.seed              # 写入种子数据
    python -m app.db.seed --reset      # 先清空再写
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
import uuid

from sqlalchemy import delete, select

from app.core.logging import get_logger
from app.db.models import (
    BadCase,
    ChatSession,
    JournalEntry,
    TrackingEvent,
    User,
    UserProfile,
)
from app.db.session import dispose_engine, get_sessionmaker

logger = get_logger(__name__)


def _new_id(prefix: str) -> str:
    """生成带前缀的业务 ID，形如 `u_a1b2c3d4`。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> dt.datetime:
    """当前 UTC 时间。"""
    return dt.datetime.now(dt.timezone.utc)


async def seed_users() -> list[User]:
    """创建演示用户。

    openid 使用明显的假值（demo_ 前缀），避免与真实微信 openid 混淆。
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        existing = (
            await session.execute(
                select(User).where(User.wechat_openid.like("demo_%"))
            )
        ).scalars().all()
        if existing:
            logger.info(f"演示用户已存在（{len(existing)} 个），跳过")
            return list(existing)

        users = [
            User(
                id=_new_id("u"),
                wechat_openid="demo_openid_001",
                nickname="演示用户·小拾",
                avatar_url="",
                status="active",
                settings={"push_hour": 21, "privacy_mode": False},
            ),
            User(
                id=_new_id("u"),
                wechat_openid="demo_openid_002",
                nickname="演示用户·小光",
                avatar_url="",
                status="active",
                settings={"push_hour": 20, "privacy_mode": True},
            ),
        ]
        session.add_all(users)
        await session.commit()
        logger.info(f"创建演示用户 {len(users)} 个")
        return users


async def seed_profiles(users: list[User]) -> None:
    """创建用户画像（供对话上下文使用）。"""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        profiles = [
            UserProfile(
                user_id=users[0].id,
                strengths=["坚持", "好奇", "愿意求助"],
                blind_spots=["容易把休息让位给工作"],
                preferences=["喜欢具体的、可立刻执行的小建议"],
                long_term_theme="工作节奏与自我照顾的平衡",
            ),
            UserProfile(
                user_id=users[1].id,
                strengths=["表达力强", "共情力好"],
                blind_spots=["习惯先照顾别人"],
                preferences=["希望被认真倾听，而不是被立刻给方案"],
                long_term_theme="在人际关系中保持边界感",
            ),
        ]
        session.add_all(profiles)
        await session.commit()
    logger.info(f"创建用户画像 {len(profiles)} 条")


async def seed_journals(users: list[User]) -> None:
    """创建演示情绪日记。

    内容全部为虚构，涵盖不同情绪与强度，用于验证情绪趋势与上下文组装。
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        now = _now()
        entries = [
            JournalEntry(
                id=_new_id("j"),
                user_id=users[0].id,
                emotion="anxiety",
                intensity=4,
                tags=["work", "future"],
                content="这周的项目节点提前了，有点慌，晚上睡不太着。",
                occurred_at=now - dt.timedelta(days=1),
                source="manual",
            ),
            JournalEntry(
                id=_new_id("j"),
                user_id=users[0].id,
                emotion="calm",
                intensity=2,
                tags=["self"],
                content="今天午休时到楼下走了走，风很舒服。",
                occurred_at=now - dt.timedelta(days=2),
                source="manual",
            ),
            JournalEntry(
                id=_new_id("j"),
                user_id=users[0].id,
                emotion="gratitude",
                intensity=4,
                tags=["interpersonal"],
                content="同事帮我review了一遍方案，节省了我半天时间。",
                occurred_at=now - dt.timedelta(days=4),
                source="manual",
            ),
            JournalEntry(
                id=_new_id("j"),
                user_id=users[0].id,
                emotion="joy",
                intensity=3,
                tags=["self"],
                content="早起跑了三公里，出了一身汗，很爽。",
                occurred_at=now - dt.timedelta(days=6),
                source="manual",
            ),
        ]
        session.add_all(entries)
        await session.commit()
    logger.info(f"创建情绪日记 {len(entries)} 条")


async def seed_sessions(users: list[User]) -> None:
    """创建演示会话（空会话，不含消息内容）。"""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        sessions = [
            ChatSession(
                id=_new_id("s"),
                user_id=users[0].id,
                title="关于工作节奏的对话",
                turn_count=0,
            ),
            ChatSession(
                id=_new_id("s"),
                user_id=users[1].id,
                title="关于边界的对话",
                turn_count=0,
            ),
        ]
        session.add_all(sessions)
        await session.commit()
    logger.info(f"创建演示会话 {len(sessions)} 个")


async def seed_bad_cases() -> None:
    """创建一条演示 BadCase，用于演示质量闭环工作流。

    真实 BadCase 从 `bad_cases` 表或 Issue 录入，不通过种子脚本写入。
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        existing = (
            await session.execute(select(BadCase).where(BadCase.id.like("demo_%")))
        ).scalars().first()
        if existing:
            logger.info("演示 BadCase 已存在，跳过")
            return

        session.add(
            BadCase(
                id="demo_badcase_0001",
                priority="P2",
                category="prompt",
                source="manual",
                user_input="（示例）我最近没什么动力",
                ai_output="（示例）你应该早点休息。",
                auto_tags=["action_unclear", "imperative_tone"],
                status="open",
                note="演示数据：微行动不具体、语气偏说教",
            )
        )
        await session.commit()
    logger.info("创建演示 BadCase 1 条")


async def seed_tracking() -> None:
    """创建演示埋点事件（用于验证北极星指标统计逻辑）。"""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        now = _now()
        events = [
            TrackingEvent(
                id=_new_id("e"),
                event_name="micro_action_shown",
                properties={"action_type": "behavioral_activation"},
                platform="wechat",
                created_at=now - dt.timedelta(hours=2),
            ),
            TrackingEvent(
                id=_new_id("e"),
                event_name="micro_action_done",
                properties={"action_type": "behavioral_activation", "duration_min": 4},
                platform="wechat",
                created_at=now - dt.timedelta(hours=1),
            ),
        ]
        session.add_all(events)
        await session.commit()
    logger.info(f"创建埋点事件 {len(events)} 条")


async def reset_all() -> None:
    """清空所有业务表（仅本地开发使用）。"""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        for model in (
            TrackingEvent,
            BadCase,
            ChatSession,
            JournalEntry,
            UserProfile,
            User,
        ):
            await session.execute(delete(model))
        await session.commit()
    logger.info("已清空所有业务表")


async def seed(reset: bool = False) -> int:
    """种子数据主流程。"""
    try:
        if reset:
            await reset_all()
        users = await seed_users()
        if len(users) >= 2:
            await seed_profiles(users)
            await seed_journals(users)
            await seed_sessions(users)
        await seed_bad_cases()
        await seed_tracking()
    except Exception as exc:  # noqa: BLE001 - 顶层入口
        logger.error("种子数据写入失败", error=str(exc))
        return 1
    finally:
        await dispose_engine()

    logger.info("✅ 种子数据写入完成")
    logger.info("提示：向量数据请执行 `make kb-ingest` 导入知识库")
    return 0


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="写入 Lumina 演示种子数据")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="先清空所有业务表再写入（危险，仅本地开发使用）",
    )
    args = parser.parse_args()

    if args.reset:
        logger.warning("⚠️  --reset 会清空所有业务数据")

    return asyncio.run(seed(reset=args.reset))


if __name__ == "__main__":
    sys.exit(main())
