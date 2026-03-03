from __future__ import annotations

from typing import Protocol


class ProviderAdapter(Protocol):
    name: str

    async def discover_models(self) -> list[dict]: ...

    async def chat_completion(self, payload: dict, model_name: str) -> dict: ...
