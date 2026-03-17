from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import AsyncGenerator, Callable
from typing import cast

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.config import Settings
from src.db import utc_now_iso
from src.health import (
    get_probe_budget_summary,
    get_probe_runtime_summary,
    get_recent_probe_activity,
    get_token_estimation_review_summary,
    mark_failure,
    mark_success,
)
from src.providers.base import (
    ProviderError,
    ProviderErrorCategorization,
    ProviderRetryableError,
    StreamResult,
    provider_error_from_error_payload,
)
from src.routing import (
    NoHealthyModelsError,
    RoutingPreferences,
    RoutingRequirements,
    pick_candidates,
)
from src.runtime_logging import get_logger, get_runtime_logging_status, runtime_log
from src.secret_store import (
    SecretStorePasswordError,
    create_gateway_auth_config,
    create_vault_config,
    unlock_vault,
    verify_gateway_auth_token,
)
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
    auth_state = getattr(request.app.state, "gateway_auth", {})
    if not auth_state or not auth_state.get("enabled"):
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1]
    source = str(auth_state.get("source", "disabled"))
    if source == "env":
        if token != auth_state.get("env_key"):
            raise HTTPException(status_code=401, detail="invalid bearer token")
        return
    config = auth_state.get("config")
    if source == "managed" and config is not None and verify_gateway_auth_token(token, config):
        return
    if source == "managed":
        raise HTTPException(status_code=401, detail="invalid bearer token")


def _readiness_guard(request: Request) -> None:
    if not request.app.state.ready:
        raise HTTPException(
            status_code=503, detail="gateway not ready", headers={"Retry-After": "10"}
        )


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


def _serialize_model_row(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "provider_id": row["provider_id"],
        "provider_model_id": row["provider_model_id"],
        "composite_score": row["composite_score"],
        "provider_rank": row["provider_rank"],
        "is_healthy": bool(row["is_healthy"]),
        "is_active": bool(row["is_active"]),
        "supports_tools": bool(row["supports_tools"]),
        "supports_streaming": bool(row["supports_streaming"]),
        "supports_vision": bool(row["supports_vision"]),
        "supports_structured_output": bool(row["supports_structured_output"]),
        "supports_system_messages": bool(row["supports_system_messages"]),
        "context_window": row["context_window"],
        "max_output_tokens": row["max_output_tokens"],
        "tokenizer_family": row["tokenizer_family"],
        "avg_latency_ms": row["avg_latency_ms"],
        "avg_ttfb_ms": row["avg_ttfb_ms"],
        "last_error": row["last_error"],
        "last_success_at": row["last_success_at"],
        "last_failure_at": row["last_failure_at"],
        "last_routed_at": row["last_routed_at"],
        "cooldown_until": row["cooldown_until"],
    }


def _build_public_model_list(rows: list[tuple[str, str]]) -> list[dict[str, str]]:
    models = [
        {"id": model_name, "object": "model", "owned_by": provider} for provider, model_name in rows
    ]
    if models:
        models.insert(0, {"id": "auto", "object": "model", "owned_by": "gateway"})
    return models


def _secret_slot_status(
    settings: Settings,
    *,
    secret_key: str,
    label: str,
    kind: str,
    env_var: str,
    managed_secret_rows: dict[str, dict[str, str | None]],
    configured_in_config: bool = False,
    provider_id: str | None = None,
) -> dict[str, object]:
    managed_row = managed_secret_rows.get(secret_key)
    source = "missing"
    configured = False
    if managed_row is not None:
        source = "managed"
        configured = True
    elif os.getenv(env_var, "").strip():
        source = "env"
        configured = True
    elif configured_in_config:
        source = "config"
        configured = True
    status: dict[str, object] = {
        "key": secret_key,
        "label": label,
        "kind": kind,
        "env_var": env_var,
        "configured": configured,
        "source": source,
        "managed": managed_row is not None,
        "updated_at": managed_row.get("updated_at") if managed_row is not None else None,
    }
    if provider_id is not None:
        status["provider_id"] = provider_id
        status["enabled"] = settings.is_provider_enabled(provider_id)
        status["discovery_enabled"] = settings.is_provider_discovery_enabled(provider_id)
        status["inference_enabled"] = settings.is_provider_inference_enabled(provider_id)
    return status


