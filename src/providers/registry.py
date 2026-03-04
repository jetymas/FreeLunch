from __future__ import annotations

from dataclasses import dataclass

from src.providers.base import ProviderAdapter
from src.providers.openrouter import OpenRouterAdapter


@dataclass(slots=True)
class RegisteredProvider:
    adapter: ProviderAdapter
    discovery_enabled: bool
    inference_enabled: bool


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, RegisteredProvider] = {}

    def register_openrouter(
        self,
        api_key: str,
        api_base: str = "https://openrouter.ai/api/v1",
        *,
        dev_stub_enabled: bool = False,
        discovery_enabled: bool = True,
        inference_enabled: bool = True,
    ) -> None:
        self._providers["openrouter"] = RegisteredProvider(
            adapter=OpenRouterAdapter(
                api_key=api_key,
                api_base=api_base,
                dev_stub_enabled=dev_stub_enabled,
            ),
            discovery_enabled=discovery_enabled,
            inference_enabled=inference_enabled,
        )

    def get(self, name: str) -> ProviderAdapter:
        provider = self._providers[name]
        if not provider.inference_enabled:
            raise KeyError(name)
        return provider.adapter

    def all(self) -> list[ProviderAdapter]:
        return [
            provider.adapter for provider in self._providers.values() if provider.discovery_enabled
        ]
