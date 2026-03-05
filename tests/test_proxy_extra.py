from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from src.providers.base import ProviderFatalError, StreamResult
from src.proxy import _parse_stream_event, _relay_stream
from src.routing import RoutingRequirements


class _FakeDB:
    def __init__(self) -> None:
        self.logged: list[dict] = []

    def log_request(self, payload: dict) -> None:
        self.logged.append(payload)


class _FakeRequest:
    def __init__(self, disconnections: list[bool]) -> None:
        self._disconnections = list(disconnections)
        fallback = disconnections[-1] if disconnections else False
        self._fallback = fallback
        self.app = SimpleNamespace(state=SimpleNamespace(settings=object()))

    async def is_disconnected(self) -> bool:
        if self._disconnections:
            return self._disconnections.pop(0)
        return self._fallback


def _req() -> RoutingRequirements:
    return RoutingRequirements(requested_model="auto", token_estimation_messages=[])


def _categorize_error(status_code: int | None, error_code: str | None, message: str):
    del status_code, error_code, message
    return ("PROVIDER_UNAVAILABLE", True)


def _collect(gen):
    async def _run():
        chunks = []
        async for chunk in gen:
            chunks.append(chunk)
        return chunks

    return asyncio.run(_run())


def test_parse_stream_event_ignores_non_data_lines_and_invalid_json():
    payload, done = _parse_stream_event(b"event: ping\n\n")
    assert payload is None
    assert done is False

    payload, done = _parse_stream_event(b"data: {not-json}\n\n")
    assert payload is None
    assert done is False


def test_relay_stream_skips_output_when_client_is_disconnected_before_first_event(monkeypatch):
    db = _FakeDB()
    request = _FakeRequest([True, True, True])
    mark_success_calls = []
    mark_failure_calls = []
    monkeypatch.setattr(
        "src.proxy.mark_success",
        lambda *args, **kwargs: mark_success_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "src.proxy.mark_failure",
        lambda *args, **kwargs: mark_failure_calls.append((args, kwargs)),
    )

    async def events():
        yield b'data: {"id":"chatcmpl-test"}\n\n'

    output = _collect(
        _relay_stream(
            request,
            db,
            _req(),
            "req-1",
            "model-1",
            "provider-1",
            0,
            b'data: {"id":"chatcmpl-test","choices":[{"index":0,"delta":{"content":"first"}}]}\n\n',
            StreamResult(events=events()),
            time.monotonic(),
            {"selected_provider_model_id": "model-1"},
            _categorize_error,
        )
    )

    assert output == []
    assert db.logged == []
    assert mark_success_calls == []
    assert mark_failure_calls == []


def test_relay_stream_uses_latest_usage_and_appends_done_when_provider_omits_done(monkeypatch):
    db = _FakeDB()
    request = _FakeRequest([False, False, False, False])
    mark_success_calls = []
    mark_failure_calls = []
    monkeypatch.setattr(
        "src.proxy.mark_success",
        lambda *args, **kwargs: mark_success_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "src.proxy.mark_failure",
        lambda *args, **kwargs: mark_failure_calls.append((args, kwargs)),
    )

    async def events():
        yield b'data: {"id":"chatcmpl-test","choices":[{"index":0,"delta":{"content":"second"},"finish_reason":null}]}\n\n'
        yield b'data: {"id":"chatcmpl-test","choices":[{"index":0,"delta":{"content":"third"},"finish_reason":null}],"usage":{"prompt_tokens":31,"completion_tokens":9,"total_tokens":40}}\n\n'

    output = _collect(
        _relay_stream(
            request,
            db,
            _req(),
            "req-2",
            "model-2",
            "provider-2",
            0,
            b'data: {"id":"chatcmpl-test","choices":[{"index":0,"delta":{"content":"first"},"finish_reason":null}]}\n\n',
            StreamResult(events=events()),
            time.monotonic(),
            {"selected_provider_model_id": "model-2"},
            _categorize_error,
        )
    )

    assert output[0].startswith(b'data: {"id":"chatcmpl-test"')
    assert output[-1] == b"data: [DONE]\n\n"
    assert len(output) == 4
    assert len(mark_success_calls) == 1
    assert mark_failure_calls == []

    assert len(db.logged) == 1
    assert db.logged[0]["success"] is True
    assert db.logged[0]["prompt_tokens"] == 31
    assert db.logged[0]["completion_tokens"] == 9
    assert db.logged[0]["total_tokens"] == 40


