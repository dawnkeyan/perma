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
from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.rag.context import build_emotion_context
from app.schemas.chat import ChatRequest, ChatResponse
from app.tools.catalog import ToolSpec, get_tool

logger = get_logger(__name__)


class ChatService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def chat(self, user_id: str, request: ChatRequest) -> ChatResponse:
        """处理一次对话。"""
        tool = self._resolve_tool(request.tool_id)
        history = await self._load_history(request.session_id, limit=6)

        # 工具声明需要的上下文优先于客户端传入的设置——
        # 例如「心绪导航」需要至少 7 天的情绪记录才能「识别状态」，
        # 客户端传了 3 天也不能让它真的只看 3 天。
        window_days = request.context.emotion_window_days
        include_profile = request.context.include_profile
        if tool is not None:
            if "emotion_window" in tool.requires_context:
                window_days = max(window_days, 7)
            if "profile" in tool.requires_context:
                include_profile = True

        emotion_context = await self._load_emotion_context(user_id, window_days)
        user_profile = await self._load_profile(user_id) if include_profile else {}

        state = make_initial_state(
            user_id=user_id,
            session_id=request.session_id,
            query=request.message,
            history=history,
            emotion_context=emotion_context,
            user_profile=user_profile,
            tool_id=tool.id if tool else "",
            tool_focus=list(tool.focus) if tool else [],
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
    # 工具解析
    # ------------------------------------------------------------------ #
    @staticmethod
    def _resolve_tool(tool_id: str | None) -> ToolSpec | None:
        """解析工具标识。

        传了不存在的工具 id 必须报错而不是静默忽略：
        静默忽略会让客户端以为「工具已生效」，但实际检索侧重没注入，
        用户拿到的是一份没有策略匹配的结果——这是最难排查的一类问题。
        """
        if not tool_id:
            return None
        tool = get_tool(tool_id)
        if tool is None:
            raise ValidationError(f"未知的工具：{tool_id}")
        return tool

    # ------------------------------------------------------------------ #
    # 数据读取
    # ------------------------------------------------------------------ #
    async def _load_history(self, session_id: str, limit: int) -> list[dict[str, str]]:
        from sqlalchemy import text

        rows = (
            (
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
            )
            .mappings()
            .all()
        )
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    async def _load_emotion_context(self, user_id: str, days: int) -> dict[str, Any]:
        from sqlalchemy import text

        rows = (
            (
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
            )
            .mappings()
            .all()
        )

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
                max(counts, key=lambda name: counts[name]) if counts else ""
            )
        return context

    async def _load_profile(self, user_id: str) -> dict[str, Any]:
        from sqlalchemy import text

        row = (
            (
                await self.session.execute(
                    text("SELECT * FROM user_profiles WHERE user_id = :uid"),
                    {"uid": user_id},
                )
            )
            .mappings()
            .first()
        )
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
