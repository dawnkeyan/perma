"""模型网关。

对上层提供统一入口，屏蔽：多供应商差异、重试、熔断、降级、缓存、计量、成本核算。

设计要点
--------
1. **降级链**：主模型 -> 备用模型 -> 本地模型 -> 规则兜底。任何情况下不返回空响应。
2. **熔断**：滑动窗口错误率超阈值后跳过该 provider，避免雪崩。
3. **语义缓存**：按 feature 命名空间隔离，可单独开关。
4. **计量**：每次调用写 `llm_calls`，支撑成本归因与效果分析。
5. **幂等**：相同 idempotency_key 直接复用上次结果，避免重复计费。
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from app.core.config import settings
from app.core.errors import ModelUnavailableError
from app.core.logging import get_logger
from app.llm.pricing import estimate_cost
from app.llm.provider import (
    BaseProvider,
    EmbeddingProvider,
    FatalProviderError,
    LocalProvider,
    OpenAICompatProvider,
)
from app.llm.schemas import (
    Embedding,
    LLMRequest,
    LLMResponse,
    Message,
    ParseError,
    ProviderError,
    RetryableProviderError,
    Usage,
)

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


# --------------------------------------------------------------------------- #
# 熔断器
# --------------------------------------------------------------------------- #
@dataclass
class CircuitBreaker:
    """滑动窗口熔断器。

    连续失败达到阈值 -> 打开熔断（open），冷却期内直接跳过该 provider；
    冷却结束后进入半开（half_open），放行一次探测请求。
    """

    name: str
    failure_threshold: int = 5
    window_seconds: float = 60.0
    cooldown_seconds: float = 30.0

    failures: deque[float] = field(default_factory=deque)
    opened_at: float | None = None
    half_open: bool = False

    @property
    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if time.monotonic() - self.opened_at >= self.cooldown_seconds:
            # 冷却结束 -> 半开，放行一次探测
            self.opened_at = None
            self.half_open = True
            return False
        return True

    def record_success(self) -> None:
        self.failures.clear()
        self.opened_at = None
        self.half_open = False

    def record_failure(self) -> None:
        now = time.monotonic()
        self.failures.append(now)
        while self.failures and now - self.failures[0] > self.window_seconds:
            self.failures.popleft()
        if len(self.failures) >= self.failure_threshold:
            self.opened_at = now
            logger.warning("circuit_open", provider=self.name, failures=len(self.failures))

    def state(self) -> str:
        if self.opened_at is not None:
            return "open"
        return "half_open" if self.half_open else "closed"


# --------------------------------------------------------------------------- #
# 缓存接口
# --------------------------------------------------------------------------- #
class Cache(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int) -> None: ...
    async def get_by_vector(
        self, namespace: str, vector: list[float]
    ) -> tuple[str, float] | None: ...
    async def set_by_vector(
        self, namespace: str, vector: list[float], value: str, ttl: int
    ) -> None: ...


class NullCache:
    """禁用缓存时的空实现。

    参数全部保留但不使用：这里必须与 `Cache` Protocol 的签名一致
    （连参数名都要一致，否则 mypy 会判定为不兼容实现）。
    """

    async def get(self, key: str) -> str | None:  # noqa: ARG002
        return None

    async def set(self, key: str, value: str, ttl: int) -> None:  # noqa: ARG002
        return None

    async def get_by_vector(
        self,
        namespace: str,  # noqa: ARG002
        vector: list[float],  # noqa: ARG002
    ) -> tuple[str, float] | None:
        return None

    async def set_by_vector(
        self,
        namespace: str,  # noqa: ARG002
        vector: list[float],  # noqa: ARG002
        value: str,  # noqa: ARG002
        ttl: int,  # noqa: ARG002
    ) -> None:
        return None


# --------------------------------------------------------------------------- #
# 计量器
# --------------------------------------------------------------------------- #
class Meter:
    """调用计量与成本核算。

    默认实现只写日志，生产环境可注入 DB 写入器。
    之所以做成可注入：单元测试不应依赖数据库。
    """

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(
        self,
        request: LLMRequest,
        response: LLMResponse,
        *,
        status: str = "ok",
        error_code: str | None = None,
    ) -> None:
        cost = estimate_cost(response.provider, response.model, response.usage)
        row = {
            "trace_id": request.trace_id,
            "user_id": request.user_id,
            "feature": request.feature,
            "provider": response.provider,
            "model": response.model,
            "prompt_version": request.prompt_version,
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "cached": response.cached,
            "latency_ms": response.latency_ms,
            "ttft_ms": response.ttft_ms,
            "status": status,
            "error_code": error_code,
            "cost_cny": cost,
            "created_at": datetime_now(),
        }
        self.records.append(row)
        logger.info(
            "llm_call",
            feature=request.feature,
            model=response.model,
            provider=response.provider,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            cost_cny=cost,
            latency_ms=response.latency_ms,
            cached=response.cached,
            status=status,
        )

    def total_cost(self) -> float:
        return round(sum(float(r["cost_cny"]) for r in self.records), 6)


def datetime_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------- #
# 网关
# --------------------------------------------------------------------------- #
class LLMGateway:
    """模型网关主类。"""

    def __init__(
        self,
        providers: list[BaseProvider] | None = None,
        cache: Cache | None = None,
        meter: Meter | None = None,
    ) -> None:
        self.providers: list[BaseProvider] = providers or self._default_providers()
        self.embedding_provider: BaseProvider = EmbeddingProvider(
            settings.embedding_base_url,
            settings.embedding_api_key,
            timeout=60,
        )
        self.cache: Cache = cache or NullCache()
        self.meter: Meter = meter or Meter()
        self.breakers: dict[str, CircuitBreaker] = {
            p.name: CircuitBreaker(name=p.name) for p in self.providers
        }
        self._idempotency_store: dict[str, LLMResponse] = {}

    # ------------------------------------------------------------------ #
    # 装配
    # ------------------------------------------------------------------ #
    @staticmethod
    def _default_providers() -> list[BaseProvider]:
        providers: list[BaseProvider] = [
            OpenAICompatProvider(
                settings.llm_base_url,
                settings.llm_api_key,
                timeout=settings.llm_timeout,
            )
        ]
        if settings.llm_local_enabled:
            providers.append(
                LocalProvider(
                    settings.llm_local_base_url,
                    settings.llm_local_api_key,
                    timeout=settings.llm_timeout,
                )
            )
        return providers

    def _candidates(self, request: LLMRequest) -> list[BaseProvider]:
        """按降级链顺序返回候选 provider，跳过已熔断的。"""
        model_order = settings.effective_llm_fallbacks
        requested = request.model
        if requested:
            model_order = [requested, *[m for m in model_order if m != requested]]

        result: list[BaseProvider] = []
        for provider in self.providers:
            breaker = self.breakers.get(provider.name)
            if breaker and breaker.is_open:
                logger.warning("skip_circuit_open", provider=provider.name)
                continue
            result.append(provider)
        return result

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    async def chat(self, request: LLMRequest) -> LLMResponse:
        """执行对话（带降级链）。"""
        if request.idempotency_key and request.idempotency_key in self._idempotency_store:
            cached = self._idempotency_store[request.idempotency_key]
            cached.cached = True
            return cached

        cache_hit = await self._semantic_cache_get(request)
        if cache_hit is not None:
            return cache_hit

        last_error: Exception | None = None
        attempt = 0

        for provider in self._candidates(request):
            for model in self._models_for(provider, request):
                attempt += 1
                attempt_request = self._with_model(request, model)
                try:
                    response = await self._call_with_retry(provider, attempt_request)
                    response.attempts = attempt
                    await self.meter.record(request, response)
                    await self._semantic_cache_set(request, response)
                    if request.idempotency_key:
                        self._idempotency_store[request.idempotency_key] = response
                    provider_breaker = self.breakers.get(provider.name)
                    if provider_breaker:
                        provider_breaker.record_success()
                    return response
                except FatalProviderError as exc:
                    # 不可重试：换模型继续（可能是模型不支持某参数）
                    last_error = exc
                    logger.warning(
                        "provider_fatal", provider=provider.name, model=model, error=exc.message
                    )
                    await self.meter.record(
                        request,
                        _error_response(provider, model, exc),
                        status="error",
                        error_code=exc.code,
                    )
                    break
                except (TimeoutError, RetryableProviderError, ProviderError) as exc:
                    last_error = exc
                    logger.warning(
                        "provider_retryable",
                        provider=provider.name,
                        model=model,
                        error=str(exc),
                    )
                    provider_breaker = self.breakers.get(provider.name)
                    if provider_breaker:
                        provider_breaker.record_failure()
                    await self.meter.record(
                        request,
                        _error_response(provider, model, exc),
                        status="error",
                        # TimeoutError 不是 ProviderError，没有 code 字段；
                        # 退化成异常类名，监控里仍能区分失败来源。
                        error_code=(
                            exc.code if isinstance(exc, ProviderError) else type(exc).__name__
                        ),
                    )
                    break

        logger.error("llm_all_providers_failed", error=str(last_error))
        raise ModelUnavailableError(
            "模型服务暂时不可用，请稍后重试", code="MODEL_UNAVAILABLE"
        ) from last_error

    async def chat_structured(
        self, request: LLMRequest, schema: type[T], *, max_repair: int = 1
    ) -> T:
        """结构化输出。

        优先使用 provider 的 json_schema 模式；若返回内容无法解析，
        则发起一次「修复重试」（把错误信息回传给模型让它自己改）。
        """
        json_schema = schema.model_json_schema()
        req = LLMRequest(
            **{
                **request.__dict__,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": schema.__name__, "schema": json_schema},
                },
            }
        )
        response = await self.chat(req)
        try:
            return schema.model_validate_json(response.content)
        except Exception as exc:
            if max_repair <= 0:
                raise ParseError(f"结构化输出解析失败: {exc}", raw=response.content) from exc

            logger.warning("structured_repair", error=str(exc))
            repair_request = LLMRequest(
                messages=[
                    *request.messages,
                    Message(
                        role="user",
                        content=(
                            "上一次输出不符合 JSON schema，错误如下：\n"
                            f"{exc}\n\n"
                            f"请重新输出严格符合以下 schema 的 JSON，不要包含任何解释文字：\n"
                            f"{json.dumps(json_schema, ensure_ascii=False)}"
                        ),
                    ),
                ],
                feature=request.feature,
                model=request.model,
                temperature=0.0,
                max_tokens=request.max_tokens,
                user_id=request.user_id,
                prompt_version=request.prompt_version,
                trace_id=request.trace_id,
            )
            retry_response = await self.chat(repair_request)
            try:
                return schema.model_validate_json(retry_response.content)
            except Exception as exc2:
                raise ParseError(
                    f"结构化输出修复后仍失败: {exc2}", raw=retry_response.content
                ) from exc2

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMResponse]:
        """流式对话。

        与非流式的差异：**只在第一个 chunk 之前允许降级**。
        一旦开始向用户输出，后续失败只能中断并报错，不能换模型拼接。
        """
        for provider in self._candidates(request):
            for model in self._models_for(provider, request):
                stream_request = self._with_model(request, model)
                try:
                    produced = False
                    async for chunk in self._stream_once(provider, stream_request):
                        produced = True
                        yield chunk
                    if produced:
                        return
                except (RetryableProviderError, FatalProviderError) as exc:
                    logger.warning("stream_fallback", provider=provider.name, error=str(exc))
                    continue
        raise ModelUnavailableError("模型服务暂时不可用，请稍后重试")

    async def _stream_once(
        self, provider: BaseProvider, request: LLMRequest
    ) -> AsyncIterator[LLMResponse]:
        if not isinstance(provider, OpenAICompatProvider):
            raise FatalProviderError("该 provider 不支持流式")
        async for chunk in provider.stream_chat(request):
            yield chunk

    # ------------------------------------------------------------------ #
    # 嵌入
    # ------------------------------------------------------------------ #
    async def embed(self, texts: list[str], model: str | None = None) -> Embedding:
        return await self.embedding_provider.embed(texts, model or settings.embedding_model)

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _models_for(self, provider: BaseProvider, request: LLMRequest) -> list[str]:
        """一个 provider 可能承载多个模型（降级链）。"""
        if request.model:
            return [request.model]
        if provider.name == "local":
            return [settings.llm_local_model]
        chain = settings.effective_llm_fallbacks
        return chain[:1] if not settings.llm_fallback_models else chain

    @staticmethod
    def _with_model(request: LLMRequest, model: str) -> LLMRequest:
        if request.model == model:
            return request
        return LLMRequest(
            messages=request.messages,
            feature=request.feature,
            model=model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            top_p=request.top_p,
            stop=request.stop,
            response_format=request.response_format,
            seed=request.seed,
            stream=request.stream,
            user_id=request.user_id,
            session_id=request.session_id,
            prompt_version=request.prompt_version,
            trace_id=request.trace_id,
            idempotency_key=request.idempotency_key,
            cache_namespace=request.cache_namespace,
            use_semantic_cache=request.use_semantic_cache,
            metadata=request.metadata,
        )

    async def _call_with_retry(self, provider: BaseProvider, request: LLMRequest) -> LLMResponse:
        """单 provider 内的重试（指数退避 + 抖动）。"""
        max_retries = settings.llm_max_retries
        delay = 0.5
        last_exc: Exception | None = None

        for attempt in range(max_retries):
            try:
                return await provider.chat(request)
            except RetryableProviderError as exc:
                last_exc = exc
                if attempt == max_retries - 1:
                    break
                sleep_for = delay * (2**attempt) * (0.5 + random.random() / 2)
                logger.info(
                    "llm_retry",
                    provider=provider.name,
                    attempt=attempt + 1,
                    sleep=round(sleep_for, 2),
                )
                await asyncio.sleep(sleep_for)
                delay = min(delay * 2, 8.0)

        raise last_exc or RetryableProviderError("未知错误")

    # ------------------------------------------------------------------ #
    # 语义缓存
    # ------------------------------------------------------------------ #
    async def _semantic_cache_get(self, request: LLMRequest) -> LLMResponse | None:
        if not settings.semantic_cache_enabled or not request.use_semantic_cache:
            return None
        namespace = request.cache_namespace or request.feature
        try:
            embedding = await self.embed([_normalize_query(request.messages[-1].content)])
            hit = await self.cache.get_by_vector(namespace, embedding.vectors[0])
        except Exception as exc:  # 缓存故障不能影响主链路
            logger.warning("semantic_cache_get_failed", error=str(exc))
            return None

        if not hit:
            return None
        payload, score = hit
        if score < settings.semantic_cache_threshold:
            return None

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None

        logger.info("semantic_cache_hit", namespace=namespace, score=round(score, 4))
        return LLMResponse(
            content=data["content"],
            model=data.get("model", request.model or settings.llm_model),
            provider=data.get("provider", "cache"),
            usage=Usage(
                prompt_tokens=data.get("prompt_tokens", 0),
                completion_tokens=data.get("completion_tokens", 0),
            ),
            cached=True,
        )

    async def _semantic_cache_set(self, request: LLMRequest, response: LLMResponse) -> None:
        if not settings.semantic_cache_enabled or not request.use_semantic_cache:
            return
        if response.finish_reason.value == "length":
            # 被截断的回复不入缓存
            return
        namespace = request.cache_namespace or request.feature
        try:
            embedding = await self.embed([_normalize_query(request.messages[-1].content)])
            payload = json.dumps(
                {
                    "content": response.content,
                    "model": response.model,
                    "provider": response.provider,
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                },
                ensure_ascii=False,
            )
            await self.cache.set_by_vector(
                namespace, embedding.vectors[0], payload, settings.semantic_cache_ttl
            )
        except Exception as exc:
            logger.warning("semantic_cache_set_failed", error=str(exc))


def _normalize_query(text: str) -> str:
    """归一化：去空白、统一标点、去掉无意义的追问词。"""
    normalized = "".join(text.split())
    for noise in ("请问", "你好", "麻烦问一下", "我想问", "能不能"):
        normalized = normalized.replace(noise, "")
    return normalized.lower()


def _error_response(provider: BaseProvider, model: str, exc: Exception) -> LLMResponse:
    return LLMResponse(
        content="",
        model=model,
        provider=provider.name,
        usage=Usage(),
        finish_reason="error",  # type: ignore[arg-type]
        raw=str(exc),
    )


# 全局单例
_gateway: LLMGateway | None = None


def get_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway


def reset_gateway() -> None:
    """测试用：重置单例。"""
    global _gateway
    _gateway = None
