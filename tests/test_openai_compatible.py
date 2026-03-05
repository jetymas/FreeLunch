from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from src.config import Settings
from src.providers.base import ProviderFatalError, ProviderRetryableError, ProviderRuntimeState
from src.providers.cerebras import CerebrasAdapter
from src.providers.deepseek import DeepSeekAdapter
from src.providers.groq import GroqAdapter
from src.providers.nvidia import NvidiaAdapter
from src.providers.openai import OpenAIAdapter
from src.providers.openai_compatible import (
    OpenAICompatibleAdapter,
    categorize_openai_compatible_error,
)
from src.providers.perplexity import PerplexityAdapter
from src.providers.registry import ProviderRegistry, iter_provider_bootstrap_descriptors
from src.providers.together import TogetherAdapter
from src.providers.xai import XAIAdapter


def _response(status_code: int, *, json_body=None, content: bytes | None = None) -> httpx.Response:
    request = httpx.Request("GET", "https://api.example.com/test")
    if json_body is not None:
        return httpx.Response(status_code, json=json_body, request=request)
    return httpx.Response(status_code, content=content or b"", request=request)


@pytest.mark.parametrize(
    ("status_code", "error_code", "message", "expected_category", "expected_retryable"),
    [
        (429, None, "rate limit", "RATE_LIMITED", True),
        (401, "invalid_api_key", "bad key", "AUTH_ERROR", False),
        (400, "context_length_exceeded", "too many tokens", "CONTEXT_EXCEEDED", True),
        (422, "invalid_request", "invalid payload", "INVALID_REQUEST", False),
        (503, "server_error", "upstream unavailable", "PROVIDER_UNAVAILABLE", True),
    ],
)
def test_categorize_openai_compatible_error_maps_common_cases(
    status_code: int | None,
    error_code: str | None,
    message: str,
    expected_category: str,
    expected_retryable: bool,
):
    category, retryable = categorize_openai_compatible_error(status_code, error_code, message)

    assert category == expected_category
    assert retryable is expected_retryable


@pytest.mark.parametrize(
    "adapter_type",
    [
        OpenAIAdapter,
        TogetherAdapter,
        GroqAdapter,
        DeepSeekAdapter,
        XAIAdapter,
        CerebrasAdapter,
        PerplexityAdapter,
        NvidiaAdapter,
    ],
)
@pytest.mark.parametrize(
    ("status_code", "error_code", "message", "expected_category", "expected_retryable"),
    [
        (429, "rate_limit_exceeded", "rate limit", "RATE_LIMITED", True),
        (401, "invalid_api_key", "bad key", "AUTH_ERROR", False),
        (400, "context_length_exceeded", "too many tokens", "CONTEXT_EXCEEDED", True),
        (422, "invalid_request", "invalid payload", "INVALID_REQUEST", False),
        (503, "server_error", "upstream unavailable", "PROVIDER_UNAVAILABLE", True),
    ],
)
def test_openai_compatible_wrappers_share_error_categorization_contract(
    adapter_type: type[OpenAICompatibleAdapter],
    status_code: int | None,
    error_code: str | None,
    message: str,
    expected_category: str,
    expected_retryable: bool,
):
    adapter = adapter_type(api_key="test-key")
    category, retryable = adapter.categorize_error(status_code, error_code, message)

    assert category == expected_category
    assert retryable is expected_retryable