def test_relay_stream_logs_non_retryable_provider_error_without_marking_failure(monkeypatch):
    db = _FakeDB()
    request = _FakeRequest([False, False, False])
    mark_success_calls = []
    mark_failure_calls = []
    monkeypatch.setattr(
        "src.proxy.mark_success",
        lambda *args, **kwargs: mark_success_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "src.proxy.mark_failure",
        lambda *args, **kwargs: mark_failure_calls.append((args, kwargs)),
    )

    async def events():
        if False:  # pragma: no cover
            yield b""
        raise ProviderFatalError("stream payload invalid", category="INVALID_REQUEST")

    output = _collect(
        _relay_stream(
            request,
            db,
            _req(),
            "req-3",
            "model-3",
            "provider-3",
            0,
            b'data: {"id":"chatcmpl-test","choices":[{"index":0,"delta":{"content":"first"},"finish_reason":null}]}\n\n',
            StreamResult(events=events()),
            time.monotonic(),
            {"selected_provider_model_id": "model-3"},
            _categorize_error,
        )
    )

    assert output == [
        b'data: {"id":"chatcmpl-test","choices":[{"index":0,"delta":{"content":"first"},"finish_reason":null}]}\n\n'
    ]
    assert mark_success_calls == []
    assert mark_failure_calls == []
    assert len(db.logged) == 1
    assert db.logged[0]["success"] is False
    assert db.logged[0]["gateway_error_category"] == "INVALID_REQUEST"
    assert "stream payload invalid" in db.logged[0]["error_message"]


def test_relay_stream_stops_when_client_disconnects_midstream(monkeypatch):
    db = _FakeDB()
    request = _FakeRequest([False, True, True])
    mark_success_calls = []
    mark_failure_calls = []
    monkeypatch.setattr(
        "src.proxy.mark_success",
        lambda *args, **kwargs: mark_success_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "src.proxy.mark_failure",
        lambda *args, **kwargs: mark_failure_calls.append((args, kwargs)),
    )

    async def events():
        yield b'data: {"id":"chatcmpl-test","choices":[{"index":0,"delta":{"content":"second"},"finish_reason":null}]}\n\n'

    output = _collect(
        _relay_stream(
            request,
            db,
            _req(),
            "req-4",
            "model-4",
            "provider-4",
            0,
            b'data: {"id":"chatcmpl-test","choices":[{"index":0,"delta":{"content":"first"},"finish_reason":null}]}\n\n',
            StreamResult(events=events()),
            time.monotonic(),
            {"selected_provider_model_id": "model-4"},
            _categorize_error,
        )
    )

    assert output == [
        b'data: {"id":"chatcmpl-test","choices":[{"index":0,"delta":{"content":"first"},"finish_reason":null}]}\n\n'
    ]
    assert db.logged == []
    assert mark_success_calls == []
    assert mark_failure_calls == []


def test_relay_stream_handles_done_first_event_without_followup_events(monkeypatch):
    db = _FakeDB()
    request = _FakeRequest([False, False])
    mark_success_calls = []
    mark_failure_calls = []
    monkeypatch.setattr(
        "src.proxy.mark_success",
        lambda *args, **kwargs: mark_success_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "src.proxy.mark_failure",
        lambda *args, **kwargs: mark_failure_calls.append((args, kwargs)),
    )

    async def events():
        if False:  # pragma: no cover
            yield b""

    output = _collect(
        _relay_stream(
            request,
            db,
            _req(),
            "req-5",
            "model-5",
            "provider-5",
            0,
            b"data: [DONE]\n\n",
            StreamResult(events=events()),
            time.monotonic(),
            {"selected_provider_model_id": "model-5"},
            _categorize_error,
        )
    )

    assert output == [b"data: [DONE]\n\n"]
    assert len(mark_success_calls) == 1
    assert mark_failure_calls == []
    assert len(db.logged) == 1
    assert db.logged[0]["success"] is True
