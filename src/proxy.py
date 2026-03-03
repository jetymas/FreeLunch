from __future__ import annotations

from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .providers.base import ProviderFatalError, ProviderRetryableError
from .routing import NoRoutableModelError, list_candidate_models


router = APIRouter()


class ChatCompletionRequest(BaseModel):
    model: str = "auto"
    messages: list[dict] = Field(default_factory=list)
    stream: bool = False
    tools: list[dict] | None = None


def require_gateway_key(request: Request) -> None:
    app = request.app
    configured = app.state.settings.gateway_api_key
    if not configured:
        return
    auth = request.headers.get("authorization", "")
    expected = f"Bearer {configured}"
    if auth != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
def readyz(request: Request) -> dict[str, str]:
    if not request.app.state.ready:
        raise HTTPException(status_code=503, detail="not ready")
    return {"status": "ready"}


@router.get("/v1/models", dependencies=[Depends(require_gateway_key)])
def list_models(request: Request) -> dict[str, list[dict[str, str]]]:
    conn = request.app.state.db.connect()
    try:
        rows = conn.execute(
            "SELECT provider, model_name FROM models WHERE is_healthy = 1 ORDER BY score DESC"
        ).fetchall()
    finally:
        conn.close()
    data = [{"id": row["model_name"], "object": "model", "owned_by": row["provider"]} for row in rows]
    return {"object": "list", "data": data}


@router.post("/v1/chat/completions", dependencies=[Depends(require_gateway_key)])
def chat_completions(request: Request, body: ChatCompletionRequest):
    if not request.app.state.ready:
        raise HTTPException(status_code=503, detail="gateway not ready")

    conn = request.app.state.db.connect()
    started = perf_counter()
    body_dict = body.model_dump(exclude_none=True)
    try:
        try:
            candidates = list_candidate_models(
                conn,
                body.model,
                require_tools=bool(body.tools),
                limit=request.app.state.settings.max_failover_attempts,
            )
        except NoRoutableModelError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        last_error = "unknown provider error"
        for candidate in candidates:
            provider = request.app.state.registry.get(candidate["provider"])
            try:
                result = provider.chat_completions(body_dict, candidate["model_name"])
                latency_ms = int((perf_counter() - started) * 1000)
                request.app.state.db.enqueue(
                    """
                    INSERT INTO request_log(provider, model_name, latency_ms, success, prompt_tokens, completion_tokens, total_tokens)
                    VALUES (?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        candidate["provider"],
                        candidate["model_name"],
                        latency_ms,
                        result.prompt_tokens,
                        result.completion_tokens,
                        result.total_tokens,
                    ),
                )
                return result.payload
            except ProviderRetryableError as exc:
                last_error = str(exc)
                request.app.state.db.enqueue(
                    """
                    INSERT INTO request_log(provider, model_name, latency_ms, success, error_type)
                    VALUES (?, ?, ?, 0, ?)
                    """,
                    (
                        candidate["provider"],
                        candidate["model_name"],
                        int((perf_counter() - started) * 1000),
                        "retryable_provider_error",
                    ),
                )
                continue
            except ProviderFatalError as exc:
                last_error = str(exc)
                request.app.state.db.enqueue(
                    """
                    INSERT INTO request_log(provider, model_name, latency_ms, success, error_type)
                    VALUES (?, ?, ?, 0, ?)
                    """,
                    (
                        candidate["provider"],
                        candidate["model_name"],
                        int((perf_counter() - started) * 1000),
                        "fatal_provider_error",
                    ),
                )
                raise HTTPException(status_code=400, detail=last_error) from exc

        raise HTTPException(status_code=503, detail=last_error)
    finally:
        conn.close()
