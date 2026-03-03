from __future__ import annotations

from src.db import Database, utc_now_iso
from src.health import mark_failure, mark_success


def _insert_model(db: Database, model_id: str) -> None:
    now = utc_now_iso()
    db.writer.enqueue(
        """
        INSERT INTO models(
            id, name, provider_id, provider_model_id, provider_base_url, provider_api_key_env,
            discovered_at, last_seen_at, is_active, is_healthy
        ) VALUES (?, ?, 'openrouter', ?, 'https://example.com', 'OPENROUTER_API_KEY', ?, ?, 1, 1)
        """,
        (model_id, model_id, model_id, now, now),
    )


def test_mark_failure_applies_cooldown_and_backoff(tmp_path):
    db = Database(str(tmp_path / "health.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "model-a")
    db.writer.flush()

    mark_failure(db, "model-a", "first failure")
    db.writer.flush()

    with db.read_conn() as conn:
        row = conn.execute(
            "SELECT consecutive_failures, backoff_level, cooldown_until FROM models WHERE id='model-a'"
        ).fetchone()

    db.writer.stop()

    assert row is not None
    assert row[0] == 1
    assert row[1] == 1
    assert row[2] is not None


def test_mark_success_clears_backoff_state(tmp_path):
    db = Database(str(tmp_path / "health-success.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "model-a")
    db.writer.flush()

    mark_failure(db, "model-a", "failure")
    db.writer.flush()
    mark_success(db, "model-a")
    db.writer.flush()

    with db.read_conn() as conn:
        row = conn.execute(
            "SELECT consecutive_failures, backoff_level, cooldown_until, is_healthy FROM models WHERE id='model-a'"
        ).fetchone()

    db.writer.stop()

    assert row is not None
    assert row[0] == 0
    assert row[1] == 0
    assert row[2] is None
    assert row[3] == 1
