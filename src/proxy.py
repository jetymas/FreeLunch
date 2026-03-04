from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.db import utc_now_iso
from src.health import (
    get_probe_budget_summary,
    get_probe_runtime_summary,
    get_recent_probe_activity,
    get_token_estimation_review_summary,
    mark_failure,
    mark_success,
)
from src.providers.base import ProviderError, ProviderRetryableError, StreamResult
from src.providers.openrouter import categorize_openrouter_error
from src.routing import (
    NoHealthyModelsError,
    RoutingPreferences,
    RoutingRequirements,
    pick_candidates,
)
from src.runtime_logging import get_logger, get_runtime_logging_status, runtime_log
from src.tokens import estimate_required_tokens, request_contains_vision

logger = get_logger(__name__)


def _candidate_token_observation(
    db,
    req: RoutingRequirements,
    *,
    model_id: str,
    provider_model_id: str,
) -> dict[str, int | str | None]:
    row = db.get_model_tokenization_metadata(model_id)
    tokenizer_family = str(row["tokenizer_family"]) if row and row["tokenizer_family"] else None
    selected_context_window = int(row["context_window"]) if row and row["context_window"] else None
    estimated_prompt_tokens = estimate_required_tokens(
        req.token_estimation_messages,
        tools=req.token_estimation_tools,
        response_format=req.token_estimation_response_format,
        safety_buffer=req.token_estimation_safety_buffer,
        tokenizer_family=tokenizer_family,
        model_hint=provider_model_id,
    )
    return {
        "selected_provider_model_id": provider_model_id,
        "selected_tokenizer_family": tokenizer_family,
        "estimated_prompt_tokens": estimated_prompt_tokens,
        "selected_context_window": selected_context_window,
    }


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
    messages = payload.get("messages", [])
    max_completion_tokens = payload.get("max_completion_tokens")
    if max_completion_tokens is None:
        max_completion_tokens = payload.get("max_tokens", 0)

    return RoutingRequirements(
        requested_model=payload.get("model", "auto"),
        requires_tools=bool(payload.get("tools")),
        requires_vision=request_contains_vision(messages),
        requires_streaming=bool(payload.get("stream")),
        requires_structured_output=bool(payload.get("response_format")),
        min_context_window=int(payload.get("min_context_window", 0) or 0),
        min_output_tokens=int(max_completion_tokens or 0),
        token_estimation_messages=messages,
        token_estimation_tools=payload.get("tools"),
        token_estimation_response_format=payload.get("response_format"),
    )


def _parse_routing_preferences(request: Request) -> RoutingPreferences | None:
    settings = request.app.state.settings
    if not settings.routing_enable_request_preference_headers:
        return None

    preference = request.headers.get("X-Gateway-Preference", "balanced").strip().lower()
    if preference not in {"balanced", "quality", "latency", "context", "reliability"}:
        preference = "balanced"

    max_latency_ms = request.headers.get("X-Gateway-Max-Latency-Ms")
    min_context = request.headers.get("X-Gateway-Min-Context")
    return RoutingPreferences(
        preference=preference,
        max_latency_ms=int(max_latency_ms) if max_latency_ms and max_latency_ms.isdigit() else None,
        min_context_tokens=int(min_context) if min_context and min_context.isdigit() else None,
    )


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


def _provider_error_status(exc: ProviderError) -> int:
    if exc.category == "AUTH_ERROR":
        return exc.status_code or 401
    if exc.category in {"INVALID_REQUEST", "CONTEXT_EXCEEDED"}:
        return 400
    if exc.category == "RATE_LIMITED":
        return 429
    return 502


def _log_failure(
    db,
    *,
    request_id: str,
    model_id: str,
    provider_name: str,
    requested_model: str | None,
    attempt_index: int,
    latency_ms: int,
    provider_error: ProviderError,
    requires_streaming: bool,
    requires_tools: bool,
    requires_vision: bool,
    token_observation: dict[str, int | str | None],
) -> None:
    db.log_request(
        {
            "request_id": request_id,
            "timestamp": utc_now_iso(),
            "request_source": "client",
            "selected_model_id": model_id,
            "provider_id": provider_name,
            "client_requested_model": requested_model,
            "attempt_index": attempt_index,
            "was_fallback": attempt_index > 0,
            **token_observation,
            "latency_ms": latency_ms,
            "success": False,
            "gateway_error_category": provider_error.category,
            "error_code": provider_error.error_code,
            "error_message": str(provider_error)[:500],
            "was_streaming": requires_streaming,
            "had_tools": requires_tools,
            "had_vision": requires_vision,
        }
    )


