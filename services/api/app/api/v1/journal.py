"""情绪打卡 API。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.chat import JournalEntryCreate, JournalEntryOut

router = APIRouter()


@router.post("/entries", response_model=JournalEntryOut, status_code=201)
async def create_entry(
    payload: JournalEntryCreate,
    session: AsyncSession = Depends(get_db),
    user_id: str = Depends(lambda: "demo-user"),
) -> JournalEntryOut:
    """创建一条情绪记录。"""
    entry_id = f"j_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)
    occurred_at = payload.occurred_at or now.isoformat()

    await session.execute(
        text(
            """
            INSERT INTO journal_entries
                (id, user_id, emotion, intensity, tags, content, occurred_at, created_at, source)
            VALUES
                (:id, :uid, :emotion, :intensity, :tags, :content, :occurred_at, :now, 'manual')
            """
        ),
        {
            "id": entry_id,
            "uid": user_id,
            "emotion": payload.emotion,
            "intensity": payload.intensity,
            "tags": payload.tags,
            "content": payload.content,
            "occurred_at": occurred_at,
            "now": now,
        },
    )
    await session.commit()

    return JournalEntryOut(
        id=entry_id,
        emotion=payload.emotion,
        intensity=payload.intensity,
        tags=payload.tags,
        content=payload.content,
        occurred_at=occurred_at,
        created_at=now.isoformat(),
    )


@router.get("/timeline")
async def timeline(
    start: str | None = None,
    end: str | None = None,
    session: AsyncSession = Depends(get_db),
    user_id: str = Depends(lambda: "demo-user"),
) -> dict[str, Any]:
    """按日聚合的时间线 + 统计。"""
    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT
                    occurred_at::date AS day,
                    round(avg(intensity)::numeric, 2) AS avg_intensity,
                    count(*) AS entries,
                    mode() WITHIN GROUP (ORDER BY emotion) AS dominant_emotion
                FROM journal_entries
                WHERE user_id = :uid
                  AND (:start IS NULL OR occurred_at >= :start::timestamptz)
                  AND (:end   IS NULL OR occurred_at <= :end::timestamptz)
                GROUP BY 1
                ORDER BY 1 DESC
                LIMIT 180
                """
                ),
                {"uid": user_id, "start": start, "end": end},
            )
        )
        .mappings()
        .all()
    )

    stats = (
        (
            await session.execute(
                text(
                    """
                SELECT
                    count(DISTINCT occurred_at::date) AS active_days,
                    round(avg(intensity)::numeric, 2) AS avg_intensity_7d
                FROM journal_entries
                WHERE user_id = :uid AND occurred_at >= now() - interval '7 days'
                """
                ),
                {"uid": user_id},
            )
        )
        .mappings()
        .first()
    )

    return {
        "items": [dict(r) for r in rows],
        "stats": {
            "active_days": stats["active_days"] if stats else 0,
            "avg_intensity_7d": float(stats["avg_intensity_7d"])
            if stats and stats["avg_intensity_7d"]
            else 0.0,
        },
    }
