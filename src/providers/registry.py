from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from .openrouter import OpenRouterAdapter


@dataclass(slots=True)
class ProviderRegistry:
    settings: Settings
    _providers: dict[str, Any] = field(init=False)

    def __post_init__(self) -> None:
        self._providers = {
            "openrouter": OpenRouterAdapter(
                api_key=self.settings.openrouter_api_key,
                base_url=self.settings.openrouter_base_url,
                timeout_s=self.settings.provider_timeout_s,
            )
        }

    def all(self):
        return self._providers.values()

    def get(self, name: str):
        return self._providers[name]
