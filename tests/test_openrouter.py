from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from src.providers.base import (
    ChatResult,
    ProviderFatalError,
    ProviderRetryableError,
    ProviderRuntimeState,
    StreamResult,
)
from src.providers.openrouter import OpenRouterAdapter, categorize_openrouter_error
from src.providers.registry import ProviderRegistry
from src.proxy import _provider_error_from_event


def _response(status_code: int, *, json_body=None, content: bytes | None = None) -> httpx.Response:
    request = httpx.Request("GET", "https://openrouter.ai/api/v1/test")
    if json_body is not None:
        return httpx.Response(status_code, json=json_body, request=request)
    return httpx.Response(status_code, content=content or b"", request=request)


class _RetryingClient:
    attempts = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, headers=None, json=None):
        type(self).attempts += 1
        if type(self).attempts < 3:
            raise httpx.ReadTimeout("timed out")
        return _response(200, json_body={"ok": True})


class _Always429Client:
    attempts = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, headers=None, json=None):
        type(self).attempts += 1
        return _response(
            429,
            json_body={
                "error": {
                    "message": "rate limit exceeded",
                    "code": "rate_limit_exceeded",
                }
            },
        )


class _TimeoutStreamResponse:
    def __init__(self):
        self.closed = False

    async def aiter_lines(self):
        yield 'data: {"id":"chatcmpl-test","choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":null}]}'
        yield ""
        raise httpx.ReadTimeout("stream stalled")

    async def aclose(self):
        self.closed = True

    status_code = 200


class _StreamingClient:
    def __init__(self, response):
        self.response = response
        self.closed = False

    def build_request(self, method, url, headers=None, json=None):
        return httpx.Request(method, url, headers=headers, json=json)

    async def send(self, request, stream=False):
        assert stream is True
        return self.response

    async def aclose(self):
        self.closed = True


class _DummyProviderAdapter:
    name = "dummy"

    def runtime_state(self) -> ProviderRuntimeState:
        return ProviderRuntimeState(discovery_available=True, inference_available=True)

    def categorize_error(self, status_code, error_code, message):
        return "INVALID_REQUEST", False

    async def discover_models(self):
        return []

    async def chat_completions(self, request_body, model):
        return ChatResult(payload={"id": "dummy", "model": model, "request": request_body})

    async def stream_chat_completions(self, request_body, model):
        async def gen():
            yield b"data: [DONE]\n\n"

        return StreamResult(events=gen())

    async def probe(self, model, *, max_tokens=1, timeout_seconds=15):
        return ChatResult(payload={"id": "dummy-probe", "model": model})


def test_openrouter_runtime_state_reflects_api_key_or_dev_stub():
    assert OpenRouterAdapter(
        api_key="", dev_stub_enabled=False
    ).runtime_state() == ProviderRuntimeState(
        discovery_available=False,
        inference_available=False,
    )
    assert OpenRouterAdapter(api_key="test-key", dev_stub_enabled=False).runtime_state() == (
        ProviderRuntimeState(
            discovery_available=True,
            inference_available=True,
        )
    )
    assert OpenRouterAdapter(
        api_key="", dev_stub_enabled=True
    ).runtime_state() == ProviderRuntimeState(
        discovery_available=True,
        inference_available=True,
    )


def test_openrouter_error_from_payload_uses_provider_classifier():
    adapter = OpenRouterAdapter(api_key="test-key")
    retryable = adapter.error_from_payload(
        {"error": {"message": "rate limit exceeded", "code": "rate_limit_exceeded"}}
    )
    assert isinstance(retryable, ProviderRetryableError)
    assert retryable.category == "RATE_LIMITED"

    fatal = adapter.error_from_payload({"error": {"message": "invalid api key", "code": "401"}})
    assert isinstance(fatal, ProviderFatalError)
    assert fatal.category == "AUTH_ERROR"

    assert adapter.error_from_payload({"choices": []}) is None


