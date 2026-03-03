from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class ProviderError(Exception):
    """Base provider error."""


class ProviderRetryableError(ProviderError):
    """Retryable provider-origin error."""


class ProviderFatalError(ProviderError):
    """Non-retryable provider-origin error."""


@dataclass(slots=True)
class ChatResult:
    payload: dict[str, Any]
    latency_ms: float | None = None
    ttfb_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class ProviderAdapter(Protocol):
    name: str

    async def discover_models(self) -> list[dict[str, Any]]: ...

    async def chat_completions(self, request_body: dict[str, Any], model: str) -> ChatResult: ...