def _parse_stream_event(raw_event: bytes) -> tuple[dict | None, bool]:
    for line in raw_event.decode("utf-8", errors="ignore").splitlines():
        if not line.startswith("data: "):
            continue
        data = line[6:].strip()
        if data == "[DONE]":
            return None, True
        try:
            return json.loads(data), False
        except json.JSONDecodeError:
            return None, False
    return None, False


def _is_comment_event(raw_event: bytes) -> bool:
    lines = [line.strip() for line in raw_event.decode("utf-8", errors="ignore").splitlines() if line.strip()]
    return bool(lines) and all(line.startswith(":") for line in lines)


def _provider_error_from_event(event: dict) -> ProviderError | None:
    error = event.get("error")
    if not isinstance(error, dict):
        return None

    message = str(error.get("message") or "provider stream error")[:500]
    code = error.get("code")
    error_code = str(code) if code is not None else None
    raw_status_code = error.get("status_code")
    status_code = None
    if isinstance(raw_status_code, int):
        status_code = raw_status_code
    elif isinstance(raw_status_code, str) and raw_status_code.isdigit():
        status_code = int(raw_status_code)
    elif isinstance(code, int):
        status_code = code
    elif isinstance(code, str) and code.isdigit():
        status_code = int(code)

    category, retryable = categorize_openrouter_error(status_code, error_code, message)
    if retryable:
        return ProviderRetryableError(
            message,
            category=category,
            status_code=status_code,
            error_code=error_code,
        )
    return ProviderError(
        message,
        category=category,
        retryable=False,
        status_code=status_code,
        error_code=error_code,
    )


