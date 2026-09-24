from __future__ import annotations

import pytest

from src.providers.openai_compatible import (
    resolve_openai_compatible_credentials,
    validate_provider_api_base,
)


@pytest.mark.parametrize(
    ("provider_id", "api_base"),
    [
        ("openai", "https://api.openai.com/v1"),
        ("together", "https://api.together.xyz/v1"),
        ("groq", "https://api.groq.com/openai/v1"),
        ("deepseek", "https://api.deepseek.com/v1"),
        ("xai", "https://api.x.ai/v1"),
        ("cerebras", "https://api.cerebras.ai/v1"),
        ("perplexity", "https://api.perplexity.ai"),
        ("nvidia", "https://integrate.api.nvidia.com/v1"),
        ("openrouter", "https://openrouter.ai/api/v1"),
    ],
)
def test_known_provider_hosts_are_accepted(provider_id: str, api_base: str):
    assert validate_provider_api_base(provider_id, api_base) == api_base


@pytest.mark.parametrize(
    "api_base",
    [
        "http://api.openai.com/v1",
        "https://user:password@api.openai.com/v1",
        "https://api.openai.com/v1?token=secret",
        "https://api.openai.com/v1#fragment",
        "https://api.openai.com:8443/v1",
        "https://localhost/v1",
        "https://service.local/v1",
        "https://127.0.0.1/v1",
        "https://[::1]/v1",
        "https://good.example\\@evil.example/v1",
        "https://good.example/v1\\path",
        "https://good.example/\x7fpath",
    ],
)
def test_unsafe_url_components_are_rejected_even_with_custom_opt_in(api_base: str):
    with pytest.raises(ValueError):
        validate_provider_api_base("openai", api_base, allow_custom_api_base=True)


def test_unknown_host_requires_explicit_opt_in():
    with pytest.raises(ValueError, match="allow_custom_api_base"):
        validate_provider_api_base("openai", "https://gateway.example/v1")

    assert (
        validate_provider_api_base(
            "openai", "https://gateway.example/v1", allow_custom_api_base=True
        )
        == "https://gateway.example/v1"
    )


def test_custom_host_opt_in_is_passed_by_shared_provider_bootstrap():
    _, api_base, _ = resolve_openai_compatible_credentials(
        {"api_base": "https://gateway.example/v1", "allow_custom_api_base": True},
        provider_id="openai",
        default_api_base="https://api.openai.com/v1",
        default_api_key_env="OPENAI_API_KEY",
    )

    assert api_base == "https://gateway.example/v1"


def test_string_false_does_not_enable_custom_host_opt_in():
    with pytest.raises(ValueError, match="allow_custom_api_base"):
        resolve_openai_compatible_credentials(
            {"api_base": "https://gateway.example/v1", "allow_custom_api_base": "false"},
            provider_id="openai",
            default_api_base="https://api.openai.com/v1",
            default_api_key_env="OPENAI_API_KEY",
        )


def test_custom_path_is_supported_on_approved_host():
    assert validate_provider_api_base("openai", "https://api.openai.com/custom/path/") == (
        "https://api.openai.com/custom/path/"
    )
