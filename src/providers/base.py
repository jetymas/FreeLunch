from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any, Literal, Protocol

GatewayErrorCategory = Literal[
    "RATE_LIMITED",
    "PROVIDER_UNAVAILABLE",
    "INVALID_REQUEST",
    "AUTH_ERROR",
    "CONTEXT_EXCEEDED",
]

RETRYABLE_ERROR_CATEGORIES: set[GatewayErrorCategory] = {
    "RATE_LIMITED",
    "PROVIDER_UNAVAILABLE",
    "CONTEXT_EXCEEDED",
}


class ProviderError(Exception):
    """Normalized provider-origin error."""

    def __init__(
        self,
        message: str,
        *,
        category: GatewayErrorCategory,
        retryable: bool,
        status_code: int | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.status_code = status_code
        self.error_code = error_code


class ProviderRetryableError(ProviderError):
    """Retryable provider-origin error."""

    def __init__(
        self,
        message: str,
        *,
        category: GatewayErrorCategory = "PROVIDER_UNAVAILABLE",
        status_code: int | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(
            message,
            category=category,
            retryable=True,
            status_code=status_code,
            error_code=error_code,
        )


class ProviderFatalError(ProviderError):
    """Non-retryable provider-origin error."""

    def __init__(
        self,
        message: str,
        *,
        category: GatewayErrorCategory = "INVALID_REQUEST",
        status_code: int | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(
            message,
            category=category,
            retryable=False,
            status_code=status_code,
            error_code=error_code,
        )


@dataclass(slots=True)
class ChatResult:
    payload: dict[str, Any]
    latency_ms: float | None = None
    ttfb_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(slots=True)
class StreamResult:
    events: AsyncGenerator[bytes, None]


class ProviderAdapter(Protocol):
    name: str

    async def discover_models(self) -> list[dict[str, Any]]: ...

    async def chat_completions(self, request_body: dict[str, Any], model: str) -> ChatResult: ...

    async def stream_chat_completions(
        self, request_body: dict[str, Any], model: str
    ) -> StreamResult: ...

    async def probe(
        self, model: str, *, max_tokens: int = 1, timeout_seconds: int = 15
    ) -> ChatResult: ...
