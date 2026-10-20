"""结构化日志。

生产环境使用 JSON 格式，方便 Loki/ELK 采集；
开发环境使用 console 格式，带颜色，可读性优先。
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.core.config import settings

_CONFIGURED = False


def configure_logging() -> None:
    """幂等地配置根 logger 与 structlog。"""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )
    # 降噪：第三方库的 INFO 噪音
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "elasticsearch"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    renderer: Any
    if settings.log_format == "json" or settings.is_production:
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso", utc=False),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """获取绑定了模块名的 logger。"""
    configure_logging()
    return structlog.get_logger(name)  # type: ignore[no-any-return]


def bind_request_context(**kwargs: Any) -> None:
    """把请求级上下文绑定到当前协程（如 trace_id、user_id）。"""
    structlog.contextvars.bind_contextvars(**kwargs)
