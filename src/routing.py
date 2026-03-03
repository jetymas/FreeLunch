from __future__ import annotations

from dataclasses import dataclass

from src.db import Database, utc_now_iso


@dataclass(slots=True)
class RoutingRequirements:
    requested_model: str | None = None
    requires_tools: bool = False
    requires_vision: bool = False
    requires_streaming: bool = False
    requires_structured_output: bool = False
    min_context_window: int = 0
    min_output_tokens: int = 0


def pick_candidates(db: Database, req: RoutingRequirements, limit: int = 3) -> list[dict[str, str]]:
    now = utc_now_iso()
    with db.read_conn() as conn:
        if req.requested_model and req.requested_model != "auto":
            rows = conn.execute(
                """
                SELECT id, provider_id, provider_model_id
                FROM models
                WHERE (id=? OR provider_model_id=?)
                  AND is_active=1
                  AND is_healthy=1
                  AND (cooldown_until IS NULL OR cooldown_until < ?)
                ORDER BY composite_score DESC
                LIMIT ?
                """,
                (req.requested_model, req.requested_model, now, limit),
            ).fetchall()
            if rows:
                return [
                    {"id": model_id, "provider_id": provider_id, "provider_model_id": provider_model_id}
                    for model_id, provider_id, provider_model_id in rows
                ]

        rows = conn.execute(
            """
            SELECT id, provider_id, provider_model_id
            FROM models
            WHERE is_active=1
              AND is_healthy=1
              AND (cooldown_until IS NULL OR cooldown_until < ?)
              AND (? = 0 OR supports_tools = 1)
              AND (? = 0 OR supports_vision = 1)
              AND (? = 0 OR supports_streaming = 1)
              AND (? = 0 OR supports_structured_output = 1)
              AND context_window >= ?
              AND (max_output_tokens IS NULL OR max_output_tokens >= ?)
            ORDER BY composite_score DESC
            LIMIT ?
            """,
            (
                now,
                1 if req.requires_tools else 0,
                1 if req.requires_vision else 0,
                1 if req.requires_streaming else 0,
                1 if req.requires_structured_output else 0,
                req.min_context_window,
                req.min_output_tokens,
                limit,
            ),
        ).fetchall()

    return [
        {"id": model_id, "provider_id": provider_id, "provider_model_id": provider_model_id}
        for model_id, provider_id, provider_model_id in rows
    ]
