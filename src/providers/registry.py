from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from src.providers.base import ProviderAdapter, ProviderErrorCategorization

if TYPE_CHECKING:
    from collections.abc import Mapping

    from src.config import Settings


@dataclass(slots=True, frozen=True)
class ProviderBootstrapContext:
    settings: Settings
    provider_id: str
    provider_config: Mapping[str, Any]


ProviderAdapterFactory = Callable[[ProviderBootstrapContext], ProviderAdapter]


@dataclass(slots=True, frozen=True)
class ProviderBootstrapDescriptor:
    provider_id: str
    factory: ProviderAdapterFactory


@dataclass(slots=True)
class RegisteredProvider:
    name: str
    adapter: ProviderAdapter
    discovery_enabled: bool
    inference_enabled: bool


def _openrouter_adapter_factory(context: ProviderBootstrapContext) -> ProviderAdapter:
    from src.providers.openrouter import OpenRouterAdapter

    settings = context.settings
    config = context.provider_config
    api_base = str(config.get("api_base", settings.openrouter_api_base)).strip()
    if not api_base:
        api_base = settings.openrouter_api_base

    dev_stub_enabled = bool(config.get("dev_stub_enabled", settings.openrouter_dev_stub_enabled))
    dev_stub_enabled = settings.app_env == "dev" and dev_stub_enabled

    return OpenRouterAdapter(
        api_key=settings.openrouter_api_key,
        api_base=api_base,
        dev_stub_enabled=dev_stub_enabled,
    )


_BUILTIN_BOOTSTRAP_DESCRIPTORS: dict[str, ProviderBootstrapDescriptor] = {
    "openrouter": ProviderBootstrapDescriptor(
        provider_id="openrouter",
        factory=_openrouter_adapter_factory,
    )
}


def _normalize_provider_id(provider_id: str) -> str:
    return provider_id.strip()


def _load_module_bootstrap_descriptor(provider_id: str) -> ProviderBootstrapDescriptor | None:
    normalized_provider_id = _normalize_provider_id(provider_id)
    if not normalized_provider_id:
        return None
    module_name = f"src.providers.{normalized_provider_id}"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return None
        raise

    descriptor = getattr(module, "PROVIDER_BOOTSTRAP_DESCRIPTOR", None)
    if isinstance(descriptor, ProviderBootstrapDescriptor):
        return ProviderBootstrapDescriptor(
            provider_id=normalized_provider_id,
            factory=descriptor.factory,
        )

    factory = getattr(module, "build_provider_adapter", None)
    if callable(factory):
        return ProviderBootstrapDescriptor(
            provider_id=normalized_provider_id,
            factory=cast(ProviderAdapterFactory, factory),
        )
    return None


