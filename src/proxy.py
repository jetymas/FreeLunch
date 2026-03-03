from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.db import utc_now_iso
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


def _serialize_model_row(row: tuple) -> dict:
    return {
        "id": row[0],
        "provider_id": row[1],
        "provider_model_id": row[2],
        "composite_score": row[3],
        "is_healthy": bool(row[4]),
        "is_active": bool(row[5]),
        "supports_tools": bool(row[6]),
        "supports_streaming": bool(row[7]),
        "supports_vision": bool(row[8]),
        "supports_structured_output": bool(row[9]),
        "context_window": row[10],
        "max_output_tokens": row[11],
        "last_error": row[12],
        "last_success_at": row[13],
        "last_failure_at": row[14],
    }


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
                """
                SELECT id, provider_id, provider_model_id, composite_score, is_healthy, is_active,
                       supports_tools, supports_streaming, supports_vision, supports_structured_output,
                       context_window, max_output_tokens, last_error, last_success_at, last_failure_at
                FROM models
                ORDER BY composite_score DESC
                """
            ).fetchall()
        return {"models": [_serialize_model_row(m) for m in rows]}

    @router.get("/admin/models/{model_id:path}")
    async def admin_model_detail(model_id: str, request: Request, authorization: str | None = Header(default=None)) -> dict:
        _check_gateway_auth(request, authorization)
        db = request.app.state.db
        with db.read_conn() as conn:
            row = conn.execute(
                """
                SELECT id, provider_id, provider_model_id, composite_score, is_healthy, is_active,
                       supports_tools, supports_streaming, supports_vision, supports_structured_output,
                       context_window, max_output_tokens, last_error, last_success_at, last_failure_at
                FROM models
                WHERE id=?
                """,
                (model_id,),
            ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="model not found")
        return {"model": _serialize_model_row(row)}

    @router.post("/admin/models/{model_id:path}/disable")
    async def admin_disable_model(model_id: str, request: Request, authorization: str | None = Header(default=None)) -> dict:
        _check_gateway_auth(request, authorization)
        db = request.app.state.db
        with db.read_conn() as conn:
            exists = conn.execute("SELECT 1 FROM models WHERE id=?", (model_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="model not found")

        db.writer.enqueue("UPDATE models SET is_active=0 WHERE id=?", (model_id,))
        db.writer.flush()
        request.app.state.recompute_readiness()
        return {"status": "disabled", "model_id": model_id}

    @router.post("/admin/models/{model_id:path}/enable")
    async def admin_enable_model(model_id: str, request: Request, authorization: str | None = Header(default=None)) -> dict:
        _check_gateway_auth(request, authorization)
        db = request.app.state.db
        with db.read_conn() as conn:
            exists = conn.execute("SELECT 1 FROM models WHERE id=?", (model_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="model not found")

        db.writer.enqueue("UPDATE models SET is_active=1 WHERE id=?", (model_id,))
        db.writer.flush()
        request.app.state.recompute_readiness()
        return {"status": "enabled", "model_id": model_id}

    @router.get("/admin/health")
    async def admin_health(request: Request, authorization: str | None = Header(default=None)) -> dict:
        _check_gateway_auth(request, authorization)
        db = request.app.state.db
        with db.read_conn() as conn:
            model_stats = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END) AS active,
                    SUM(CASE WHEN is_healthy=1 THEN 1 ELSE 0 END) AS healthy,
                    SUM(CASE WHEN is_active=1 AND is_healthy=1 THEN 1 ELSE 0 END) AS routable
                FROM models
                """
            ).fetchone()
            provider_stats = conn.execute(
                """
                SELECT provider_id,
                       COUNT(*) AS total,
                       SUM(CASE WHEN is_active=1 AND is_healthy=1 THEN 1 ELSE 0 END) AS routable
                FROM models
                GROUP BY provider_id
                ORDER BY provider_id
                """
            ).fetchall()
            errors = conn.execute(
                "SELECT id, last_error, consecutive_failures FROM models WHERE last_error IS NOT NULL ORDER BY last_failure_at DESC LIMIT 20"
            ).fetchall()

        return {
            "bootstrap": {
                "started_at": request.app.state.started_at,
                "ready": bool(request.app.state.ready),
                "force_discovery": bool(request.app.state.force_discovery),
            },
            "db": {
                "writer_queue_depth": db.writer.queue_depth(),
            },
            "models": {
                "total": int(model_stats[0] or 0),
                "active": int(model_stats[1] or 0),
                "healthy": int(model_stats[2] or 0),
                "routable": int(model_stats[3] or 0),
                "providers": [
                    {"provider_id": row[0], "total": int(row[1] or 0), "routable": int(row[2] or 0)}
                    for row in provider_stats
                ],
            },
            "scheduler": {
                "jobs": request.app.state.job_status,
            },
            "recent_model_errors": [
                {"model_id": e[0], "last_error": e[1], "consecutive_failures": e[2]} for e in errors
            ],
        }

    @router.post("/admin/refresh")
    async def admin_refresh(request: Request, authorization: str | None = Header(default=None)) -> dict:
        _check_gateway_auth(request, authorization)
        discovery_runner = getattr(request.app.state, "discovery_runner", None)
        if discovery_runner is None:
            raise HTTPException(status_code=503, detail="discovery runner unavailable")

        async with request.app.state.discovery_lock:
            request.app.state.force_discovery = True
            outcome = await discovery_runner()
        return {"status": "completed", "outcome": outcome}

    @router.get("/admin/logs")
    async def admin_logs(
        request: Request,
        authorization: str | None = Header(default=None),
        limit: int = 50,
        success_only: bool | None = None,
    ) -> dict:
        _check_gateway_auth(request, authorization)
        capped_limit = min(max(limit, 1), 500)

        db = request.app.state.db
        db.writer.flush()
        where = []
        params: list[object] = []
        if success_only is not None:
            where.append("success=?")
            params.append(1 if success_only else 0)

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        query = (
            "SELECT request_id, timestamp, selected_model_id, provider_id, attempt_index, was_fallback, "
            "latency_ms, ttfb_ms, success, gateway_error_category, error_message, was_streaming, had_tools, had_vision "
            f"FROM request_log {where_sql} ORDER BY timestamp DESC, id DESC LIMIT ?"
        )
        params.append(capped_limit)

        with db.read_conn() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()

        logs = [
            {
                "request_id": row[0],
                "timestamp": row[1],
                "selected_model_id": row[2],
                "provider_id": row[3],
                "attempt_index": row[4],
                "was_fallback": bool(row[5]),
                "latency_ms": row[6],
                "ttfb_ms": row[7],
                "success": bool(row[8]),
                "gateway_error_category": row[9],
                "error_message": row[10],
                "was_streaming": bool(row[11]),
                "had_tools": bool(row[12]),
                "had_vision": bool(row[13]),
            }
            for row in rows
        ]
        return {"logs": logs, "count": len(logs), "limit": capped_limit}

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
                        utc_now_iso(),
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
                        utc_now_iso(),
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
