from __future__ import annotations

import httpx
import pytest

from src.providers.base import ProviderFatalError, ProviderRetryableError
from src.providers.openrouter import OpenRouterAdapter


def _response(status_code: int, *, json_body=None, content: bytes | None = None) -> httpx.Response:
    request = httpx.Request("GET", "https://openrouter.ai/api/v1/test")
    if json_body is not None:
        return httpx.Response(status_code, json=json_body, request=request)
    return httpx.Response(status_code, content=content or b"", request=request)


@pytest.mark.asyncio
async def test_discover_models_maps_free_model_fields(monkeypatch):
    adapter = OpenRouterAdapter(api_key="test-key")

    async def fake_request(self, method, path, *, json_body=None, timeout_seconds):
        assert method == "GET"
        assert path == "/models"
        return _response(
            200,
            json_body={
                "data": [
                    {
                        "id": "meta-llama/llama-3.3-70b-instruct:free",
                        "name": "Llama 3.3 70B Instruct",
                        "context_length": 131072,
                        "architecture": {
                            "tokenizer": "Llama3",
                            "input_modalities": ["text", "image"],
                        },
                        "supported_parameters": ["tools", "stream", "response_format"],
                        "top_provider": {"max_completion_tokens": 4096},
                        "pricing": {"prompt": "0", "completion": "0"},
                    },
                    {
                        "id": "paid/model",
                        "name": "Paid Model",
                        "pricing": {"prompt": "0.1", "completion": "0.1"},
                    },
                ]
            },
        )

    monkeypatch.setattr(OpenRouterAdapter, "_request_with_retries", fake_request)

    models = await adapter.discover_models()

    assert len(models) == 1
    assert models[0]["id"] == "openrouter/meta-llama/llama-3.3-70b-instruct:free"
    assert models[0]["provider_model_id"] == "meta-llama/llama-3.3-70b-instruct:free"
    assert models[0]["tokenizer_family"] == "Llama3"
    assert models[0]["max_output_tokens"] == 4096
    assert models[0]["supports_tools"] == 1
    assert models[0]["supports_streaming"] == 1
    assert models[0]["supports_vision"] == 1
    assert models[0]["supports_structured_output"] == 1


@pytest.mark.asyncio
async def test_chat_completions_extracts_usage(monkeypatch):
    adapter = OpenRouterAdapter(api_key="test-key")

    async def fake_request(self, method, path, *, json_body=None, timeout_seconds):
        assert method == "POST"
        assert path == "/chat/completions"
        assert json_body["model"] == "meta-llama/llama-3.3-70b-instruct:free"
        return _response(
            200,
            json_body={
                "id": "chatcmpl-123",
                "object": "chat.completion",
                "model": "meta-llama/llama-3.3-70b-instruct:free",
                "choices": [],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                },
            },
        )

    monkeypatch.setattr(OpenRouterAdapter, "_request_with_retries", fake_request)

    result = await adapter.chat_completions(
        {"messages": [{"role": "user", "content": "hello"}]},
        model="meta-llama/llama-3.3-70b-instruct:free",
    )

    assert result.payload["id"] == "chatcmpl-123"
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 7
    assert result.total_tokens == 18
    assert result.latency_ms is not None
    assert result.ttfb_ms == result.latency_ms


def test_raise_for_response_maps_context_exceeded_to_retryable():
    adapter = OpenRouterAdapter(api_key="test-key")

    with pytest.raises(ProviderRetryableError) as exc_info:
        adapter._raise_for_response(
            400,
            b'{"error":{"message":"maximum context length exceeded","code":"context_length_exceeded"}}',
        )

    assert exc_info.value.category == "CONTEXT_EXCEEDED"
    assert exc_info.value.status_code == 400
    assert exc_info.value.error_code == "context_length_exceeded"


def test_raise_for_response_maps_auth_error_to_fatal():
    adapter = OpenRouterAdapter(api_key="test-key")

    with pytest.raises(ProviderFatalError) as exc_info:
        adapter._raise_for_response(
            401,
            b'{"error":{"message":"invalid api key","code":"invalid_api_key"}}',
        )

    assert exc_info.value.category == "AUTH_ERROR"
    assert exc_info.value.status_code == 401
    assert exc_info.value.error_code == "invalid_api_key"


@pytest.mark.asyncio
async def test_discover_models_without_api_key_returns_fallback_identity():
    adapter = OpenRouterAdapter(api_key="")

    models = await adapter.discover_models()

    assert models == [
        {
            "id": "openrouter/openrouter/free",
            "name": "openrouter/free",
            "provider_id": "openrouter",
            "provider_model_id": "openrouter/free",
            "provider_base_url": "https://openrouter.ai/api/v1",
            "provider_api_key_env": "OPENROUTER_API_KEY",
            "context_window": 4096,
            "max_output_tokens": None,
            "supports_tools": 1,
            "supports_streaming": 1,
            "supports_vision": 1,
            "supports_structured_output": 0,
            "supports_system_messages": 1,
            "openrouter_rank": 1,
            "chatbot_arena_elo": None,
            "open_llm_score": None,
            "is_healthy": 1,
        }
    ]
