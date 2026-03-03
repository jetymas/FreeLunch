from __future__ import annotations

from src.db import Database, utc_now_iso


def startup_health_pass(db: Database, max_models: int = 3) -> int:
    now = utc_now_iso()
    with db.read_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM models WHERE is_active=1 ORDER BY composite_score DESC LIMIT ?",
            (max_models,),
        ).fetchall()

    for (model_id,) in rows:
        db.writer.enqueue(
            "UPDATE models SET is_healthy=1, last_health_check=?, last_success_at=? WHERE id=?",
            (now, now, model_id),
        )
    return len(rows)


def mark_failure(db: Database, model_id: str, error: str) -> None:
    now = utc_now_iso()
    db.writer.enqueue(
        """
        UPDATE models
        SET consecutive_failures=consecutive_failures+1,
            last_failure_at=?,
            last_error=?,
            is_healthy=CASE WHEN consecutive_failures+1 >= 3 THEN 0 ELSE is_healthy END
        WHERE id=?
        """,
        (now, error[:500], model_id),
    )


def mark_success(db: Database, model_id: str) -> None:
    now = utc_now_iso()
    db.writer.enqueue(
        """
        UPDATE models
        SET consecutive_failures=0,
            is_healthy=1,
            last_success_at=?,
            last_routed_at=?
        WHERE id=?
        """,
        (now, now, model_id),
    )
