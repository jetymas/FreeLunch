from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, HTTPException, Request

from src.routing import pick_model


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @router.get("/readyz")
    async def readyz(request: Request) -> dict:
        if not request.app.state.ready:
            raise HTTPException(status_code=503, detail="not ready")
        return {"status": "ready"}

    @router.get("/v1/models")
    async def list_models(request: Request) -> dict:
        db = request.app.state.db
        with db.read_conn() as conn:
            rows = conn.execute(
                "SELECT provider, model_name FROM models WHERE is_healthy=1 ORDER BY score DESC"
            ).fetchall()
        return {
            "object": "list",
            "data": [
                {"id": model_name, "object": "model", "owned_by": provider}
                for provider, model_name in rows
            ],
        }

    @router.post("/v1/chat/completions")
    async def chat_completions(payload: dict, request: Request) -> dict:
        if not request.app.state.ready:
            raise HTTPException(status_code=503, detail="gateway not ready")

        db = request.app.state.db
        registry = request.app.state.registry

        model = payload.get("model", "auto")
        requires_tools = bool(payload.get("tools"))

        start = time.monotonic()
        request_id = str(uuid.uuid4())
        try:
            provider_name, model_name = pick_model(
                db=db,
                requested_model=model,
                requires_tools=requires_tools,
            )
            provider = registry.get(provider_name)
            response = await provider.chat_completion(payload, model_name=model_name)
            success = 1
            error_type = None
            return response
        except Exception as exc:
            success = 0
            error_type = exc.__class__.__name__
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            db.writer.enqueue(
                """
                INSERT INTO request_log(request_id, provider, model_name, success, latency_ms, error_type)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    provider_name if 'provider_name' in locals() else None,
                    model_name if 'model_name' in locals() else None,
                    success,
                    elapsed_ms,
                    error_type,
                ),
            )

    return router
