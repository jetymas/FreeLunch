from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from src.providers.base import ProviderFatalError, ProviderRetryableError
from src.providers.openai import OpenAIAdapter
from src.providers.openai_compatible import (
    OpenAICompatibleAdapter,
    categorize_openai_compatible_error,
    resolve_openai_compatible_credentials,
)


def _response(status_code: int, *, json_body: Any = None, content: bytes | None = None) -> httpx.Response:
    request = httpx.Request("GET", "https://api.example.com/test")
    if json_body is not None:
        return httpx.Response(status_code, json=json_body, request=request)
    return httpx.Response(status_code, content=content or b"", request=request)


@pytest.mark.parametrize(
    ("status_code", "error_code", "message", "expected_category", "expected_retryable"),
    [
        (None, None, "unknown provider issue", "PROVIDER_UNAVAILABLE", True),
        (599, "other", "backend exploded", "PROVIDER_UNAVAILABLE", True),
        (418, None, "teapot", "INVALID_REQUEST", False),
    ],
)
def test_categorize_openai_compatible_error_covers_fallback_paths(
    status_code: int | None,
    error_code: str | None,
    message: str,
    expected_category: str,
    expected_retryable: bool,
):
    category, retryable = categorize_openai_compatible_error(status_code, error_code, message)

    assert category == expected_category
    assert retryable is expected_retryable


def test_resolve_openai_compatible_credentials_falls_back_to_config_api_key(monkeypatch):
    monkeypatch.delenv("CUSTOM_ENV", raising=False)

    api_key, api_base, api_key_env = resolve_openai_compatible_credentials(
        {
            "api_base": " https://custom.example/v1/ ",
            "api_key_env": " CUSTOM_ENV ",
            "api_key": " config-key ",
        },
        default_api_base="https://default.example/v1",
        default_api_key_env="DEFAULT_ENV",
    )

    assert api_key == "config-key"
    assert api_base == "https://custom.example/v1/"
    assert api_key_env == "CUSTOM_ENV"


def test_error_from_payload_delegates_and_returns_normalized_error():
    adapter = OpenAICompatibleAdapter(api_key="test-key")

    error = adapter.error_from_payload(
        {
            "error": {
                "message": "too many tokens",
                "code": "context_length_exceeded",
                "status_code": "400",
            }
        }
    )

    assert isinstance(error, ProviderRetryableError)
    assert error.category == "CONTEXT_EXCEEDED"
    assert error.status_code == 400
    assert error.error_code == "context_length_exceeded"


@pytest.mark.asyncio
async def test_discover_models_returns_empty_when_data_is_not_a_list(monkeypatch):
    adapter = OpenAICompatibleAdapter(api_key="test-key")

    async def fake_request(self, method, path, *, json_body=None, timeout_seconds):
        assert method == "GET"
        assert path == "/models"
        return _response(200, json_body={"data": {"id": "not-a-list"}})

    monkeypatch.setattr(OpenAICompatibleAdapter, "_request_with_retries", fake_request)

    models = await adapter.discover_models()

    assert models == []


@pytest.mark.asyncio
async def test_discover_models_skips_invalid_rows_and_uses_capability_fallbacks(monkeypatch):
    adapter = OpenAICompatibleAdapter(api_key="test-key")

    async def fake_request(self, method, path, *, json_body=None, timeout_seconds):
        assert method == "GET"
        assert path == "/models"
        return _response(
            200,
            json_body={
                "data": [
                    "not-a-row",
                    {"id": "   "},
                    {
                        "id": "model-a",
                        "supported_features": {
                            "function_calling": 1,
                            "response_format": True,
                            "stream": 0,
                        },
                        "architecture": {
                            "input_modalities": ["Image"],
                            "tokenizer": " custom-tokenizer ",
                        },
                        "max_completion_tokens": "16",
                    },
                    {"id": "model-b"},
                ]
            },
        )

    monkeypatch.setattr(OpenAICompatibleAdapter, "_request_with_retries", fake_request)

    models = await adapter.discover_models()

    assert len(models) == 2

    model_a = models[0]
    assert model_a["id"] == "openai_compatible/model-a"
    assert model_a["supports_tools"] == 1
    assert model_a["supports_streaming"] == 0
    assert model_a["supports_structured_output"] == 1
    assert model_a["supports_vision"] == 1
    assert model_a["max_output_tokens"] == 16
    assert model_a["tokenizer_family"] == "custom-tokenizer"

    model_b = models[1]
    assert model_b["id"] == "openai_compatible/model-b"
    assert model_b["supports_tools"] == 0
    assert model_b["supports_streaming"] == 1
    assert model_b["supports_vision"] == 0
    assert model_b["tokenizer_family"] is None
    assert model_b["context_window"] == OpenAICompatibleAdapter.default_context_window


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

    def build_request(self, method, url, headers=None, json=None):
        return {"method": method, "url": url, "headers": headers, "json": json}

    async def send(self, request, stream=False):
        return self.response

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_stream_chat_completions_maps_line_timeout_to_retryable_error(monkeypatch):
    adapter = OpenAIAdapter(api_key="test-key")
    response = _FakeStreamingResponse(line_error=httpx.TimeoutException("timed out"))
    client = _FakeStreamingClient(response)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: client)

    stream_result = await adapter.stream_chat_completions(
        {"messages": [{"role": "user", "content": "hello"}]},
        model="gpt-test",
    )

    with pytest.raises(ProviderRetryableError) as exc_info:
        [chunk async for chunk in stream_result.events]

    assert str(exc_info.value) == "provider stream timeout"
    assert exc_info.value.category == "PROVIDER_UNAVAILABLE"
    assert response.closed is True
    assert client.closed is True


