"""对话 API。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.session import get_db
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService

router = APIRouter()


@router.post("/messages", response_model=ChatResponse)
async def create_message(
    payload: ChatRequest,
    session: AsyncSession = Depends(get_db),
    user_id: str = Depends(lambda: "demo-user"),
) -> ChatResponse:
    """非流式对话（便于自动化测试与内部调用）。"""
    service = ChatService(session)
    return await service.chat(user_id, payload)


@router.post("/stream")
async def stream_message(
    payload: ChatRequest,
    session: AsyncSession = Depends(get_db),
    user_id: str = Depends(lambda: "demo-user"),
) -> StreamingResponse:
    """流式对话（SSE）。

    事件类型：meta / delta / citation / action / done / error
    """
    service = ChatService(session)

    async def event_source() -> AsyncIterator[str]:
        try:
            response = await service.chat(user_id, payload)
        except AppError as exc:
            yield _sse("error", exc.to_payload())
            return
        except Exception as exc:  # noqa: BLE001
            yield _sse("error", {"code": "INTERNAL", "message": str(exc)})
            return

        yield _sse(
            "meta",
            {
                "message_id": response.message_id,
                "prompt_version": response.prompt_version,
                "usage": response.usage,
                "cost_cny": response.cost_cny,
            },
        )
        # 按段落输出，模拟增量流的效果
        for piece in _split_for_stream(response.content):
            yield _sse("delta", {"text": piece})
        for citation in response.citations:
            yield _sse("citation", citation)
        if response.sections.get("action"):
            yield _sse("action", {"text": response.sections["action"]})
        yield _sse("done", {"message_id": response.message_id})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 关闭 Nginx 缓冲，否则流式会失效
        },
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _split_for_stream(text: str, size: int = 24) -> list[str]:
    """把完整回答切成小块用于流式输出。"""
    if not text:
        return []
    return [text[i : i + size] for i in range(0, len(text), size)]
