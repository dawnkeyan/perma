"""健康检查。

区分两种：
- `/healthz`：进程存活（liveness），只返回自身状态，用于 K8s livenessProbe
- `/readyz`：依赖就绪（readiness），探活 DB / Redis / 模型，用于 K8s readinessProbe
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import settings
from app.db.session import session_scope

router = APIRouter()


@router.get("")
async def healthz() -> dict[str, Any]:
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env}


@router.get("/ready")
async def readyz() -> dict[str, Any]:
    checks: dict[str, Any] = {}

    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"

    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
        await client.ping()
        await client.aclose()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"

    try:
        import httpx

        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{settings.llm_base_url}/models")
        checks["llm"] = "ok" if resp.status_code < 500 else "degraded"
    except Exception as exc:
        # 模型不可用不阻塞就绪：网关有降级链
        checks["llm"] = f"degraded: {exc}"

    ready = checks.get("database") == "ok"
    return {"status": "ok" if ready else "degraded", "checks": checks}
