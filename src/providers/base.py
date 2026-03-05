from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
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
ProviderErrorCategorization = tuple[GatewayErrorCategory, bool]


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


@dataclass(slots=True, frozen=True)
class ProviderRuntimeState:
    discovery_available: bool
    inference_available: bool


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


def provider_error_from_error_payload(
    payload: object,
    *,
    categorize_error: Callable[[int | None, str | None, str], ProviderErrorCategorization],
    default_message: str = "provider stream error",
) -> ProviderError | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None

    message = str(error.get("message") or default_message)[:500]
    code = error.get("code")
    error_code = str(code) if code is not None else None

    status_code: int | None = None
    raw_status_code = error.get("status_code")
    if isinstance(raw_status_code, int):
        status_code = raw_status_code
    elif isinstance(raw_status_code, str) and raw_status_code.isdigit():
        status_code = int(raw_status_code)
    elif isinstance(code, int):
        status_code = code
    elif isinstance(code, str) and code.isdigit():
        status_code = int(code)

    category, retryable = categorize_error(status_code, error_code, message)
    if retryable:
        return ProviderRetryableError(
            message,
            category=category,
            status_code=status_code,
            error_code=error_code,
        )
    return ProviderFatalError(
        message,
        category=category,
        status_code=status_code,
        error_code=error_code,
    )


class ProviderAdapter(Protocol):
    name: str

    def runtime_state(self) -> ProviderRuntimeState: ...

    def categorize_error(
        self, status_code: int | None, error_code: str | None, message: str
    ) -> ProviderErrorCategorization: ...

    async def discover_models(self) -> list[dict[str, Any]]: ...

    async def chat_completions(self, request_body: dict[str, Any], model: str) -> ChatResult: ...

    async def stream_chat_completions(
        self, request_body: dict[str, Any], model: str
    ) -> StreamResult: ...

    async def probe(
        self, model: str, *, max_tokens: int = 1, timeout_seconds: int = 15
    ) -> ChatResult: ...
