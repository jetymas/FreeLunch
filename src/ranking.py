from __future__ import annotations

from src.db import Database, utc_now_iso


def recompute_ranking(db: Database) -> int:
    with db.read_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, is_healthy, COALESCE(chatbot_arena_elo, 0), COALESCE(open_llm_score, 0),
                   COALESCE(avg_latency_ms, 0), COALESCE(consecutive_failures, 0)
            FROM models
            WHERE is_active=1
            """
        ).fetchall()

    updates = 0
    now = utc_now_iso()
    for model_id, healthy, elo, llm, latency, failures in rows:
        score = (elo * 0.05) + (llm * 10.0) + (15 if healthy else -100) - (latency / 200.0) - (failures * 5)
        db.writer.enqueue(
            "UPDATE models SET composite_score=?, score_updated_at=? WHERE id=?",
            (float(score), now, model_id),
        )
        updates += 1
    return updates
