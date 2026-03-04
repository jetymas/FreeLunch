from __future__ import annotations

from src.config import Settings
from src.db import Database, utc_now_iso
from src.ranking import recompute_ranking


def _insert_model(
    db: Database, model_id: str, *, elo: float, llm: float, avg_latency_ms: float
) -> None:
    now = utc_now_iso()
    db.writer.enqueue(
        """
        INSERT INTO models(
            id, name, provider_id, provider_model_id, provider_base_url, provider_api_key_env,
            chatbot_arena_elo, open_llm_score, avg_latency_ms, discovered_at, last_seen_at, is_active, is_healthy
        ) VALUES (?, ?, 'openrouter', ?, 'https://example.com', 'OPENROUTER_API_KEY', ?, ?, ?, ?, ?, 1, 1)
        """,
        (model_id, model_id, model_id, elo, llm, avg_latency_ms, now, now),
    )


def test_recompute_ranking_prefers_better_success_rate(tmp_path):
    db = Database(str(tmp_path / "rank.db"))
    db.init()
    db.writer.start()

    _insert_model(db, "model-good", elo=1200, llm=0.75, avg_latency_ms=400)
    _insert_model(db, "model-bad", elo=1200, llm=0.75, avg_latency_ms=400)

    for _ in range(12):
        db.writer.enqueue(
            """
            INSERT INTO request_log(timestamp, selected_model_id, provider_id, success, latency_ms, request_source)
            VALUES (?, 'model-good', 'openrouter', 1, 350, 'client')
            """,
            (utc_now_iso(),),
        )

    for _ in range(12):
        db.writer.enqueue(
            """
            INSERT INTO request_log(timestamp, selected_model_id, provider_id, success, latency_ms, request_source)
            VALUES (?, 'model-bad', 'openrouter', 0, 350, 'client')
            """,
            (utc_now_iso(),),
        )

    db.writer.flush()
    recompute_ranking(db)
    db.writer.flush()

    with db.read_conn() as conn:
        rows = conn.execute(
            "SELECT id, composite_score FROM models ORDER BY composite_score DESC"
        ).fetchall()

    db.writer.stop()

    assert rows[0][0] == "model-good"


def test_recompute_ranking_uses_telemetry_latency_over_stale_model_latency(tmp_path):
    db = Database(str(tmp_path / "rank-latency.db"))
    db.init()
    db.writer.start()

    _insert_model(db, "model-fast", elo=1200, llm=0.75, avg_latency_ms=1000)
    _insert_model(db, "model-slow", elo=1200, llm=0.75, avg_latency_ms=100)

    for _ in range(10):
        db.writer.enqueue(
            """
            INSERT INTO request_log(timestamp, selected_model_id, provider_id, success, latency_ms, request_source)
            VALUES (?, 'model-fast', 'openrouter', 1, 120, 'client')
            """,
            (utc_now_iso(),),
        )
        db.writer.enqueue(
            """
            INSERT INTO request_log(timestamp, selected_model_id, provider_id, success, latency_ms, request_source)
            VALUES (?, 'model-slow', 'openrouter', 1, 1800, 'client')
            """,
            (utc_now_iso(),),
        )

    db.writer.flush()
    recompute_ranking(db)
    db.writer.flush()

    with db.read_conn() as conn:
        rows = conn.execute(
            "SELECT id, composite_score FROM models ORDER BY composite_score DESC"
        ).fetchall()

    db.writer.stop()

    assert rows[0][0] == "model-fast"


def test_recompute_ranking_uses_openrouter_rank_when_telemetry_is_insufficient(tmp_path):
    db = Database(str(tmp_path / "rank-cold-start.db"))
    db.init()
    db.writer.start()

    now = utc_now_iso()
    db.writer.enqueue(
        """
        INSERT INTO models(
            id, name, provider_id, provider_model_id, provider_base_url, provider_api_key_env,
            openrouter_rank, discovered_at, last_seen_at, is_active, is_healthy
        ) VALUES
            ('model-ranked-high', 'model-ranked-high', 'openrouter', 'model-ranked-high', 'https://example.com', 'OPENROUTER_API_KEY', 1, ?, ?, 1, 1),
            ('model-ranked-low', 'model-ranked-low', 'openrouter', 'model-ranked-low', 'https://example.com', 'OPENROUTER_API_KEY', 25, ?, ?, 1, 1)
        """,
        (now, now, now, now),
    )
    db.writer.flush()

    recompute_ranking(db, settings=Settings())
    db.writer.flush()

    with db.read_conn() as conn:
        rows = conn.execute(
            "SELECT id, composite_score FROM models ORDER BY composite_score DESC"
        ).fetchall()

    db.writer.stop()

    assert rows[0][0] == "model-ranked-high"