def test_registry_supports_generic_provider_registration():
    registry = ProviderRegistry()
    adapter = _DummyProviderAdapter()

    registry.register(adapter, discovery_enabled=True, inference_enabled=False)

    assert registry.all() == [adapter]
    with pytest.raises(KeyError):
        registry.get("dummy")

    registered = registry.get_registered("dummy")
    assert registered.name == "dummy"
    assert registered.discovery_enabled is True
    assert registered.inference_enabled is False


def test_registry_categorize_error_delegates_to_provider_contract():
    registry = ProviderRegistry()
    registry.register(_DummyProviderAdapter())

    category, retryable = registry.categorize_error("dummy", None, "bad_request", "bad request")

    assert category == "INVALID_REQUEST"
    assert retryable is False


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
async def test_discover_models_defaults_streaming_and_max_tokens_from_row_when_parameters_missing(
    monkeypatch,
):
    adapter = OpenRouterAdapter(api_key="test-key")

    async def fake_request(self, method, path, *, json_body=None, timeout_seconds):
        return _response(
            200,
            json_body={
                "data": [
                    {
                        "id": "mistralai/mistral-small-3.1-24b-instruct:free",
                        "name": "Mistral Small",
                        "context_length": 65536,
                        "architecture": {"tokenizer": "Mistral"},
                        "max_completion_tokens": 2048,
                        "pricing": {"prompt": "0", "completion": "0"},
                    }
                ]
            },
        )

    monkeypatch.setattr(OpenRouterAdapter, "_request_with_retries", fake_request)

    models = await adapter.discover_models()

    assert models[0]["supports_streaming"] == 1
    assert models[0]["supports_tools"] == 0
    assert models[0]["supports_structured_output"] == 0
    assert models[0]["max_output_tokens"] == 2048


@pytest.mark.asyncio
async def test_discover_models_treats_streaming_as_supported_when_parameter_list_omits_stream(
    monkeypatch,
):
    adapter = OpenRouterAdapter(api_key="test-key")

    async def fake_request(self, method, path, *, json_body=None, timeout_seconds):
        return _response(
            200,
            json_body={
                "data": [
                    {
                        "id": "qwen/qwen3-next-80b-a3b-instruct:free",
                        "name": "Qwen 3 Next",
                        "context_length": 65536,
                        "architecture": {"tokenizer": "Qwen3"},
                        "supported_parameters": ["tools", "response_format"],
                        "pricing": {"prompt": "0", "completion": "0"},
                    }
                ]
            },
        )

    monkeypatch.setattr(OpenRouterAdapter, "_request_with_retries", fake_request)

    models = await adapter.discover_models()

    assert models[0]["supports_streaming"] == 1
    assert models[0]["supports_tools"] == 1
    assert models[0]["supports_structured_output"] == 1


@pytest.mark.asyncio
async def test_discover_models_with_real_key_does_not_synthesize_fallback_on_empty_result(
    monkeypatch,
):
    adapter = OpenRouterAdapter(api_key="test-key", dev_stub_enabled=True)

    async def fake_request(self, method, path, *, json_body=None, timeout_seconds):
        assert method == "GET"
        assert path == "/models"
        return _response(200, json_body={"data": []})

    monkeypatch.setattr(OpenRouterAdapter, "_request_with_retries", fake_request)

    models = await adapter.discover_models()

    assert models == []


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


@pytest.mark.asyncio
async def test_request_with_retries_retries_timeout_then_succeeds(monkeypatch):
    _RetryingClient.attempts = 0
    adapter = OpenRouterAdapter(api_key="test-key")

    monkeypatch.setattr("src.providers.openrouter.httpx.AsyncClient", _RetryingClient)

    response = await adapter._request_with_retries("GET", "/models", timeout_seconds=15)

    assert response.status_code == 200
    assert _RetryingClient.attempts == 3


@pytest.mark.asyncio
async def test_request_with_retries_raises_last_retryable_error_after_exhaustion(monkeypatch):
    _Always429Client.attempts = 0
    adapter = OpenRouterAdapter(api_key="test-key")

    monkeypatch.setattr("src.providers.openrouter.httpx.AsyncClient", _Always429Client)

    with pytest.raises(ProviderRetryableError) as exc_info:
        await adapter._request_with_retries("GET", "/models", timeout_seconds=15)

    assert exc_info.value.category == "RATE_LIMITED"
    assert _Always429Client.attempts == 3


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


