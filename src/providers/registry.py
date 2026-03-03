from __future__ import annotations

from src.providers.base import ProviderAdapter
from src.providers.openrouter import OpenRouterAdapter


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ProviderAdapter] = {}

    def register_openrouter(self, api_key: str, api_base: str = "https://openrouter.ai/api/v1") -> None:
        self._providers["openrouter"] = OpenRouterAdapter(api_key=api_key, api_base=api_base)

    def get(self, name: str) -> ProviderAdapter:
        return self._providers[name]

    def all(self) -> list[ProviderAdapter]:
        return list(self._providers.values())
