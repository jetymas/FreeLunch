from __future__ import annotations

from src.db import Database


def pick_model(db: Database, requested_model: str | None = None, requires_tools: bool = False) -> tuple[str, str]:
    with db.read_conn() as conn:
        if requested_model and requested_model != "auto":
            row = conn.execute(
                """
                SELECT provider, model_name
                FROM models
                WHERE model_name = ? AND is_healthy = 1
                ORDER BY score DESC
                LIMIT 1
                """,
                (requested_model,),
            ).fetchone()
            if row:
                return row[0], row[1]

        row = conn.execute(
            """
            SELECT provider, model_name
            FROM models
            WHERE is_healthy = 1
              AND (? = 0 OR supports_tools = 1)
            ORDER BY score DESC
            LIMIT 1
            """,
            (1 if requires_tools else 0,),
        ).fetchone()

    if not row:
        raise RuntimeError("No routable healthy model found")

    return row[0], row[1]