def test_extract_error_details_handles_top_level_and_plain_text_payloads():
    adapter = OpenRouterAdapter(api_key="test-key")

    message, error_code = adapter._extract_error_details(
        b'{"message":"plain top-level error","code":"server_error"}'
    )
    assert message == "plain top-level error"
    assert error_code == "server_error"

    plain_message, plain_code = adapter._extract_error_details(b"upstream gateway timed out")
    assert plain_message == "upstream gateway timed out"
    assert plain_code is None


def test_categorize_openrouter_error_maps_common_string_codes_without_status():
    assert categorize_openrouter_error(None, "rate_limit_exceeded", "rate limit exceeded") == (
        "RATE_LIMITED",
        True,
    )
    assert categorize_openrouter_error(None, "invalid_api_key", "bad key") == (
        "AUTH_ERROR",
        False,
    )
    assert categorize_openrouter_error(None, "token_limit_exceeded", "token limit hit") == (
        "CONTEXT_EXCEEDED",
        True,
    )
    assert categorize_openrouter_error(None, "server_error", "upstream unavailable") == (
        "PROVIDER_UNAVAILABLE",
        True,
    )


def test_provider_error_from_event_uses_error_code_when_status_code_is_absent():
    adapter = OpenRouterAdapter(api_key="test-key")

    rate_limit_error = _provider_error_from_event(
        {"error": {"message": "rate limit exceeded", "code": "rate_limit_exceeded"}},
        categorize_error=adapter.categorize_error,
    )
    assert isinstance(rate_limit_error, ProviderRetryableError)
    assert rate_limit_error.category == "RATE_LIMITED"

    auth_error = _provider_error_from_event(
        {"error": {"message": "invalid api key", "code": "401"}},
        categorize_error=adapter.categorize_error,
    )
    assert auth_error is not None
    assert auth_error.category == "AUTH_ERROR"
    assert auth_error.retryable is False


@pytest.mark.asyncio
async def test_stream_chat_completions_converts_transport_timeout_and_closes_resources(
    monkeypatch,
):
    adapter = OpenRouterAdapter(api_key="test-key")
    response = _TimeoutStreamResponse()
    client = _StreamingClient(response)

    monkeypatch.setattr(
        "src.providers.openrouter.httpx.AsyncClient",
        lambda *args, **kwargs: client,
    )

    result = await adapter.stream_chat_completions(
        {"messages": [{"role": "user", "content": "hello"}]},
        model="openrouter/free",
    )

    first_event = await anext(result.events)
    assert b'"content":"hi"' in first_event

    with pytest.raises(ProviderRetryableError) as exc_info:
        await anext(result.events)

    assert exc_info.value.category == "PROVIDER_UNAVAILABLE"
    assert response.closed is True
    assert client.closed is True


@pytest.mark.asyncio
async def test_discover_models_without_api_key_returns_fallback_identity_only_in_dev_stub_mode():
    adapter = OpenRouterAdapter(api_key="", dev_stub_enabled=True)

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


@pytest.mark.asyncio
async def test_discover_models_without_api_key_raises_when_dev_stub_disabled():
    adapter = OpenRouterAdapter(api_key="", dev_stub_enabled=False)

    with pytest.raises(ProviderFatalError) as exc_info:
        await adapter.discover_models()

    assert exc_info.value.category == "AUTH_ERROR"


@pytest.mark.asyncio
async def test_chat_completions_without_api_key_raises_when_dev_stub_disabled():
    adapter = OpenRouterAdapter(api_key="", dev_stub_enabled=False)

    with pytest.raises(ProviderFatalError) as exc_info:
        await adapter.chat_completions(
            {"messages": [{"role": "user", "content": "hello"}]},
            model="openrouter/free",
        )

    assert exc_info.value.category == "AUTH_ERROR"


