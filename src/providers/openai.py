from __future__ import annotations

from typing import TYPE_CHECKING

from src.providers.openai_compatible import (
    OpenAICompatibleAdapter,
    resolve_openai_compatible_credentials,
)

if TYPE_CHECKING:
    from src.providers.registry import ProviderBootstrapContext


class OpenAIAdapter(OpenAICompatibleAdapter):
    name = "openai"
    provider_api_key_env = "OPENAI_API_KEY"
    default_api_base = "https://api.openai.com/v1"


def build_provider_adapter(context: ProviderBootstrapContext) -> OpenAIAdapter:
    api_key, api_base, api_key_env = resolve_openai_compatible_credentials(
        context.provider_config,
        default_api_base=OpenAIAdapter.default_api_base,
        default_api_key_env=OpenAIAdapter.provider_api_key_env,
    )
    adapter = OpenAIAdapter(api_key=api_key, api_base=api_base)
    adapter.provider_api_key_env = api_key_env
    return adapter