def iter_provider_bootstrap_descriptors(settings: Settings) -> list[ProviderBootstrapDescriptor]:
    descriptors: list[ProviderBootstrapDescriptor] = []
    seen_provider_ids: set[str] = set()
    for provider_id in settings.known_provider_ids:
        normalized_provider_id = _normalize_provider_id(provider_id)
        if not normalized_provider_id or normalized_provider_id in seen_provider_ids:
            continue
        seen_provider_ids.add(normalized_provider_id)
        descriptor = _BUILTIN_BOOTSTRAP_DESCRIPTORS.get(normalized_provider_id)
        if descriptor is None:
            descriptor = _load_module_bootstrap_descriptor(normalized_provider_id)
        elif descriptor.provider_id != normalized_provider_id:
            descriptor = ProviderBootstrapDescriptor(
                provider_id=normalized_provider_id,
                factory=descriptor.factory,
            )
        if descriptor is not None:
            descriptors.append(descriptor)
    return descriptors


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, RegisteredProvider] = {}

    def register_configured(self, settings: Settings) -> None:
        self._providers = {}
        for descriptor in iter_provider_bootstrap_descriptors(settings):
            provider_id = descriptor.provider_id
            context = ProviderBootstrapContext(
                settings=settings,
                provider_id=provider_id,
                provider_config=settings.get_provider_bootstrap_config(provider_id),
            )
            self.register(
                descriptor.factory(context),
                name=provider_id,
                discovery_enabled=settings.is_provider_discovery_enabled(provider_id),
                inference_enabled=settings.is_provider_inference_enabled(provider_id),
            )

    def register(
        self,
        adapter: ProviderAdapter,
        *,
        name: str | None = None,
        discovery_enabled: bool = True,
        inference_enabled: bool = True,
    ) -> None:
        provider_name = (name or adapter.name).strip()
        if not provider_name:
            raise ValueError("provider name cannot be empty")
        self._providers[provider_name] = RegisteredProvider(
            name=provider_name,
            adapter=adapter,
            discovery_enabled=discovery_enabled,
            inference_enabled=inference_enabled,
        )

    def register_openrouter(
        self,
        api_key: str,
        api_base: str = "https://openrouter.ai/api/v1",
        *,
        dev_stub_enabled: bool = False,
        discovery_enabled: bool = True,
        inference_enabled: bool = True,
    ) -> None:
        from src.providers.openrouter import OpenRouterAdapter

        self.register(
            OpenRouterAdapter(
                api_key=api_key,
                api_base=api_base,
                dev_stub_enabled=dev_stub_enabled,
            ),
            name="openrouter",
            discovery_enabled=discovery_enabled,
            inference_enabled=inference_enabled,
        )

    def register_openai(
        self,
        api_key: str,
        api_base: str = "https://api.openai.com/v1",
        *,
        discovery_enabled: bool = True,
        inference_enabled: bool = True,
    ) -> None:
        from src.providers.openai import OpenAIAdapter

        self.register(
            OpenAIAdapter(api_key=api_key, api_base=api_base),
            name="openai",
            discovery_enabled=discovery_enabled,
            inference_enabled=inference_enabled,
        )

    def register_together(
        self,
        api_key: str,
        api_base: str = "https://api.together.xyz/v1",
        *,
        discovery_enabled: bool = True,
        inference_enabled: bool = True,
    ) -> None:
        from src.providers.together import TogetherAdapter

        self.register(
            TogetherAdapter(api_key=api_key, api_base=api_base),
            name="together",
            discovery_enabled=discovery_enabled,
            inference_enabled=inference_enabled,
        )

    def register_groq(
        self,
        api_key: str,
        api_base: str = "https://api.groq.com/openai/v1",
        *,
        discovery_enabled: bool = True,
        inference_enabled: bool = True,
    ) -> None:
        from src.providers.groq import GroqAdapter

        self.register(
            GroqAdapter(api_key=api_key, api_base=api_base),
            name="groq",
            discovery_enabled=discovery_enabled,
            inference_enabled=inference_enabled,
        )

    def register_deepseek(
        self,
        api_key: str,
        api_base: str = "https://api.deepseek.com/v1",
        *,
        discovery_enabled: bool = True,
        inference_enabled: bool = True,
    ) -> None:
        from src.providers.deepseek import DeepSeekAdapter

        self.register(
            DeepSeekAdapter(api_key=api_key, api_base=api_base),
            name="deepseek",
            discovery_enabled=discovery_enabled,
            inference_enabled=inference_enabled,
        )

    def register_xai(
        self,
        api_key: str,
        api_base: str = "https://api.x.ai/v1",
        *,
        discovery_enabled: bool = True,
        inference_enabled: bool = True,
    ) -> None:
        from src.providers.xai import XAIAdapter

        self.register(
            XAIAdapter(api_key=api_key, api_base=api_base),
            name="xai",
            discovery_enabled=discovery_enabled,
            inference_enabled=inference_enabled,
        )

    def register_cerebras(
        self,
        api_key: str,
        api_base: str = "https://api.cerebras.ai/v1",
        *,
        discovery_enabled: bool = True,
        inference_enabled: bool = True,
    ) -> None:
        from src.providers.cerebras import CerebrasAdapter

        self.register(
            CerebrasAdapter(api_key=api_key, api_base=api_base),
            name="cerebras",
            discovery_enabled=discovery_enabled,
            inference_enabled=inference_enabled,
        )

    def register_perplexity(
        self,
        api_key: str,
        api_base: str = "https://api.perplexity.ai",
        *,
        discovery_enabled: bool = True,
        inference_enabled: bool = True,
    ) -> None:
        from src.providers.perplexity import PerplexityAdapter

        self.register(
            PerplexityAdapter(api_key=api_key, api_base=api_base),
            name="perplexity",
            discovery_enabled=discovery_enabled,
            inference_enabled=inference_enabled,
        )

    def register_nvidia(
        self,
        api_key: str,
        api_base: str = "https://integrate.api.nvidia.com/v1",
        *,
        discovery_enabled: bool = True,
        inference_enabled: bool = True,
    ) -> None:
        from src.providers.nvidia import NvidiaAdapter

        self.register(
            NvidiaAdapter(api_key=api_key, api_base=api_base),
            name="nvidia",
            discovery_enabled=discovery_enabled,
            inference_enabled=inference_enabled,
        )

    def get_registered(self, name: str) -> RegisteredProvider:
        provider_name = name.strip()
        if not provider_name:
            raise KeyError(name)
        return self._providers[provider_name]

    def get(self, name: str) -> ProviderAdapter:
        provider = self.get_registered(name)
        if not provider.inference_enabled:
            raise KeyError(name)
        return provider.adapter

    def all(self) -> list[ProviderAdapter]:
        return [
            provider.adapter for provider in self._providers.values() if provider.discovery_enabled
        ]

    def all_registered(self) -> list[RegisteredProvider]:
        return list(self._providers.values())

    def categorize_error(
        self,
        name: str,
        status_code: int | None,
        error_code: str | None,
        message: str,
    ) -> ProviderErrorCategorization:
        return self.get_registered(name).adapter.categorize_error(status_code, error_code, message)