@pytest.mark.asyncio
async def test_chat_completions_without_api_key_returns_dev_stub_echo():
    adapter = OpenRouterAdapter(api_key="", dev_stub_enabled=True)

    result = await adapter.chat_completions(
        {"messages": [{"role": "user", "content": "hello"}]},
        model="openrouter/openrouter/free",
    )

    assert result.payload["choices"][0]["message"]["content"] == "Echo: hello"
    assert result.payload["model"] == "openrouter/openrouter/free"


@pytest.mark.asyncio
async def test_stream_chat_completions_without_api_key_raises_when_dev_stub_disabled():
    adapter = OpenRouterAdapter(api_key="", dev_stub_enabled=False)

    with pytest.raises(ProviderFatalError) as exc_info:
        await adapter.stream_chat_completions(
            {"messages": [{"role": "user", "content": "hello"}]},
            model="openrouter/free",
        )

    assert exc_info.value.category == "AUTH_ERROR"


@pytest.mark.asyncio
async def test_stream_chat_completions_without_api_key_returns_dev_stub_events():
    adapter = OpenRouterAdapter(api_key="", dev_stub_enabled=True)

    result = await adapter.stream_chat_completions(
        {"messages": [{"role": "user", "content": "hello"}]},
        model="openrouter/openrouter/free",
    )
    events = [event async for event in result.events]

    assert len(events) == 2
    assert b"Echo: hello" in events[0]
    assert events[1] == b"data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_probe_without_api_key_raises_when_dev_stub_disabled():
    adapter = OpenRouterAdapter(api_key="", dev_stub_enabled=False)

    with pytest.raises(ProviderFatalError) as exc_info:
        await adapter.probe("openrouter/free")

    assert exc_info.value.category == "AUTH_ERROR"


@pytest.mark.asyncio
async def test_probe_without_api_key_uses_dev_stub_chat_path():
    adapter = OpenRouterAdapter(api_key="", dev_stub_enabled=True)

    result = await adapter.probe("openrouter/openrouter/free")

    assert result.payload["choices"][0]["message"]["content"] == "Echo: ping"


def test_extract_error_details_falls_back_to_plain_text_for_non_json_body():
    adapter = OpenRouterAdapter(api_key="test-key")

    message, error_code = adapter._extract_error_details(b"upstream overloaded")

    assert message == "upstream overloaded"
    assert error_code is None


def test_extract_error_details_supports_top_level_message_and_non_string_code():
    adapter = OpenRouterAdapter(api_key="test-key")

    message, error_code = adapter._extract_error_details(b'{"message":"quota exceeded","code":429}')

    assert message == "quota exceeded"
    assert error_code == "429"


def test_raise_for_response_uses_default_message_for_empty_body():
    adapter = OpenRouterAdapter(api_key="test-key")

    with pytest.raises(ProviderRetryableError) as exc_info:
        adapter._raise_for_response(503, b"")

    assert str(exc_info.value) == "openrouter error (503)"
    assert exc_info.value.category == "PROVIDER_UNAVAILABLE"


def test_raise_for_response_maps_rate_limit_message_to_retryable_even_without_429():
    adapter = OpenRouterAdapter(api_key="test-key")

    with pytest.raises(ProviderRetryableError) as exc_info:
        adapter._raise_for_response(
            400,
            b'{"error":{"message":"rate limit exceeded for this account","code":"quota_exceeded"}}',
        )

    assert exc_info.value.category == "RATE_LIMITED"
    assert exc_info.value.status_code == 400
    assert exc_info.value.error_code == "quota_exceeded"


@pytest.mark.asyncio
async def test_request_with_retries_retries_retryable_response_then_succeeds(monkeypatch):
    adapter = OpenRouterAdapter(api_key="test-key")
    responses = [
        _response(503, json_body={"error": {"message": "temporary outage"}}),
        _response(200, json_body={"ok": True}),
    ]

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def request(self, method, url, headers=None, json=None):
            return responses.pop(0)

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = await adapter._request_with_retries("GET", "/models", timeout_seconds=15)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert responses == []


