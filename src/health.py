from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.db import Database, utc_now_iso


def _iso_after(seconds: int) -> str:
    return (
        (datetime.now(timezone.utc) + timedelta(seconds=seconds))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


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

    with db.read_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(backoff_level, 0) FROM models WHERE id=?",
            (model_id,),
        ).fetchone()

    if row is None:
        return

    current_backoff = int(row[0])
    next_backoff = min(current_backoff + 1, 6)
    cooldown_seconds = min(30 * (2 ** max(next_backoff - 1, 0)), 30 * 60)
    cooldown_until = _iso_after(cooldown_seconds)

    db.writer.enqueue(
        """
        UPDATE models
        SET consecutive_failures=consecutive_failures+1,
            last_failure_at=?,
            last_error=?,
            backoff_level=?,
            cooldown_until=?,
            is_healthy=CASE WHEN consecutive_failures+1 >= 3 THEN 0 ELSE is_healthy END
        WHERE id=?
        """,
        (now, error[:500], next_backoff, cooldown_until, model_id),
    )


def mark_success(db: Database, model_id: str) -> None:
    now = utc_now_iso()
    db.writer.enqueue(
        """
        UPDATE models
        SET consecutive_failures=0,
            backoff_level=0,
            cooldown_until=NULL,
            is_healthy=1,
            last_success_at=?,
            last_routed_at=?
        WHERE id=?
        """,
        (now, now, model_id),
    )
