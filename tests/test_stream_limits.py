from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from types import SimpleNamespace

import httpx
import pytest

from src.providers.base import ProviderRetryableError, StreamResult
from src.providers.openai import OpenAIAdapter
from src.proxy import _relay_stream
from src.routing import RoutingRequirements


class _ResponseStream:
    status_code = 200
    headers: dict[str, str] = {}
    request = httpx.Request("POST", "https://provider.test/chat/completions")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_bytes(self, chunk_size: int = 65536) -> AsyncIterator[bytes]:
        del chunk_size
        yield b"123456"
        yield b"7890"


class _ResponseClient:
    def __init__(self, *args, **kwargs):
        pass

    def stream(self, *args, **kwargs):
        return _ResponseStream()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_nonstream_upstream_response_stops_at_configured_adapter_cap(monkeypatch):
    adapter = OpenAIAdapter(api_key="test-key")
    adapter.max_response_bytes = 8
    monkeypatch.setattr("src.providers.openai_compatible.httpx.AsyncClient", _ResponseClient)

    with pytest.raises(ProviderRetryableError, match="response exceeded size limit"):
        await adapter._request_with_retries("GET", "/models", timeout_seconds=1)


@pytest.mark.asyncio
async def test_sse_event_is_bounded_while_reading_bytes(monkeypatch):
    class StreamingResponse:
        status_code = 200
        closed = False

        async def aiter_bytes(self, chunk_size: int = 65536):
            del chunk_size
            yield b"data: event-is-too-large\n\n"

        async def aclose(self):
            self.closed = True

    response = StreamingResponse()

    class StreamingClient:
        def __init__(self, *args, **kwargs):
            pass

        def build_request(self, *args, **kwargs):
            return object()

        async def send(self, request, stream=False):
            return response

        async def aclose(self):
            pass

    monkeypatch.setattr("src.providers.openai_compatible.httpx.AsyncClient", StreamingClient)
    adapter = OpenAIAdapter(api_key="test-key")
    adapter.max_sse_event_bytes = 8
    result = await adapter.stream_chat_completions({"messages": []}, model="gpt-test")

    with pytest.raises(ProviderRetryableError, match="SSE event exceeded size limit"):
        await anext(result.events)
    assert response.closed


class _RelayRequest:
    def __init__(self, idle: float, total: float):
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    gateway_stream_idle_timeout_seconds=idle,
                    gateway_stream_total_timeout_seconds=total,
                    gateway_max_sse_event_bytes=1024 * 1024,
                )
            )
        )

    async def is_disconnected(self):
        return False


class _RelayDb:
    def log_request(self, entry):
        self.entry = entry


async def _relay(first_event: bytes, events, idle: float, total: float) -> list[bytes]:
    request = _RelayRequest(idle, total)
    req = RoutingRequirements(requested_model="auto", requires_streaming=True)
    return [
        event
        async for event in _relay_stream(
            request,  # type: ignore[arg-type]
            _RelayDb(),
            req,
            "request-id",
            "model-id",
            "provider-id",
            0,
            first_event,
            StreamResult(events=events),
            time.monotonic(),
            {},
            lambda status, code, message: ("PROVIDER_UNAVAILABLE", True),
        )
    ]


@pytest.mark.asyncio
async def test_stream_idle_deadline_stops_stalled_provider_stream(monkeypatch):
    async def stalled():
        await asyncio.sleep(0.1)
        yield b"data: late\n\n"

    monkeypatch.setattr("src.proxy.mark_failure", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.proxy._log_failure", lambda *args, **kwargs: None)
    output = await _relay(b"data: first\n\n", stalled(), idle=0.01, total=2)
    assert output == [b"data: first\n\n"]


@pytest.mark.asyncio
async def test_stream_total_deadline_stops_active_but_overlong_stream(monkeypatch):
    async def slow():
        yield b"data: one\n\n"
        await asyncio.sleep(0.4)
        yield b"data: two\n\n"

    monkeypatch.setattr("src.proxy.mark_failure", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.proxy._log_failure", lambda *args, **kwargs: None)
    output = await _relay(b"data: first\n\n", slow(), idle=1, total=0.15)
    assert output == [b"data: first\n\n", b"data: one\n\n"]


@pytest.mark.asyncio
async def test_normal_stream_finishes_with_done_marker(monkeypatch):
    async def quick():
        yield b"data: normal\n\n"

    monkeypatch.setattr("src.proxy.mark_success", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.proxy.mark_failure", lambda *args, **kwargs: None)
    output = await _relay(b"data: first\n\n", quick(), idle=1, total=1)
    assert output == [b"data: first\n\n", b"data: normal\n\n", b"data: [DONE]\n\n"]