@pytest.mark.asyncio
async def test_stream_chat_completions_maps_line_http_error_to_retryable_error(monkeypatch):
    adapter = OpenAIAdapter(api_key="test-key")
    response = _FakeStreamingResponse(line_error=httpx.HTTPError("socket closed"))
    client = _FakeStreamingClient(response)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: client)

    stream_result = await adapter.stream_chat_completions(
        {"messages": [{"role": "user", "content": "hello"}]},
        model="gpt-test",
    )

    with pytest.raises(ProviderRetryableError) as exc_info:
        [chunk async for chunk in stream_result.events]

    assert str(exc_info.value) == "provider stream transport error"
    assert exc_info.value.category == "PROVIDER_UNAVAILABLE"
    assert response.closed is True
    assert client.closed is True


@pytest.mark.asyncio
async def test_probe_builds_expected_payload_and_clamps_timeout(monkeypatch):
    adapter = OpenAICompatibleAdapter(api_key="test-key")
    captured: dict[str, Any] = {}

    async def fake_request(self, method, path, *, json_body=None, timeout_seconds):
        captured["method"] = method
        captured["path"] = path
        captured["json_body"] = dict(json_body or {})
        captured["timeout_seconds"] = timeout_seconds
        return _response(
            200,
            json_body={
                "id": "chatcmpl-probe",
                "usage": {
                    "prompt_tokens": "9",
                    "completion_tokens": 2.8,
                    "total_tokens": True,
                },
            },
        )

    monkeypatch.setattr(OpenAICompatibleAdapter, "_request_with_retries", fake_request)

    result = await adapter.probe("test-model", max_tokens=7, timeout_seconds=0)

    assert captured["method"] == "POST"
    assert captured["path"] == "/chat/completions"
    assert captured["timeout_seconds"] == 1
    assert captured["json_body"] == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 7,
        "stream": False,
    }
    assert result.payload["id"] == "chatcmpl-probe"
    assert result.prompt_tokens == 9
    assert result.completion_tokens == 2
    assert result.total_tokens is None


@pytest.mark.asyncio
async def test_request_with_retries_retries_retryable_status_errors(monkeypatch):
    adapter = OpenAICompatibleAdapter(api_key="test-key")
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
            if attempts < 3:
                return _response(
                    503,
                    json_body={"error": {"message": "try again", "code": "server_error"}},
                )
            return _response(200, json_body={"ok": True})

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    response = await adapter._request_with_retries("GET", "/models", timeout_seconds=15)

    assert response.status_code == 200
    assert attempts == 3


def test_extract_error_details_handles_empty_non_json_and_non_mapping_payloads():
    adapter = OpenAICompatibleAdapter(api_key="test-key")

    assert adapter._extract_error_details(b"") == (None, None)
    assert adapter._extract_error_details(b"not-json-body") == ("not-json-body", None)
    assert adapter._extract_error_details(b'["array", "payload"]') == (None, None)


def test_nested_get_returns_value_or_none_for_dotted_paths():
    adapter = OpenAICompatibleAdapter(api_key="test-key")
    row = {"architecture": {"tokenizer": "o200k_base"}, "tokenizer": "cl100k_base"}

    assert adapter._nested_get(row, "architecture.tokenizer") == "o200k_base"
    assert adapter._nested_get(row, "architecture.tokenizer.family") is None
    assert adapter._nested_get(row, "tokenizer") == "cl100k_base"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, None),
        (7.9, 7),
        (-1.2, None),
        ("42", 42),
        (" 0042 ", 42),
        ("4.2", None),
    ],
)
def test_coerce_int_handles_float_and_string_edges(value: Any, expected: int | None):
    adapter = OpenAICompatibleAdapter(api_key="test-key")

    assert adapter._coerce_int(value) == expected


def test_extract_bool_parses_string_true_and_false_values():
    adapter = OpenAICompatibleAdapter(api_key="test-key")

    assert adapter._extract_bool({"flag": " yes "}, "flag") is True
    assert adapter._extract_bool({"flag": "off"}, "flag") is False
    assert adapter._extract_bool({"flag": "unknown"}, "flag") is None


@pytest.mark.asyncio
async def test_request_with_retries_raises_last_retryable_error_after_exhaustion(monkeypatch):
    adapter = OpenAICompatibleAdapter(api_key="test-key")
    adapter.max_retries = 2

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def request(self, method, url, headers=None, json=None):
            return _response(
                503,
                json_body={"error": {"message": "upstream unavailable", "code": "server_error"}},
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(ProviderRetryableError) as exc_info:
        await adapter._request_with_retries("GET", "/models", timeout_seconds=15)

    assert str(exc_info.value) == "upstream unavailable"
    assert exc_info.value.status_code == 503
    assert exc_info.value.error_code == "server_error"


def test_raise_for_response_uses_fallback_message_for_empty_error_body():
    adapter = OpenAICompatibleAdapter(api_key="test-key")

    with pytest.raises(ProviderFatalError) as exc_info:
        adapter._raise_for_response(404, b"")

    assert str(exc_info.value) == "openai_compatible error (404)"
    assert exc_info.value.category == "INVALID_REQUEST"
