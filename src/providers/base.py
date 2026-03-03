from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class ChatResult:
    payload: dict[str, Any]
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class ProviderRetryableError(RuntimeError):
    pass


class ProviderFatalError(RuntimeError):
    pass


class ProviderAdapter(Protocol):
    name: str

    def discover_models(self) -> list[dict[str, Any]]:
        ...

    def chat_completions(self, request_body: dict[str, Any], model: str) -> ChatResult:
        ...
