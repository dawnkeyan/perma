"""LLM 层的通用数据结构。

这些类型是网关与上层业务之间的唯一契约，
上层（agents / services）不允许直接接触 OpenAI SDK 的类型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Message(BaseModel):
    role: Role
    content: str

    def to_openai(self) -> dict[str, str]:
        return {"role": str(self.role), "content": self.content}


class FinishReason(StrEnum):
    STOP = "stop"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    TOOL_CALLS = "tool_calls"
    ERROR = "error"


@dataclass(slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(slots=True)
class LLMResponse:
    """网关对外的统一响应。"""

    content: str
    model: str
    provider: str
    usage: Usage = field(default_factory=Usage)
    finish_reason: FinishReason = FinishReason.STOP
    latency_ms: int = 0
    ttft_ms: int | None = None
    cached: bool = False
    attempts: int = 1
    raw: Any = None

    @property
    def cost_cny(self) -> float:
        from app.llm.pricing import estimate_cost

        return estimate_cost(self.provider, self.model, self.usage)


@dataclass(slots=True)
class LLMRequest:
    """网关入参。

    feature 用于路由与成本归因；temperature 不传则由网关按 feature 取默认值。
    """

    messages: list[Message]
    feature: str = "chat"
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    stop: list[str] | None = None
    response_format: dict[str, Any] | None = None
    seed: int | None = None
    stream: bool = False
    # 业务上下文，仅用于计量与日志，不参与模型请求
    user_id: str | None = None
    session_id: str | None = None
    prompt_version: str | None = None
    trace_id: str | None = None
    idempotency_key: str | None = None
    # 语义缓存命名空间：不同 feature 可独立开关
    cache_namespace: str | None = None
    use_semantic_cache: bool = True
    # 标记该调用为「安全硬编码路径」，用于成本统计区分
    metadata: dict[str, Any] = field(default_factory=dict)


class ProviderError(Exception):
    """provider 层异常基类。"""

    def __init__(self, message: str, *, code: str = "unknown", retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable


class RetryableProviderError(ProviderError):
    """可重试错误：429 / 5xx / 网络超时。"""

    def __init__(self, message: str, *, code: str = "retryable") -> None:
        super().__init__(message, code=code, retryable=True)


class FatalProviderError(ProviderError):
    """不可重试错误：401 / 400 参数错误。"""

    def __init__(self, message: str, *, code: str = "fatal") -> None:
        super().__init__(message, code=code, retryable=False)


class ParseError(ProviderError):
    """结构化输出解析失败。"""

    def __init__(self, message: str, *, raw: str = "") -> None:
        super().__init__(message, code="parse_error", retryable=True)
        self.raw = raw


@dataclass(slots=True)
class Embedding:
    """嵌入结果。"""

    vectors: list[list[float]]
    model: str
    usage: Usage = field(default_factory=Usage)


@dataclass(slots=True)
class RerankResult:
    """精排结果。"""

    index: int
    score: float
