from __future__ import annotations

from src.db import DB_SCHEMA_VERSION, Database
from src.discover import run_discovery


def test_init_applies_all_schema_migrations(tmp_path):
    db = Database(str(tmp_path / "db-init.db"))
    db.init()

    with db.read_conn() as conn:
        versions = [
            int(row["version"])
            for row in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]

    assert versions == list(range(1, DB_SCHEMA_VERSION + 1))


def test_set_override_uses_canonical_utc_timestamp(tmp_path):
    db = Database(str(tmp_path / "db-overrides.db"))
    db.init()
    db.writer.start()

    db.set_override("health.probe_interval_minutes", 15)
    db.writer.flush()

    with db.read_conn() as conn:
        row = conn.execute(
            "SELECT key, value, updated_at FROM config_overrides WHERE key='health.probe_interval_minutes'"
        ).fetchone()

    db.writer.stop()

    assert row is not None
    assert row["value"] == "15"
    assert row["updated_at"].endswith("Z")
    assert "T" in row["updated_at"]


def test_leaderboard_cache_round_trip_respects_freshness(tmp_path):
    db = Database(str(tmp_path / "db-cache.db"))
    db.init()
    db.writer.start()

    db.upsert_leaderboard_cache(
        "llama-3-3-70b",
        chatbot_arena_elo=1234.5,
        open_llm_avg_score=77.7,
    )
    db.writer.flush()

    row = db.get_leaderboard_cache("llama-3-3-70b")
    db.writer.stop()

    assert row is not None
    assert row["chatbot_arena_elo"] == 1234.5
    assert row["open_llm_avg_score"] == 77.7
    assert row["fetched_at"].endswith("Z")


class _FakeProvider:
    name = "fake"

    async def discover_models(self):
        return [
            {
                "id": "fake/model-a",
                "name": "Model A",
                "provider_id": "fake",
                "endpoint_id": "endpoint-1",
                "provider_model_id": "model-a",
                "provider_base_url": "https://example.com",
                "provider_api_key_env": "FAKE_API_KEY",
                "provider_options_json": '{"tier":"free"}',
                "context_window": 8192,
                "max_output_tokens": 1024,
                "tokenizer_family": "llama3",
                "supports_tools": 1,
                "supports_streaming": 1,
                "supports_vision": 0,
                "supports_structured_output": 1,
                "supports_system_messages": 1,
                "openrouter_rank": 7,
                "chatbot_arena_elo": 1200.0,
                "open_llm_score": 0.8,
                "is_healthy": 1,
            }
        ]


class _FakeRegistry:
    def all(self):
        return [_FakeProvider()]


def test_discovery_upsert_preserves_extended_model_fields(tmp_path):
    import asyncio

    db = Database(str(tmp_path / "db-discovery.db"))
    db.init()
    db.writer.start()

    asyncio.run(run_discovery(db, _FakeRegistry()))
    db.writer.flush()

    with db.read_conn() as conn:
        row = conn.execute(
            """
            SELECT endpoint_id, provider_options_json, tokenizer_family, supports_structured_output,
                   supports_system_messages, openrouter_rank, chatbot_arena_elo, open_llm_score
            FROM models
            WHERE id='fake/model-a'
            """
        ).fetchone()

    db.writer.stop()

    assert row is not None
    assert row["endpoint_id"] == "endpoint-1"
    assert row["provider_options_json"] == '{"tier":"free"}'
    assert row["tokenizer_family"] == "llama3"
    assert row["supports_structured_output"] == 1
    assert row["supports_system_messages"] == 1
    assert row["openrouter_rank"] == 7
    assert row["chatbot_arena_elo"] == 1200.0
    assert row["open_llm_score"] == 0.8