@pytest.mark.asyncio
async def test_request_with_retries_raises_retryable_timeout_after_exhausting_attempts(
    monkeypatch,
):
    adapter = OpenRouterAdapter(api_key="test-key")

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def request(self, method, url, headers=None, json=None):
            raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(ProviderRetryableError) as exc_info:
        await adapter._request_with_retries("GET", "/models", timeout_seconds=15)

    assert exc_info.value.category == "PROVIDER_UNAVAILABLE"
    assert str(exc_info.value) == "provider timeout"


@pytest.mark.asyncio
async def test_request_with_retries_raises_retryable_transport_error_after_exhausting_attempts(
    monkeypatch,
):
    adapter = OpenRouterAdapter(api_key="test-key")

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def request(self, method, url, headers=None, json=None):
            raise httpx.HTTPError("socket closed")

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(ProviderRetryableError) as exc_info:
        await adapter._request_with_retries("GET", "/models", timeout_seconds=15)

    assert exc_info.value.category == "PROVIDER_UNAVAILABLE"
    assert str(exc_info.value) == "provider transport error"
    assert isinstance(exc_info.value.__cause__, httpx.HTTPError)


@pytest.mark.asyncio
async def test_request_with_retries_does_not_retry_fatal_provider_error(monkeypatch):
    adapter = OpenRouterAdapter(api_key="test-key")
    attempts = 0

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
async def test_stream_chat_completions_merges_sse_lines_and_ignores_comments(monkeypatch):
    adapter = OpenRouterAdapter(api_key="test-key")
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
        model="openrouter/test",
    )
    events = [event async for event in result.events]

    assert events == [
        b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    assert client.requests[0][3]["stream"] is True
    assert response.closed is True
    assert client.closed is True


@pytest.mark.asyncio
async def test_stream_chat_completions_maps_stream_timeout_to_retryable(monkeypatch):
    adapter = OpenRouterAdapter(api_key="test-key")
    response = _FakeStreamingResponse(line_error=httpx.TimeoutException("timed out"))
    client = _FakeStreamingClient(response)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: client)

    result = await adapter.stream_chat_completions(
        {"messages": [{"role": "user", "content": "hello"}]},
        model="openrouter/test",
    )

    with pytest.raises(ProviderRetryableError) as exc_info:
        async for _event in result.events:
            pass

    assert exc_info.value.category == "PROVIDER_UNAVAILABLE"
    assert str(exc_info.value) == "provider stream timeout"
    assert response.closed is True
    assert client.closed is True


@pytest.mark.asyncio
async def test_stream_chat_completions_maps_stream_http_error_to_retryable(monkeypatch):
    adapter = OpenRouterAdapter(api_key="test-key")
    response = _FakeStreamingResponse(line_error=httpx.HTTPError("socket closed"))
    client = _FakeStreamingClient(response)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: client)

    result = await adapter.stream_chat_completions(
        {"messages": [{"role": "user", "content": "hello"}]},
        model="openrouter/test",
    )

    with pytest.raises(ProviderRetryableError) as exc_info:
        async for _event in result.events:
            pass

    assert exc_info.value.category == "PROVIDER_UNAVAILABLE"
    assert str(exc_info.value) == "provider stream transport error"
    assert response.closed is True
    assert client.closed is True


@pytest.mark.asyncio
async def test_stream_chat_completions_maps_error_response_before_streaming(monkeypatch):
    adapter = OpenRouterAdapter(api_key="test-key")
    response = _FakeStreamingResponse(
        status_code=503,
        content=b'{"error":{"message":"upstream unavailable","code":"server_error"}}',
    )
    client = _FakeStreamingClient(response)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: client)

    with pytest.raises(ProviderRetryableError) as exc_info:
        await adapter.stream_chat_completions(
            {"messages": [{"role": "user", "content": "hello"}]},
            model="openrouter/test",
        )

    assert exc_info.value.category == "PROVIDER_UNAVAILABLE"
    assert exc_info.value.status_code == 503
    assert response.closed is True
    assert client.closed is True
