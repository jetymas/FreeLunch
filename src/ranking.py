from __future__ import annotations

from src.db import Database


def recompute_ranking(db: Database) -> int:
    with db.read_conn() as conn:
        rows = conn.execute(
            "SELECT id, score, is_healthy FROM models"
        ).fetchall()

    updates = 0
    for row_id, score, healthy in rows:
        adjusted = float(score) + (10.0 if healthy else -50.0)
        db.writer.enqueue("UPDATE models SET score=? WHERE id=?", (adjusted, row_id))
        updates += 1
    return updates
