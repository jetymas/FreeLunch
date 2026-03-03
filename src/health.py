from __future__ import annotations

from .db import Database


def run_startup_health_pass(db: Database) -> None:
    db.enqueue("UPDATE models SET is_healthy = is_healthy, last_checked_at = CURRENT_TIMESTAMP")
