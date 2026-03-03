from __future__ import annotations

from src.db import Database


def startup_health_pass(db: Database, max_models: int = 3) -> int:
    with db.read_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM models ORDER BY score DESC LIMIT ?", (max_models,)
        ).fetchall()

    for (row_id,) in rows:
        db.writer.enqueue("UPDATE models SET is_healthy=1 WHERE id=?", (row_id,))

    return len(rows)
