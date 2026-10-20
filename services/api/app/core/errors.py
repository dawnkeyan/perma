"""领域异常与统一错误响应。

约定：业务代码只抛 AppError 子类，FastAPI 异常处理器负责转成 HTTP 响应。
禁止在业务代码里直接抛 HTTPException。
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """所有业务异常的基类。"""

    code: str = "INTERNAL"
    message: str = "服务内部错误"
    status_code: int = 500
    details: list[dict[str, Any]] | None = None

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: list[dict[str, Any]] | None = None,
        status_code: int | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.details = details if details is not None else self.details
        self.status_code = status_code or self.status_code
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


class ValidationError(AppError):
    code = "VALIDATION_ERROR"
    message = "请求参数不合法"
    status_code = 400


class UnauthorizedError(AppError):
    code = "UNAUTHORIZED"
    message = "登录状态已失效，请重新进入"
    status_code = 401


class ForbiddenError(AppError):
    code = "FORBIDDEN"
    message = "无权访问该资源"
    status_code = 403


class NotFoundError(AppError):
    code = "NOT_FOUND"
    message = "资源不存在"
    status_code = 404


class ConflictError(AppError):
    code = "CONFLICT"
    message = "资源状态冲突"
    status_code = 409


class RateLimitError(AppError):
    code = "RATE_LIMITED"
    message = "操作过于频繁，请稍后再试"
    status_code = 429

    def __init__(self, message: str | None = None, *, retry_after: int = 60) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ModelUnavailableError(AppError):
    """模型链路全降级仍无法产出结果。属于可重试错误。"""

    code = "MODEL_UNAVAILABLE"
    message = "服务暂时不可用，请稍后再试"
    status_code = 503


class SafetyInterruptError(AppError):
    """触发安全中断（危机/越界）。

    注意：安全场景下 **不允许降级**，必须直接中断整条 Agent 图。
    """

    code = "SAFETY_INTERRUPT"
    message = "请求已按安全策略处理"
    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(self.message)
        self.payload = payload