def _list_secret_slots(request: Request) -> list[dict[str, object]]:
    settings = request.app.state.settings
    db = request.app.state.db
    managed_secret_rows = {
        str(row["key"]): {
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in db.list_managed_secrets()
    }
    slots = [
        _secret_slot_status(
            settings,
            secret_key="openrouter_api_key",
            label="OpenRouter API key",
            kind="provider",
            env_var="OPENROUTER_API_KEY",
            managed_secret_rows=managed_secret_rows,
            provider_id="openrouter",
        ),
    ]
    for provider_id in settings.supported_provider_ids:
        if provider_id == "openrouter":
            continue
        provider_config = settings.get_provider_bootstrap_config(provider_id)
        slots.append(
            _secret_slot_status(
                settings,
                secret_key=f"providers.{provider_id}.api_key",
                label=f"{provider_id} API key",
                kind="provider",
                env_var=settings.get_provider_api_key_env(provider_id),
                managed_secret_rows=managed_secret_rows,
                configured_in_config=bool(str(provider_config.get("api_key", "")).strip()),
                provider_id=provider_id,
            )
        )
    return slots


def _require_secret_management_unlocked(request: Request):
    if getattr(request.app.state, "secret_vault_config", None) is None:
        raise HTTPException(status_code=409, detail="secret vault is not configured")
    secret_store = getattr(request.app.state, "secret_store", None)
    if secret_store is None:
        raise HTTPException(status_code=423, detail="secret vault is locked")
    return secret_store


def _parse_secret_password(payload: dict) -> str:
    password = str(payload.get("password", "")).strip()
    if not password:
        raise HTTPException(status_code=400, detail="vault password cannot be empty")
    return password


def _uninstall_info(settings: Settings) -> dict[str, object]:
    port = int(settings.gateway_port)
    return {
        "available": False,
        "reason": "FreeLunch runs inside Docker without host-level uninstall control from the web UI.",
        "admin_ui_url": f"http://localhost:{port}/admin/ui",
        "commands": [
            {
                "label": "PowerShell",
                "command": "powershell -ExecutionPolicy Bypass -File .\\uninstall.ps1",
            },
            {"label": "Shell", "command": "./uninstall.sh"},
        ],
    }


def _type_name(value: object) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "str"


def _serialize_public_settings(settings: Settings) -> list[dict[str, object]]:
    effective = settings.public_settings()
    return [
        {
            "key": key,
            "value": effective[key],
            "type": _type_name(effective[key]),
            "overridable": settings.is_overridable(key),
            "section": key.split(".", 1)[0],
        }
        for key in sorted(effective)
    ]


def _gateway_auth_status(request: Request) -> dict[str, object]:
    auth_state = getattr(request.app.state, "gateway_auth", {})
    return {
        "mode": auth_state.get("mode", "inherit"),
        "enabled": bool(auth_state.get("enabled")),
        "source": auth_state.get("source", "disabled"),
        "env_configured": bool(auth_state.get("env_configured")),
        "updated_at": auth_state.get("updated_at"),
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
    lines = [
        line.strip()
        for line in raw_event.decode("utf-8", errors="ignore").splitlines()
        if line.strip()
    ]
    return bool(lines) and all(line.startswith(":") for line in lines)


def _provider_error_from_event(
    event: dict,
    *,
    categorize_error: Callable[[int | None, str | None, str], ProviderErrorCategorization],
) -> ProviderError | None:
    return provider_error_from_error_payload(event, categorize_error=categorize_error)


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
    categorize_error: Callable[[int | None, str | None, str], ProviderErrorCategorization],
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
        stream_error = _provider_error_from_event(
            first_payload,
            categorize_error=categorize_error,
        )

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
                parsed_error = _provider_error_from_event(
                    payload,
                    categorize_error=categorize_error,
                )
                if parsed_error is not None:
                    stream_error = parsed_error
            yield raw_event
    except ProviderError as exc:
        stream_error = exc
    except Exception as exc:
        stream_error = ProviderRetryableError(
            str(exc)[:500],
            category="PROVIDER_UNAVAILABLE",
        )
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
    async def list_models(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict:
        _check_gateway_auth(request, authorization)
        _readiness_guard(request)
        db = request.app.state.db
        with db.read_conn() as conn:
            rows = conn.execute(
                "SELECT provider_id, provider_model_id FROM models WHERE is_healthy=1 AND is_active=1 ORDER BY composite_score DESC"
            ).fetchall()
        return {"object": "list", "data": _build_public_model_list(rows)}

    @router.get("/admin/models")
    async def admin_models(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict:
        _check_gateway_auth(request, authorization)
        db = request.app.state.db
        with db.read_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, name, provider_id, provider_model_id, composite_score, provider_rank,
                       is_healthy, is_active, supports_tools, supports_streaming, supports_vision,
                       supports_structured_output, supports_system_messages, context_window,
                       max_output_tokens, tokenizer_family, avg_latency_ms, avg_ttfb_ms, last_error,
                       last_success_at, last_failure_at, last_routed_at, cooldown_until
                FROM models
                ORDER BY composite_score DESC
                """
            ).fetchall()
        return {"models": [_serialize_model_row(m) for m in rows]}

    @router.get("/admin/models/{model_id:path}")
    async def admin_model_detail(
        model_id: str, request: Request, authorization: str | None = Header(default=None)
    ) -> dict:
        _check_gateway_auth(request, authorization)
        db = request.app.state.db
        with db.read_conn() as conn:
            row = conn.execute(
                """
                SELECT id, name, provider_id, provider_model_id, composite_score, provider_rank,
                       is_healthy, is_active, supports_tools, supports_streaming, supports_vision,
                       supports_structured_output, supports_system_messages, context_window,
                       max_output_tokens, tokenizer_family, avg_latency_ms, avg_ttfb_ms, last_error,
                       last_success_at, last_failure_at, last_routed_at, cooldown_until
                FROM models
                WHERE id=?
                """,
                (model_id,),
            ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="model not found")
        return {"model": _serialize_model_row(row)}

    @router.post("/admin/models/{model_id:path}/disable")
    async def admin_disable_model(
        model_id: str, request: Request, authorization: str | None = Header(default=None)
    ) -> dict:
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
    async def admin_enable_model(
        model_id: str, request: Request, authorization: str | None = Header(default=None)
    ) -> dict:
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
    async def admin_health(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict:
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
            "secret_management": dict(getattr(request.app.state, "secret_management_status", {})),
            "recent_model_errors": [
                {"model_id": e[0], "last_error": e[1], "consecutive_failures": e[2]} for e in errors
            ],
        }

    @router.get("/admin/config")
    async def admin_config(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict:
        _check_gateway_auth(request, authorization)
        db = request.app.state.db
        overrides = [
            {"key": row["key"], "value": row["value"], "updated_at": row["updated_at"]}
            for row in db.list_overrides()
        ]
        return {
            "overrides": overrides,
            "effective_values": _serialize_public_settings(request.app.state.settings),
            "overridable_keys": sorted(
                key
                for key in request.app.state.settings.public_settings()
                if request.app.state.settings.is_overridable(key)
            ),
            "effective": request.app.state.settings.public_settings(),
            "gateway_auth": _gateway_auth_status(request),
        }

    @router.get("/admin/gateway-auth")
    async def admin_gateway_auth(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict:
        _check_gateway_auth(request, authorization)
        return _gateway_auth_status(request)

    @router.put("/admin/gateway-auth")
    async def admin_set_gateway_auth(
        payload: dict,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict:
        _check_gateway_auth(request, authorization)
        key = str(payload.get("key", "")).strip()
        if not key:
            raise HTTPException(status_code=400, detail="gateway auth key cannot be empty")

        config = create_gateway_auth_config(key)
        db = request.app.state.db
        db.set_gateway_auth_config(
            mode="enabled",
            token_salt_b64=config.token_salt_b64,
            token_hash_b64=config.token_hash_b64,
        )
        db.writer.flush()
        request.app.state.reload_settings()
        runtime_log(
            logger,
            "admin.gateway_auth.updated",
            verbosity="verbose",
            message="Updated managed gateway auth key",
        )
        return _gateway_auth_status(request)

    @router.delete("/admin/gateway-auth")
    async def admin_disable_gateway_auth(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict:
        _check_gateway_auth(request, authorization)
        db = request.app.state.db
        db.set_gateway_auth_config(mode="disabled")
        db.writer.flush()
        request.app.state.reload_settings()
        runtime_log(
            logger,
            "admin.gateway_auth.disabled",
            verbosity="verbose",
            message="Disabled gateway auth via managed override",
        )
        return _gateway_auth_status(request)

    @router.post("/admin/gateway-auth/inherit")
    async def admin_inherit_gateway_auth(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict:
        _check_gateway_auth(request, authorization)
        db = request.app.state.db
        db.set_gateway_auth_config(mode="inherit")
        db.writer.flush()
        request.app.state.reload_settings()
        runtime_log(
            logger,
            "admin.gateway_auth.inherit",
            verbosity="verbose",
            message="Gateway auth reverted to environment inheritance",
        )
        return _gateway_auth_status(request)

    @router.get("/admin/secrets")
    async def admin_secrets(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict:
        _check_gateway_auth(request, authorization)
        return {
            "secret_management": dict(getattr(request.app.state, "secret_management_status", {})),
            "secrets": _list_secret_slots(request),
        }

    @router.post("/admin/secrets/vault/setup")
    async def admin_setup_secret_vault(
        payload: dict,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict:
        _check_gateway_auth(request, authorization)
        if getattr(request.app.state, "secret_vault_config", None) is not None:
            raise HTTPException(status_code=409, detail="secret vault is already configured")

        password = _parse_secret_password(payload)
        db = request.app.state.db
        try:
            vault_config, secret_store = create_vault_config(password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        db.set_secret_vault_config(
            salt_b64=vault_config.salt_b64,
            verifier_encrypted=vault_config.verifier_encrypted,
        )
        db.writer.flush()
        request.app.state.secret_store = secret_store
        request.app.state.reload_settings()
        runtime_log(
            logger,
            "admin.secret_vault.configured",
            verbosity="verbose",
            message="Configured and unlocked secret vault",
        )
        return {
            "status": "configured",
            "secret_management": dict(getattr(request.app.state, "secret_management_status", {})),
            "secrets": _list_secret_slots(request),
        }

    @router.post("/admin/secrets/vault/unlock")
    async def admin_unlock_secret_vault(
        payload: dict,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict:
        _check_gateway_auth(request, authorization)
        vault_config = getattr(request.app.state, "secret_vault_config", None)
        if vault_config is None:
            raise HTTPException(status_code=409, detail="secret vault is not configured")

        password = _parse_secret_password(payload)
        try:
            secret_store = unlock_vault(password, vault_config)
        except SecretStorePasswordError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        request.app.state.secret_store = secret_store
        request.app.state.reload_settings()
        runtime_log(
            logger,
            "admin.secret_vault.unlocked",
            verbosity="verbose",
            message="Unlocked secret vault",
        )
        return {
            "status": "unlocked",
            "secret_management": dict(getattr(request.app.state, "secret_management_status", {})),
            "secrets": _list_secret_slots(request),
        }

    @router.post("/admin/secrets/vault/lock")
    async def admin_lock_secret_vault(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict:
        _check_gateway_auth(request, authorization)
        if getattr(request.app.state, "secret_vault_config", None) is None:
            raise HTTPException(status_code=409, detail="secret vault is not configured")

        request.app.state.secret_store = None
        request.app.state.reload_settings()
        runtime_log(
            logger,
            "admin.secret_vault.locked",
            verbosity="verbose",
            message="Locked secret vault",
        )
        return {
            "status": "locked",
            "secret_management": dict(getattr(request.app.state, "secret_management_status", {})),
            "secrets": _list_secret_slots(request),
        }

    @router.put("/admin/secrets/{secret_key:path}")
    async def admin_set_secret(
        secret_key: str,
        payload: dict,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict:
        _check_gateway_auth(request, authorization)
        normalized_key = secret_key.strip()
        if not Settings.is_managed_secret_key(normalized_key):
            raise HTTPException(status_code=400, detail="secret key not allowed")
        if "value" not in payload:
            raise HTTPException(status_code=400, detail="missing value")
        value = str(payload["value"]).strip()
        if not value:
            raise HTTPException(status_code=400, detail="secret value cannot be empty")

        secret_store = _require_secret_management_unlocked(request)
        db = request.app.state.db
        db.set_managed_secret(normalized_key, secret_store.encrypt(value))
        db.writer.flush()
        request.app.state.reload_settings()
        runtime_log(
            logger,
            "admin.secret.updated",
            verbosity="verbose",
            message="Updated managed secret",
            key=normalized_key,
        )
        return {"status": "updated", "key": normalized_key, "secrets": _list_secret_slots(request)}

    @router.delete("/admin/secrets/{secret_key:path}")
    async def admin_delete_secret(
        secret_key: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict:
        _check_gateway_auth(request, authorization)
        normalized_key = secret_key.strip()
        if not Settings.is_managed_secret_key(normalized_key):
            raise HTTPException(status_code=400, detail="secret key not allowed")

        _require_secret_management_unlocked(request)
        db = request.app.state.db
        db.delete_managed_secret(normalized_key)
        db.writer.flush()
        request.app.state.reload_settings()
        runtime_log(
            logger,
            "admin.secret.deleted",
            verbosity="verbose",
            message="Deleted managed secret",
            key=normalized_key,
        )
        return {"status": "deleted", "key": normalized_key, "secrets": _list_secret_slots(request)}

    @router.get("/admin/uninstall")
    async def admin_uninstall_info(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict:
        _check_gateway_auth(request, authorization)
        return _uninstall_info(request.app.state.settings)

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
    async def admin_refresh(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict:
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
        provider_id: str | None = None,
        request_source: str | None = None,
        model_id: str | None = None,
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
        if provider_id:
            where.append("provider_id=?")
            params.append(provider_id.strip())
        if request_source:
            where.append("request_source=?")
            params.append(request_source.strip())
        if model_id:
            where.append("selected_model_id=?")
            params.append(model_id.strip())

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        query = (
            "SELECT request_id, timestamp, request_source, selected_model_id, provider_id, "
            "selected_provider_model_id, selected_tokenizer_family, attempt_index, was_fallback, "
            "estimated_prompt_tokens, selected_context_window, prompt_tokens, completion_tokens, "
            "total_tokens, latency_ms, ttfb_ms, success, gateway_error_category, error_code, "
            "error_message, was_streaming, had_tools, had_vision "
            f"FROM request_log {where_sql} ORDER BY timestamp DESC, id DESC LIMIT ?"
        )
        params.append(capped_limit)

        with db.read_conn() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()

        logs = [
            {
                "request_id": row[0],
                "timestamp": row[1],
                "request_source": row[2],
                "selected_model_id": row[3],
                "provider_id": row[4],
                "selected_provider_model_id": row[5],
                "selected_tokenizer_family": row[6],
                "attempt_index": row[7],
                "was_fallback": bool(row[8]),
                "estimated_prompt_tokens": row[9],
                "selected_context_window": row[10],
                "prompt_tokens": row[11],
                "completion_tokens": row[12],
                "total_tokens": row[13],
                "latency_ms": row[14],
                "ttfb_ms": row[15],
                "success": bool(row[16]),
                "gateway_error_category": row[17],
                "error_code": row[18],
                "error_message": row[19],
                "was_streaming": bool(row[20]),
                "had_tools": bool(row[21]),
                "had_vision": bool(row[22]),
            }
            for row in rows
        ]
        return {
            "logs": logs,
            "count": len(logs),
            "limit": capped_limit,
            "filters": {
                "success_only": success_only,
                "provider_id": provider_id,
                "request_source": request_source,
                "model_id": model_id,
            },
        }

    @router.post("/v1/chat/completions")
    async def chat_completions(
        payload: dict, request: Request, authorization: str | None = Header(default=None)
    ):
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

                    def categorize_stream_error(
                        status_code: int | None,
                        error_code: str | None,
                        message: str,
                        _provider_name: str = provider_name,
                    ) -> ProviderErrorCategorization:
                        return cast(
                            ProviderErrorCategorization,
                            registry.categorize_error(
                                _provider_name,
                                status_code,
                                error_code,
                                message,
                            ),
                        )

                    stream_result = await provider.stream_chat_completions(
                        payload, model=model_name
                    )
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
                            categorize_stream_error,
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
                mark_failure(db, model_id, str(last_provider_error), settings=settings)
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
                raise HTTPException(
                    status_code=_provider_error_status(exc), detail=str(exc)
                ) from exc
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
            error_category=last_provider_error.category
            if last_provider_error is not None
            else None,
        )
        raise HTTPException(
            status_code=502, detail=str(last_provider_error or "all candidates failed")
        )

    return router
