from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.health import mark_failure, mark_success
from src.routing import RoutingRequirements, pick_candidates


def _check_gateway_auth(request: Request, authorization: str | None) -> None:
    required_key = request.app.state.settings.gateway_api_key
    if not required_key:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1]
    if token != required_key:
        raise HTTPException(status_code=401, detail="invalid bearer token")


def _readiness_guard(request: Request) -> None:
    if not request.app.state.ready:
        raise HTTPException(status_code=503, detail="gateway not ready", headers={"Retry-After": "10"})


def _parse_requirements(payload: dict) -> RoutingRequirements:
    return RoutingRequirements(
        requested_model=payload.get("model", "auto"),
        requires_tools=bool(payload.get("tools")),
        requires_vision=any("image" in str(m.get("content", "")) for m in payload.get("messages", [])),
        requires_streaming=bool(payload.get("stream")),
        requires_structured_output=bool(payload.get("response_format")),
        min_context_window=int(payload.get("min_context_window", 0) or 0),
        min_output_tokens=int(payload.get("max_tokens", 0) or 0),
    )


def _to_sse(payload: dict) -> AsyncGenerator[bytes, None]:
    async def gen() -> AsyncGenerator[bytes, None]:
        choice = payload.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "")
        chunk = {
            "id": payload.get("id", "chatcmpl-stream"),
            "object": "chat.completion.chunk",
            "model": payload.get("model"),
            "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(chunk)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    return gen()


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @router.get("/readyz")
    async def readyz(request: Request) -> dict:
        _readiness_guard(request)
        return {"status": "ready"}

    @router.get("/v1/models")
    async def list_models(request: Request, authorization: str | None = Header(default=None)) -> dict:
        _check_gateway_auth(request, authorization)
        _readiness_guard(request)
        db = request.app.state.db
        with db.read_conn() as conn:
            rows = conn.execute(
                "SELECT provider_id, provider_model_id FROM models WHERE is_healthy=1 AND is_active=1 ORDER BY composite_score DESC"
            ).fetchall()
        return {
            "object": "list",
            "data": [
                {"id": model_name, "object": "model", "owned_by": provider}
                for provider, model_name in rows
            ],
        }

    @router.get("/admin/models")
    async def admin_models(request: Request, authorization: str | None = Header(default=None)) -> dict:
        _check_gateway_auth(request, authorization)
        db = request.app.state.db
        with db.read_conn() as conn:
            rows = conn.execute(
                "SELECT id, provider_id, provider_model_id, composite_score, is_healthy, is_active FROM models ORDER BY composite_score DESC"
            ).fetchall()
        return {
            "models": [
                {
                    "id": m[0],
                    "provider_id": m[1],
                    "provider_model_id": m[2],
                    "composite_score": m[3],
                    "is_healthy": bool(m[4]),
                    "is_active": bool(m[5]),
                }
                for m in rows
            ]
        }

    @router.get("/admin/health")
    async def admin_health(request: Request, authorization: str | None = Header(default=None)) -> dict:
        _check_gateway_auth(request, authorization)
        db = request.app.state.db
        with db.read_conn() as conn:
            errors = conn.execute(
                "SELECT id, last_error, consecutive_failures FROM models WHERE last_error IS NOT NULL ORDER BY last_failure_at DESC LIMIT 20"
            ).fetchall()
        return {"errors": [{"model_id": e[0], "last_error": e[1], "consecutive_failures": e[2]} for e in errors]}

    @router.post("/admin/refresh")
    async def admin_refresh(request: Request, authorization: str | None = Header(default=None)) -> dict:
        _check_gateway_auth(request, authorization)
        request.app.state.force_discovery = True
        return {"status": "queued"}

    @router.post("/v1/chat/completions")
    async def chat_completions(payload: dict, request: Request, authorization: str | None = Header(default=None)):
        _check_gateway_auth(request, authorization)
        _readiness_guard(request)

        db = request.app.state.db
        registry = request.app.state.registry
        settings = request.app.state.settings

        req = _parse_requirements(payload)
        candidates = pick_candidates(db, req, limit=settings.routing_max_attempts)
        if not candidates:
            raise HTTPException(status_code=503, detail="No routable healthy model found", headers={"Retry-After": "10"})

        start = time.monotonic()
        request_id = str(uuid.uuid4())
        last_error: Exception | None = None

        for idx, candidate in enumerate(candidates):
            model_id = candidate["id"]
            provider_name = candidate["provider_id"]
            model_name = candidate["provider_model_id"]
            try:
                provider = registry.get(provider_name)
                result = await provider.chat_completions(payload, model=model_name)
                mark_success(db, model_id)
                db.writer.enqueue(
                    """
                    INSERT INTO request_log(request_id, timestamp, request_source, selected_model_id, provider_id,
                                            client_requested_model, attempt_index, was_fallback, prompt_tokens,
                                            completion_tokens, total_tokens, latency_ms, ttfb_ms, success,
                                            was_streaming, had_tools, had_vision)
                    VALUES (?, ?, 'client', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        request_id,
                        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        model_id,
                        provider_name,
                        req.requested_model,
                        idx,
                        1 if idx > 0 else 0,
                        result.prompt_tokens,
                        result.completion_tokens,
                        result.total_tokens,
                        int((time.monotonic() - start) * 1000),
                        result.ttfb_ms,
                        1 if req.requires_streaming else 0,
                        1 if req.requires_tools else 0,
                        1 if req.requires_vision else 0,
                    ),
                )
                if req.requires_streaming:
                    return StreamingResponse(_to_sse(result.payload), media_type="text/event-stream")
                return result.payload
            except Exception as exc:
                last_error = exc
                mark_failure(db, model_id, str(exc))
                db.writer.enqueue(
                    """
                    INSERT INTO request_log(request_id, timestamp, request_source, selected_model_id, provider_id,
                                            client_requested_model, attempt_index, was_fallback, latency_ms,
                                            success, gateway_error_category, error_message, was_streaming, had_tools, had_vision)
                    VALUES (?, ?, 'client', ?, ?, ?, ?, ?, ?, 0, 'provider_error', ?, ?, ?, ?)
                    """,
                    (
                        request_id,
                        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        model_id,
                        provider_name,
                        req.requested_model,
                        idx,
                        1 if idx > 0 else 0,
                        int((time.monotonic() - start) * 1000),
                        str(exc)[:500],
                        1 if req.requires_streaming else 0,
                        1 if req.requires_tools else 0,
                        1 if req.requires_vision else 0,
                    ),
                )

        raise HTTPException(status_code=503, detail=str(last_error or "all candidates failed"), headers={"Retry-After": "10"})

    return router
