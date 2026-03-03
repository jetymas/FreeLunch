from __future__ import annotations

from src.providers.openrouter import OpenRouterAdapter


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, object] = {}

    def register_openrouter(self, api_key: str) -> None:
        self._providers["openrouter"] = OpenRouterAdapter(api_key=api_key)

    def get(self, name: str) -> object:
        return self._providers[name]

    def all(self) -> list[object]:
        return list(self._providers.values())
