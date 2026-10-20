"""Provider 适配层。

职责：把具体的模型服务（云端 OpenAI 兼容 API、本地 vLLM）统一成 `LLMRequest -> LLMResponse`。
上层不感知供应商差异。
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.llm.schemas import (
    Embedding,
    FatalProviderError,
    FinishReason,
    LLMRequest,
    LLMResponse,
    RetryableProviderError,
    Usage,
)

logger = get_logger(__name__)


class BaseProvider(ABC):
    """Provider 抽象。"""

    name: str = "base"

    def __init__(self, base_url: str, api_key: str, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @abstractmethod
    async def chat(self, request: LLMRequest) -> LLMResponse:
        """执行一次非流式对话。"""

    @abstractmethod
    async def embed(self, texts: list[str], model: str) -> Embedding:
        """批量向量化。"""

    async def aclose(self) -> None:  # noqa: B027
        """释放底层连接池。

        默认空实现（可选钩子）：大部分 provider 用短连接，无需额外释放；
        持有长连接的子类再覆写。故意不声明为 abstract——
        那样会强迫每个子类写一个无意义的空方法。
        """

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )


class OpenAICompatProvider(BaseProvider):
    """OpenAI 兼容协议适配器。

    覆盖通义千问、DeepSeek、智谱、vLLM、SGLang、Ollama 等绝大多数服务。
    """

    name = "openai-compat"

    async def chat(self, request: LLMRequest) -> LLMResponse:
        model = request.model or settings.llm_model
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.to_openai() for m in request.messages],
            "temperature": (
                request.temperature if request.temperature is not None else settings.llm_temperature
            ),
            "max_tokens": request.max_tokens or settings.llm_max_tokens,
            "stream": False,
        }
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.stop:
            payload["stop"] = request.stop
        if request.response_format:
            payload["response_format"] = request.response_format
        if request.seed is not None:
            payload["seed"] = request.seed

        started = time.perf_counter()
        try:
            async with self._client() as client:
                resp = await client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise RetryableProviderError(f"请求超时: {exc}") from exc
        except httpx.HTTPError as exc:
            raise RetryableProviderError(f"网络错误: {exc}") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)

        if resp.status_code == 429:
            raise RetryableProviderError("触发限流 (429)", code="rate_limited")
        if resp.status_code in (401, 403):
            raise FatalProviderError(f"鉴权失败 ({resp.status_code})", code="auth_failed")
        if resp.status_code >= 500:
            raise RetryableProviderError(f"服务端错误 ({resp.status_code})", code="server_error")
        if resp.status_code >= 400:
            raise FatalProviderError(f"请求被拒绝 ({resp.status_code}): {resp.text[:200]}")

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise RetryableProviderError("响应中无 choices", code="empty_choices")

        choice = choices[0]
        content = (choice.get("message") or {}).get("content") or ""
        usage_raw = data.get("usage") or {}

        return LLMResponse(
            content=content,
            model=data.get("model", model),
            provider=self.name,
            usage=Usage(
                prompt_tokens=int(usage_raw.get("prompt_tokens", 0)),
                completion_tokens=int(usage_raw.get("completion_tokens", 0)),
            ),
            finish_reason=_map_finish_reason(choice.get("finish_reason")),
            latency_ms=latency_ms,
            raw=data,
        )

    async def embed(self, texts: list[str], model: str) -> Embedding:
        if not texts:
            return Embedding(vectors=[], model=model)

        payload = {"model": model, "input": texts, "encoding_format": "float"}
        try:
            async with self._client() as client:
                resp = await client.post("/embeddings", json=payload)
        except httpx.HTTPError as exc:
            raise RetryableProviderError(f"向量化请求失败: {exc}") from exc

        if resp.status_code >= 400:
            raise RetryableProviderError(f"向量化失败 ({resp.status_code})")

        data = resp.json()
        items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
        vectors = [item["embedding"] for item in items]
        usage_raw = data.get("usage") or {}
        return Embedding(
            vectors=vectors,
            model=data.get("model", model),
            usage=Usage(
                prompt_tokens=int(usage_raw.get("prompt_tokens", 0)),
                completion_tokens=int(usage_raw.get("total_tokens", 0)),
            ),
        )

    async def stream_chat(self, request: LLMRequest) -> AsyncIterator[LLMResponse]:
        """流式对话。逐块 yield 增量内容。

        流式下的降级策略比非流式更严格：一旦已经开始向用户输出内容，
        就**不能切换模型**，否则会出现前后半段风格/质量不一致的拼接输出。
        因此流式只在「首个 chunk 之前」允许降级。
        """
        model = request.model or settings.llm_model
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.to_openai() for m in request.messages],
            "temperature": (
                request.temperature if request.temperature is not None else settings.llm_temperature
            ),
            "max_tokens": request.max_tokens or settings.llm_max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.response_format:
            payload["response_format"] = request.response_format

        started = time.perf_counter()
        ttft_ms: int | None = None
        completion_chars = 0
        usage = Usage()

        async with (
            self._client() as client,
            client.stream("POST", "/chat/completions", json=payload) as resp,
        ):
            if resp.status_code >= 400:
                await resp.aread()
                if resp.status_code in (401, 403):
                    raise FatalProviderError(f"鉴权失败 ({resp.status_code})")
                raise RetryableProviderError(f"流式请求失败 ({resp.status_code})")

            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                if chunk.get("usage"):
                    usage = Usage(
                        prompt_tokens=int(chunk["usage"].get("prompt_tokens", 0)),
                        completion_tokens=int(chunk["usage"].get("completion_tokens", 0)),
                    )
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content") or ""
                if not delta:
                    continue
                if ttft_ms is None:
                    ttft_ms = int((time.perf_counter() - started) * 1000)
                completion_chars += len(delta)
                yield LLMResponse(
                    content=delta,
                    model=model,
                    provider=self.name,
                    finish_reason=FinishReason.STOP,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    ttft_ms=ttft_ms,
                    usage=usage,
                )

        logger.debug("stream_done", provider=self.name, model=model, chars=completion_chars)


class LocalProvider(OpenAICompatProvider):
    """本地自部署模型（vLLM / SGLang / Ollama）。"""

    name = "local"

    def __init__(self, base_url: str, api_key: str, timeout: int = 60) -> None:
        super().__init__(base_url, api_key or "EMPTY", timeout)


class EmbeddingProvider(OpenAICompatProvider):
    """嵌入服务。默认走本地 TEI，也兼容云端。"""

    name = "embedding"


def _map_finish_reason(raw: str | None) -> FinishReason:
    mapping = {
        "stop": FinishReason.STOP,
        "length": FinishReason.LENGTH,
        "content_filter": FinishReason.CONTENT_FILTER,
        "tool_calls": FinishReason.TOOL_CALLS,
    }
    return mapping.get(raw or "", FinishReason.STOP)