@pytest.mark.parametrize(
    ("adapter_type", "expected_name", "expected_base", "expected_env"),
    [
        (OpenAIAdapter, "openai", "https://api.openai.com/v1", "OPENAI_API_KEY"),
        (TogetherAdapter, "together", "https://api.together.xyz/v1", "TOGETHER_API_KEY"),
        (GroqAdapter, "groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
        (DeepSeekAdapter, "deepseek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
        (XAIAdapter, "xai", "https://api.x.ai/v1", "XAI_API_KEY"),
        (CerebrasAdapter, "cerebras", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY"),
        (PerplexityAdapter, "perplexity", "https://api.perplexity.ai", "PERPLEXITY_API_KEY"),
        (NvidiaAdapter, "nvidia", "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY"),
    ],
)
def test_provider_wrappers_expose_identity_and_runtime_state(
    adapter_type: type[OpenAICompatibleAdapter],
    expected_name: str,
    expected_base: str,
    expected_env: str,
):
    assert adapter_type(api_key="").runtime_state() == ProviderRuntimeState(
        discovery_available=False,
        inference_available=False,
    )

    adapter = adapter_type(api_key="test-key")
    assert adapter.name == expected_name
    assert adapter.api_base == expected_base
    assert adapter.provider_api_key_env == expected_env
    assert adapter.runtime_state() == ProviderRuntimeState(
        discovery_available=True,
        inference_available=True,
    )


@pytest.mark.asyncio
async def test_discover_models_maps_openai_compatible_fields(monkeypatch):
    adapter = OpenAIAdapter(api_key="test-key")

    async def fake_request(self, method, path, *, json_body=None, timeout_seconds):
        assert method == "GET"
        assert path == "/models"
        return _response(
            200,
            json_body={
                "data": [
                    {
                        "id": "gpt-test",
                        "name": "GPT Test",
                        "context_window": 64000,
                        "max_output_tokens": 2048,
                        "tokenizer": "o200k_base",
                        "supports_streaming": True,
                        "supports_tools": 1,
                        "supports_vision": False,
                        "capabilities": ["response_format"],
                    }
                ]
            },
        )

    monkeypatch.setattr(OpenAIAdapter, "_request_with_retries", fake_request)

    models = await adapter.discover_models()

    assert len(models) == 1
    assert models[0]["id"] == "openai/gpt-test"
    assert models[0]["provider_id"] == "openai"
    assert models[0]["provider_model_id"] == "gpt-test"
    assert models[0]["provider_api_key_env"] == "OPENAI_API_KEY"
    assert models[0]["context_window"] == 64000
    assert models[0]["max_output_tokens"] == 2048
    assert models[0]["tokenizer_family"] == "o200k_base"
    assert models[0]["supports_streaming"] == 1
    assert models[0]["supports_tools"] == 1
    assert models[0]["supports_vision"] == 0
    assert models[0]["supports_structured_output"] == 1
    assert models[0]["provider_rank"] == 1


@pytest.mark.asyncio
async def test_discover_models_requires_api_key():
    adapter = OpenAIAdapter(api_key="")

    with pytest.raises(ProviderFatalError) as exc_info:
        await adapter.discover_models()

    assert exc_info.value.category == "AUTH_ERROR"


@pytest.mark.asyncio
async def test_chat_completions_extracts_usage(monkeypatch):
    adapter = OpenAIAdapter(api_key="test-key")

    async def fake_request(self, method, path, *, json_body=None, timeout_seconds):
        assert method == "POST"
        assert path == "/chat/completions"
        assert json_body["model"] == "gpt-test"
        return _response(
            200,
            json_body={
                "id": "chatcmpl-123",
                "model": "gpt-test",
                "choices": [],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )

    monkeypatch.setattr(OpenAIAdapter, "_request_with_retries", fake_request)

    result = await adapter.chat_completions(
        {"messages": [{"role": "user", "content": "hello"}]},
        model="gpt-test",
    )

    assert result.payload["id"] == "chatcmpl-123"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5
    assert result.total_tokens == 15
    assert result.ttfb_ms == result.latency_ms


class _FakeStreamingResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        lines: list[str] | None = None,
        content: bytes = b"",
        line_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self._lines = lines or []
        self.content = content
        self.closed = False
        self._line_error = line_error

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line
        if self._line_error is not None:
            raise self._line_error

    async def aread(self) -> bytes:
        return self.content

    async def aclose(self) -> None:
        self.closed = True


class _FakeStreamingClient:
    def __init__(self, response: _FakeStreamingResponse) -> None:
        self.response = response
        self.closed = False
        self.requests: list[tuple[str, str, dict[str, str] | None, dict[str, Any] | None]] = []

    def build_request(self, method, url, headers=None, json=None):
        self.requests.append((method, url, headers, json))
        return {"method": method, "url": url, "headers": headers, "json": json}

    async def send(self, request, stream=False):
        return self.response

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_stream_chat_completions_merges_events_and_ignores_comments(monkeypatch):
    adapter = OpenAIAdapter(api_key="test-key")
    response = _FakeStreamingResponse(
        lines=[
            ": ping",
            'data: {"choices":[{"delta":{"content":"hi"}}]}',
            "",
            "data: [DONE]",
            "",
        ]
    )
    client = _FakeStreamingClient(response)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: client)

    result = await adapter.stream_chat_completions(
        {"messages": [{"role": "user", "content": "hello"}]},
        model="gpt-test",
    )
    events = [event async for event in result.events]

    assert events == [
        b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    assert client.requests[0][3]["model"] == "gpt-test"
    assert client.requests[0][3]["stream"] is True
    assert response.closed is True
    assert client.closed is True


@pytest.mark.asyncio
async def test_stream_chat_completions_maps_error_response_before_streaming(monkeypatch):
    adapter = OpenAIAdapter(api_key="test-key")
    response = _FakeStreamingResponse(
        status_code=503,
        content=b'{"error":{"message":"upstream unavailable","code":"server_error"}}',
    )
    client = _FakeStreamingClient(response)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: client)

    with pytest.raises(ProviderRetryableError) as exc_info:
        await adapter.stream_chat_completions(
            {"messages": [{"role": "user", "content": "hello"}]},
            model="gpt-test",
        )

    assert exc_info.value.category == "PROVIDER_UNAVAILABLE"
    assert exc_info.value.status_code == 503
    assert response.closed is True
    assert client.closed is True


@pytest.mark.asyncio
async def test_request_with_retries_retries_timeout_then_succeeds(monkeypatch):
    attempts = 0
    adapter = OpenAIAdapter(api_key="test-key")

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def request(self, method, url, headers=None, json=None):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise httpx.TimeoutException("timed out")
            return _response(200, json_body={"ok": True})

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = await adapter._request_with_retries("GET", "/models", timeout_seconds=15)

    assert response.status_code == 200
    assert attempts == 3


@pytest.mark.asyncio
async def test_request_with_retries_does_not_retry_fatal_provider_error(monkeypatch):
    attempts = 0
    adapter = OpenAIAdapter(api_key="test-key")

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def request(self, method, url, headers=None, json=None):
            nonlocal attempts
            attempts += 1
            return _response(
                401,
                json_body={"error": {"message": "invalid api key", "code": "invalid_api_key"}},
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(ProviderFatalError) as exc_info:
        await adapter._request_with_retries("GET", "/models", timeout_seconds=15)

    assert exc_info.value.category == "AUTH_ERROR"
    assert attempts == 1


@pytest.mark.parametrize(
    ("register_fn_name", "expected_name", "expected_type"),
    [
        ("register_openai", "openai", OpenAIAdapter),
        ("register_together", "together", TogetherAdapter),
        ("register_groq", "groq", GroqAdapter),
        ("register_deepseek", "deepseek", DeepSeekAdapter),
        ("register_xai", "xai", XAIAdapter),
        ("register_cerebras", "cerebras", CerebrasAdapter),
        ("register_perplexity", "perplexity", PerplexityAdapter),
        ("register_nvidia", "nvidia", NvidiaAdapter),
    ],
)
def test_registry_openai_compatible_helpers_register_provider_adapters(
    register_fn_name: str,
    expected_name: str,
    expected_type: type[OpenAICompatibleAdapter],
):
    registry = ProviderRegistry()
    register_fn: Callable[..., Any] = getattr(registry, register_fn_name)

    register_fn(
        api_key="test-key",
        discovery_enabled=True,
        inference_enabled=False,
    )

    registered = registry.get_registered(expected_name)
    assert registered.name == expected_name
    assert registered.discovery_enabled is True
    assert registered.inference_enabled is False
    assert isinstance(registered.adapter, expected_type)


def test_registry_register_configured_loads_openai_compatible_module_factories(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("TOGETHER_API_KEY", "together-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("XAI_CUSTOM_KEY", "xai-key")
    monkeypatch.setenv("CEREBRAS_API_KEY", "cerebras-key")
    monkeypatch.setenv("PERPLEXITY_API_KEY", "perplexity-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvidia-key")

    provider_ids = (
        "openai",
        "together",
        "groq",
        "deepseek",
        "xai",
        "cerebras",
        "perplexity",
        "nvidia",
    )
    provider_enabled = {provider_id: True for provider_id in provider_ids}
    provider_enabled["openrouter"] = False
    provider_bootstrap_config = {
        provider_id: {} for provider_id in provider_ids
    }
    provider_bootstrap_config["xai"] = {
        "api_key_env": "XAI_CUSTOM_KEY",
        "api_base": "https://custom.x.ai/v1",
    }
    provider_bootstrap_config["openrouter"] = {}

    settings = Settings(
        providers_enabled=provider_ids,
        provider_enabled=provider_enabled,
        provider_discovery_enabled=dict(provider_enabled),
        provider_inference_enabled=dict(provider_enabled),
        openrouter_enabled=False,
        openrouter_discovery_enabled=False,
        openrouter_inference_enabled=False,
        provider_bootstrap_config=provider_bootstrap_config,
    )

    registry = ProviderRegistry()
    registry.register_configured(settings)

    assert isinstance(registry.get_registered("openai").adapter, OpenAIAdapter)
    assert isinstance(registry.get_registered("together").adapter, TogetherAdapter)
    assert isinstance(registry.get_registered("groq").adapter, GroqAdapter)
    assert isinstance(registry.get_registered("deepseek").adapter, DeepSeekAdapter)
    assert isinstance(registry.get_registered("xai").adapter, XAIAdapter)
    assert isinstance(registry.get_registered("cerebras").adapter, CerebrasAdapter)
    assert isinstance(registry.get_registered("perplexity").adapter, PerplexityAdapter)
    assert isinstance(registry.get_registered("nvidia").adapter, NvidiaAdapter)

    xai_adapter = registry.get_registered("xai").adapter
    assert isinstance(xai_adapter, XAIAdapter)
    assert xai_adapter.api_base == "https://custom.x.ai/v1"
    assert xai_adapter.provider_api_key_env == "XAI_CUSTOM_KEY"


def test_iter_provider_bootstrap_descriptors_skips_unknown_provider_modules():
    settings = Settings(
        providers_enabled=("openrouter", "missingprovider"),
        provider_enabled={"openrouter": True, "missingprovider": True},
        provider_discovery_enabled={"openrouter": True, "missingprovider": True},
        provider_inference_enabled={"openrouter": True, "missingprovider": True},
        provider_bootstrap_config={"openrouter": {}, "missingprovider": {}},
    )

    descriptors = iter_provider_bootstrap_descriptors(settings)
    provider_ids = {descriptor.provider_id for descriptor in descriptors}

    assert "openrouter" in provider_ids
    assert "missingprovider" not in provider_ids