async def _relay_stream(
    request: Request,
    db,
    req: RoutingRequirements,
    request_id: str,
    model_id: str,
    provider_name: str,
    attempt_index: int,
    first_event: bytes,
    stream_result: StreamResult,
    start: float,
    token_observation: dict[str, int | str | None],
) -> AsyncGenerator[bytes, None]:
    done_seen = False
    prompt_tokens = None
    completion_tokens = None
    total_tokens = None
    stream_error: ProviderError | None = None
    ttfb_ms = int((time.monotonic() - start) * 1000)
    first_payload, done_seen = _parse_stream_event(first_event)
    if first_payload and isinstance(first_payload.get("usage"), dict):
        usage = first_payload["usage"]
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
    if first_payload:
        stream_error = _provider_error_from_event(first_payload)

    try:
        if not await request.is_disconnected():
            yield first_event

        async for raw_event in stream_result.events:
            if await request.is_disconnected():
                break
            payload, event_is_done = _parse_stream_event(raw_event)
            done_seen = done_seen or event_is_done
            if payload and isinstance(payload.get("usage"), dict):
                usage = payload["usage"]
                prompt_tokens = usage.get("prompt_tokens")
                completion_tokens = usage.get("completion_tokens")
                total_tokens = usage.get("total_tokens")
            if payload:
                parsed_error = _provider_error_from_event(payload)
                if parsed_error is not None:
                    stream_error = parsed_error
            yield raw_event
    except ProviderError as exc:
        stream_error = exc
    finally:
        latency_ms = int((time.monotonic() - start) * 1000)
        if stream_error is None and not await request.is_disconnected():
            if not done_seen:
                yield b"data: [DONE]\n\n"
            mark_success(db, model_id, latency_ms=latency_ms, ttfb_ms=ttfb_ms)
            db.log_request(
                {
                    "request_id": request_id,
                    "timestamp": utc_now_iso(),
                    "request_source": "client",
                    "selected_model_id": model_id,
                    "provider_id": provider_name,
                    "client_requested_model": req.requested_model,
                    "attempt_index": attempt_index,
                    "was_fallback": attempt_index > 0,
                    **token_observation,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "latency_ms": latency_ms,
                    "ttfb_ms": ttfb_ms,
                    "success": True,
                    "was_streaming": True,
                    "had_tools": req.requires_tools,
                    "had_vision": req.requires_vision,
                }
            )
            runtime_log(
                logger,
                "request.stream.completed",
                verbosity="verbose",
                message="Streaming request completed",
                request_id=request_id,
                model_id=model_id,
                provider_id=provider_name,
                attempt_index=attempt_index,
                latency_ms=latency_ms,
                ttfb_ms=ttfb_ms,
            )
        elif stream_error is not None:
            if stream_error.retryable:
                mark_failure(db, model_id, str(stream_error), settings=request.app.state.settings)
            _log_failure(
                db,
                request_id=request_id,
                model_id=model_id,
                provider_name=provider_name,
                requested_model=req.requested_model,
                attempt_index=attempt_index,
                latency_ms=latency_ms,
                provider_error=stream_error,
                requires_streaming=True,
                requires_tools=req.requires_tools,
                requires_vision=req.requires_vision,
                token_observation=token_observation,
            )
            runtime_log(
                logger,
                "request.stream.failed",
                verbosity="concise",
                level=30,
                message="Streaming request failed",
                request_id=request_id,
                model_id=model_id,
                provider_id=provider_name,
                attempt_index=attempt_index,
                error_category=stream_error.category,
                retryable=bool(stream_error.retryable),
            )


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

        db.set_model_active(model_id, is_active=False)
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

        db.set_model_active(model_id, is_active=True)
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
            "runtime_logging": get_runtime_logging_status(),
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
            "probe_budgets": get_probe_budget_summary(db, request.app.state.settings),
            "probe_state": get_probe_runtime_summary(db, request.app.state.settings),
            "recent_probe_activity": get_recent_probe_activity(db),
            "token_estimation_review": get_token_estimation_review_summary(db),
            "recent_model_errors": [
                {"model_id": e[0], "last_error": e[1], "consecutive_failures": e[2]} for e in errors
            ],
        }

    @router.get("/admin/config")
    async def admin_config(request: Request, authorization: str | None = Header(default=None)) -> dict:
        _check_gateway_auth(request, authorization)
        db = request.app.state.db
        overrides = [
            {"key": row["key"], "value": row["value"], "updated_at": row["updated_at"]}
            for row in db.list_overrides()
        ]
        return {
            "overrides": overrides,
            "effective": request.app.state.settings.public_settings(),
        }

    @router.put("/admin/config/{key:path}")
    async def admin_set_config(
        key: str,
        payload: dict,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict:
        _check_gateway_auth(request, authorization)
        if "value" not in payload:
            raise HTTPException(status_code=400, detail="missing value")
        if not request.app.state.settings.is_overridable(key):
            raise HTTPException(status_code=400, detail="override key not allowed")

        db = request.app.state.db
        db.set_override(key, payload["value"])
        db.writer.flush()
        request.app.state.reload_settings()
        runtime_log(
            logger,
            "admin.config.updated",
            verbosity="verbose",
            message="Updated config override",
            key=key,
        )
        return {
            "status": "updated",
            "key": key,
            "value": payload["value"],
            "effective": request.app.state.settings.public_settings(),
        }

    @router.delete("/admin/config/{key:path}")
    async def admin_delete_config(
        key: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict:
        _check_gateway_auth(request, authorization)
        db = request.app.state.db
        db.delete_override(key)
        db.writer.flush()
        request.app.state.reload_settings()
        runtime_log(
            logger,
            "admin.config.deleted",
            verbosity="verbose",
            message="Deleted config override",
            key=key,
        )
        return {
            "status": "deleted",
            "key": key,
            "effective": request.app.state.settings.public_settings(),
        }

    @router.post("/admin/refresh")
    async def admin_refresh(request: Request, authorization: str | None = Header(default=None)) -> dict:
        _check_gateway_auth(request, authorization)
        discovery_runner = getattr(request.app.state, "discovery_runner", None)
        if discovery_runner is None:
            raise HTTPException(status_code=503, detail="discovery runner unavailable")

        async with request.app.state.discovery_lock:
            request.app.state.reload_settings()
            request.app.state.force_discovery = True
            outcome = await discovery_runner()
        runtime_log(
            logger,
            "admin.refresh.completed",
            verbosity="verbose",
            message="Admin refresh completed",
            **outcome,
        )
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
            "latency_ms, ttfb_ms, success, gateway_error_category, error_code, error_message, "
            "was_streaming, had_tools, had_vision "
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
                "error_code": row[10],
                "error_message": row[11],
                "was_streaming": bool(row[12]),
                "had_tools": bool(row[13]),
                "had_vision": bool(row[14]),
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
        preferences = _parse_routing_preferences(request)
        try:
            candidates = pick_candidates(
                db,
                req,
                preferences=preferences,
                fallback_model_id=settings.ranking_fallback_model,
                limit=settings.routing_max_attempts,
            )
        except NoHealthyModelsError as exc:
            runtime_log(
                logger,
                "routing.no_candidates",
                verbosity="concise",
                level=30,
                message="No routable candidate models were available",
                requested_model=req.requested_model,
            )
            raise HTTPException(
                status_code=503,
                detail="No routable healthy model found",
                headers={"Retry-After": "10"},
            ) from exc
        runtime_log(
            logger,
            "routing.candidates.selected",
            verbosity="debug",
            message="Selected routing candidates",
            candidate_ids=[candidate["id"] for candidate in candidates],
            requested_model=req.requested_model,
            preference=preferences.preference if preferences is not None else None,
        )

        start = time.monotonic()
        request_id = str(uuid.uuid4())
        last_provider_error: ProviderError | None = None
        all_failures_context_exceeded = True
        token_observations: dict[str, dict[str, int | str | None]] = {}
        runtime_log(
            logger,
            "request.received",
            verbosity="debug",
            message="Received chat completion request",
            request_id=request_id,
            requested_model=req.requested_model,
            requires_tools=req.requires_tools,
            requires_vision=req.requires_vision,
            requires_streaming=req.requires_streaming,
            requires_structured_output=req.requires_structured_output,
            min_output_tokens=req.min_output_tokens,
        )

        for idx, candidate in enumerate(candidates):
            model_id = candidate["id"]
            provider_name = candidate["provider_id"]
            model_name = candidate["provider_model_id"]
            provider = registry.get(provider_name)
            token_observation = token_observations.setdefault(
                model_id,
                _candidate_token_observation(
                    db,
                    req,
                    model_id=model_id,
                    provider_model_id=model_name,
                ),
            )
            runtime_log(
                logger,
                "request.attempt.started",
                verbosity="debug",
                message="Starting provider attempt",
                request_id=request_id,
                attempt_index=idx,
                model_id=model_id,
                provider_id=provider_name,
                provider_model_id=model_name,
                estimated_prompt_tokens=token_observation.get("estimated_prompt_tokens"),
                selected_context_window=token_observation.get("selected_context_window"),
            )
            try:
                if req.requires_streaming:
                    stream_result = await provider.stream_chat_completions(payload, model=model_name)
                    first_event = await anext(stream_result.events)
                    while _is_comment_event(first_event):
                        first_event = await anext(stream_result.events)
                    runtime_log(
                        logger,
                        "request.stream.started",
                        verbosity="verbose",
                        message="Streaming provider attempt started",
                        request_id=request_id,
                        attempt_index=idx,
                        model_id=model_id,
                        provider_id=provider_name,
                    )
                    return StreamingResponse(
                        _relay_stream(
                            request,
                            db,
                            req,
                            request_id,
                            model_id,
                            provider_name,
                            idx,
                            first_event,
                            stream_result,
                            start,
                            token_observation,
                        ),
                        media_type="text/event-stream",
                    )

                result = await provider.chat_completions(payload, model=model_name)
                mark_success(db, model_id, latency_ms=result.latency_ms, ttfb_ms=result.ttfb_ms)
                db.log_request(
                    {
                        "request_id": request_id,
                        "timestamp": utc_now_iso(),
                        "request_source": "client",
                        "selected_model_id": model_id,
                        "provider_id": provider_name,
                        "client_requested_model": req.requested_model,
                        "attempt_index": idx,
                        "was_fallback": idx > 0,
                        **token_observation,
                        "prompt_tokens": result.prompt_tokens,
                        "completion_tokens": result.completion_tokens,
                        "total_tokens": result.total_tokens,
                        "latency_ms": int((time.monotonic() - start) * 1000),
                        "ttfb_ms": result.ttfb_ms,
                        "success": True,
                        "was_streaming": req.requires_streaming,
                        "had_tools": req.requires_tools,
                        "had_vision": req.requires_vision,
                    }
                )
                runtime_log(
                    logger,
                    "request.completed",
                    verbosity="verbose",
                    message="Chat completion request succeeded",
                    request_id=request_id,
                    attempt_index=idx,
                    model_id=model_id,
                    provider_id=provider_name,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                )
                return result.payload
            except StopAsyncIteration:
                last_provider_error = ProviderRetryableError(
                    "provider stream ended before first event",
                    category="PROVIDER_UNAVAILABLE",
                )
                all_failures_context_exceeded = False
                runtime_log(
                    logger,
                    "request.attempt.failed",
                    verbosity="concise",
                    level=30,
                    message="Provider stream ended before first event",
                    request_id=request_id,
                    attempt_index=idx,
                    model_id=model_id,
                    provider_id=provider_name,
                )
            except ProviderError as exc:
                last_provider_error = exc
                if exc.retryable:
                    if exc.category != "CONTEXT_EXCEEDED":
                        all_failures_context_exceeded = False
                    if exc.category != "CONTEXT_EXCEEDED":
                        mark_failure(db, model_id, str(exc), settings=settings)
                    _log_failure(
                        db,
                        request_id=request_id,
                        model_id=model_id,
                        provider_name=provider_name,
                        requested_model=req.requested_model,
                        attempt_index=idx,
                        latency_ms=int((time.monotonic() - start) * 1000),
                        provider_error=exc,
                        requires_streaming=req.requires_streaming,
                        requires_tools=req.requires_tools,
                        requires_vision=req.requires_vision,
                        token_observation=token_observation,
                    )
                    runtime_log(
                        logger,
                        "request.attempt.retryable_failure",
                        verbosity="verbose",
                        level=30,
                        message="Provider attempt failed and gateway will retry another candidate",
                        request_id=request_id,
                        attempt_index=idx,
                        model_id=model_id,
                        provider_id=provider_name,
                        error_category=exc.category,
                        error_code=exc.error_code,
                    )
                    continue

                _log_failure(
                    db,
                    request_id=request_id,
                    model_id=model_id,
                    provider_name=provider_name,
                    requested_model=req.requested_model,
                    attempt_index=idx,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    provider_error=exc,
                    requires_streaming=req.requires_streaming,
                    requires_tools=req.requires_tools,
                    requires_vision=req.requires_vision,
                    token_observation=token_observation,
                )
                runtime_log(
                    logger,
                    "request.failed",
                    verbosity="concise",
                    level=30,
                    message="Non-retryable provider error ended request",
                    request_id=request_id,
                    attempt_index=idx,
                    model_id=model_id,
                    provider_id=provider_name,
                    error_category=exc.category,
                    error_code=exc.error_code,
                )
                raise HTTPException(status_code=_provider_error_status(exc), detail=str(exc)) from exc
            except Exception as exc:
                last_provider_error = ProviderRetryableError(
                    str(exc)[:500],
                    category="PROVIDER_UNAVAILABLE",
                )
                all_failures_context_exceeded = False
                mark_failure(db, model_id, str(exc), settings=settings)
                _log_failure(
                    db,
                    request_id=request_id,
                    model_id=model_id,
                    provider_name=provider_name,
                    requested_model=req.requested_model,
                    attempt_index=idx,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    provider_error=last_provider_error,
                    requires_streaming=req.requires_streaming,
                    requires_tools=req.requires_tools,
                    requires_vision=req.requires_vision,
                    token_observation=token_observation,
                )
                runtime_log(
                    logger,
                    "request.attempt.exception",
                    verbosity="concise",
                    level=40,
                    message="Unexpected exception during provider attempt",
                    request_id=request_id,
                    attempt_index=idx,
                    model_id=model_id,
                    provider_id=provider_name,
                    exc_info=True,
                )

        if (
            last_provider_error is not None
            and last_provider_error.category == "CONTEXT_EXCEEDED"
            and all_failures_context_exceeded
        ):
            runtime_log(
                logger,
                "request.failed",
                verbosity="concise",
                level=30,
                message="All candidates exhausted with context exceeded",
                request_id=request_id,
                error_category=last_provider_error.category,
            )
            raise HTTPException(status_code=400, detail=str(last_provider_error))
        runtime_log(
            logger,
            "request.failed",
            verbosity="concise",
            level=30,
            message="All provider candidates failed",
            request_id=request_id,
            error_category=last_provider_error.category if last_provider_error is not None else None,
        )
        raise HTTPException(status_code=502, detail=str(last_provider_error or "all candidates failed"))

    return router
