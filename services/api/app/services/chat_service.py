"""对话用例编排。

职责边界：
- service 层负责用例级编排（查数据 → 调 Agent → 落库）
- 不碰 HTTP 对象，不自己处理异常响应
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.graph import get_graph
from app.agents.state import make_initial_state
from app.core.logging import get_logger
from app.rag.context import build_emotion_context
from app.schemas.chat import ChatRequest, ChatResponse

logger = get_logger(__name__)


class ChatService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def chat(self, user_id: str, request: ChatRequest) -> ChatResponse:
        """处理一次对话。"""
        history = await self._load_history(request.session_id, limit=6)
        emotion_context = await self._load_emotion_context(
            user_id, request.context.emotion_window_days
        )
        user_profile = await self._load_profile(user_id) if request.context.include_profile else {}

        state = make_initial_state(
            user_id=user_id,
            session_id=request.session_id,
            query=request.message,
            history=history,
            emotion_context=emotion_context,
            user_profile=user_profile,
        )

        graph = get_graph()
        result = await graph.ainvoke(state)

        message_id = f"m_{uuid.uuid4().hex[:12]}"
        await self._persist(request, result, message_id)

        return ChatResponse(
            message_id=message_id,
            content=result.get("answer", ""),
            sections=result.get("sections", {}),
            citations=result.get("citations", []),
            usage=result.get("usage", {}),
            cost_cny=result.get("cost_cny", 0.0),
            prompt_version=result.get("prompt_version", ""),
            safety=result.get("safety", {}),
            trace=result.get("trace", []),
        )

    # ------------------------------------------------------------------ #
    # 数据读取
    # ------------------------------------------------------------------ #
    async def _load_history(self, session_id: str, limit: int) -> list[dict[str, str]]:
        from sqlalchemy import text

        rows = (
            await self.session.execute(
                text(
                    """
                    SELECT role, content FROM chat_messages
                    WHERE session_id = :sid
                    ORDER BY created_at DESC LIMIT :lim
                    """
                ),
                {"sid": session_id, "lim": limit},
            )
        ).mappings().all()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    async def _load_emotion_context(self, user_id: str, days: int) -> dict[str, Any]:
        from sqlalchemy import text

        rows = (
            await self.session.execute(
                text(
                    """
                    SELECT emotion, intensity, tags, occurred_at
                    FROM journal_entries
                    WHERE user_id = :uid
                      AND occurred_at >= now() - (:days || ' days')::interval
                    ORDER BY occurred_at DESC
                    LIMIT 50
                    """
                ),
                {"uid": user_id, "days": days},
            )
        ).mappings().all()

        entries = [dict(r) for r in rows]
        context = {
            "days": days,
            "entry_count": len(entries),
            "text": build_emotion_context(entries=entries, days=days),
        }
        if entries:
            intensities = [e["intensity"] for e in entries if e.get("intensity")]
            context["avg_intensity"] = (
                round(sum(intensities) / len(intensities), 2) if intensities else 0.0
            )
            counts: dict[str, int] = {}
            for entry in entries:
                if entry.get("emotion"):
                    counts[entry["emotion"]] = counts.get(entry["emotion"], 0) + 1
            context["dominant_emotion"] = (
                max(counts, key=counts.get) if counts else ""
            )
        return context

    async def _load_profile(self, user_id: str) -> dict[str, Any]:
        from sqlalchemy import text

        row = (
            await self.session.execute(
                text("SELECT * FROM user_profiles WHERE user_id = :uid"),
                {"uid": user_id},
            )
        ).mappings().first()
        if not row:
            return {}
        return {
            "strengths": row.get("strengths") or [],
            "blind_spots": row.get("blind_spots") or [],
            "preferences": row.get("preferences") or [],
            "long_term_theme": row.get("long_term_theme") or "",
        }

    # ------------------------------------------------------------------ #
    # 落库
    # ------------------------------------------------------------------ #
    async def _persist(self, request: ChatRequest, result: dict, message_id: str) -> None:
        from sqlalchemy import text

        now = datetime.now(UTC)
        try:
            await self.session.execute(
                text(
                    """
                    INSERT INTO chat_messages
                        (id, session_id, user_id, role, content, sections, citations,
                         intent, safety_verdict, prompt_version, trace_id, latency_ms, created_at)
                    VALUES
                        (:id, :sid, :uid, 'user', :content, NULL, NULL, NULL, NULL, NULL, :trace, NULL, :now),
                        (:mid, :sid, :uid, 'assistant', :answer, CAST(:sections AS jsonb),
                         CAST(:citations AS jsonb), :intent, CAST(:safety AS jsonb),
                         :pver, :trace, :latency, :now)
                    """
                ),
                {
                    "id": f"m_{uuid.uuid4().hex[:12]}",
                    "sid": request.session_id,
                    "uid": result.get("user_id", ""),
                    "content": request.message,
                    "now": now,
                    "mid": message_id,
                    "answer": result.get("answer", ""),
                    "sections": _json(result.get("sections", {})),
                    "citations": _json(result.get("citations", [])),
                    "intent": result.get("intent", ""),
                    "safety": _json(result.get("safety", {})),
                    "pver": result.get("prompt_version", ""),
                    "trace": result.get("session_id", ""),
                    "latency": sum(t.get("duration_ms", 0) for t in result.get("trace", [])),
                },
            )
        except Exception as exc:
            # 落库失败不阻断用户响应
            logger.error("chat_persist_failed", error=str(exc))


def _json(data: Any) -> str:
    import json

    return json.dumps(data, ensure_ascii=False, default=str)
