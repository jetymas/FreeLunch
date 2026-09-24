from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from src.proxy import _read_chat_payload, build_router


def test_chat_route_keeps_object_request_schema_in_openapi():
    app = FastAPI()
    app.include_router(build_router())
    operation = app.openapi()["paths"]["/v1/chat/completions"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {"type": "object"}


def _request_with_body(body: bytes, *, limit: int = 1024) -> Request:
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            await asyncio.sleep(0)
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    app = SimpleNamespace(
        state=SimpleNamespace(settings=SimpleNamespace(gateway_max_request_body_bytes=limit))
    )
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 8000),
        "app": app,
    }
    return Request(scope, receive)


def test_chat_request_body_limit_returns_413():
    request = _request_with_body(b'{"messages":"' + (b"x" * 2000) + b'"}')
    with pytest.raises(HTTPException) as error:
        asyncio.run(_read_chat_payload(request))
    assert error.value.status_code == 413
    assert error.value.detail == "request body too large"


def test_chat_request_rejects_non_object_json():
    request = _request_with_body(b'["not","an","object"]')
    with pytest.raises(HTTPException) as error:
        asyncio.run(_read_chat_payload(request))
    assert error.value.status_code == 400
    assert error.value.detail == "request body must be a JSON object"
