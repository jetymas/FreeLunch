from __future__ import annotations

from .db import Database


def recompute_rankings(db: Database) -> None:
    db.enqueue(
        """
        UPDATE models
        SET score = CASE WHEN is_healthy = 1 THEN score ELSE 0.0 END,
            updated_at = CURRENT_TIMESTAMP
        """
    )
