"""应用入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    logger.info("app_starting", env=settings.app_env)
    yield
    await dispose_engine()
    logger.info("app_stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Lumina API",
        description="Lumina 拾光 · 积极心理学 AI 情绪教练",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    register_exception_handlers(app)
    register_routers(app)
    register_metrics(app)
    return app


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "app_error",
            path=request.url.path,
            code=exc.code,
            message=exc.message,
        )
        return JSONResponse(status_code=exc.status_code, content={"error": exc.to_payload()})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(x) for x in err.get("loc", ())[1:]),
                "reason": err.get("msg", ""),
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "请求参数不合法",
                    "details": details,
                }
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", path=request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL", "message": "服务内部错误"}},
        )


def register_routers(app: FastAPI) -> None:
    from app.api.v1 import chat, health, journal

    app.include_router(health.router, prefix="/healthz", tags=["health"])
    app.include_router(chat.router, prefix="/v1/chat", tags=["chat"])
    app.include_router(journal.router, prefix="/v1/journal", tags=["journal"])


def register_metrics(app: FastAPI) -> None:
    if not settings.prometheus_enabled:
        return
    try:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
        from starlette.responses import Response

        @app.get("/metrics", include_in_schema=False)
        async def _metrics() -> Response:
            return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    except ImportError:
        logger.warning("prometheus_not_available")


app = create_app()
