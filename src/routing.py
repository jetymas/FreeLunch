from __future__ import annotations

import sqlite3


class NoRoutableModelError(RuntimeError):
    pass


def list_candidate_models(
    conn: sqlite3.Connection,
    requested_model: str | None,
    require_tools: bool = False,
    limit: int = 3,
) -> list[sqlite3.Row]:
    if requested_model and requested_model != "auto":
        row = conn.execute(
            "SELECT * FROM models WHERE model_name = ? AND is_healthy = 1 ORDER BY score DESC LIMIT 1",
            (requested_model,),
        ).fetchone()
        if row:
            return [row]

    sql = "SELECT * FROM models WHERE is_healthy = 1"
    if require_tools:
        sql += " AND supports_tools = 1"
    sql += " ORDER BY score DESC, updated_at DESC LIMIT ?"
    rows = conn.execute(sql, (limit,)).fetchall()
    if not rows:
        raise NoRoutableModelError("no healthy candidate model")
    return list(rows)
