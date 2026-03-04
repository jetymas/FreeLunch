from __future__ import annotations

import sqlite3

from src.benchmarks import normalize_model_name
from src.db import DB_SCHEMA_VERSION, HIGH_PRIORITY_QUEUE_RESERVE, Database
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


def test_read_conn_closes_connection_after_context_exit(tmp_path):
    db = Database(str(tmp_path / "db-read-close.db"))
    db.init()

    with db.read_conn() as conn:
        row = conn.execute("SELECT 1").fetchone()

    assert row is not None
    try:
        conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        closed = True
    else:
        closed = False

    assert closed is True


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


def test_leaderboard_cache_upsert_preserves_existing_source_values(tmp_path):
    db = Database(str(tmp_path / "db-cache-merge.db"))
    db.init()
    db.writer.start()

    db.upsert_leaderboard_cache(
        "llama-3-3-70b",
        chatbot_arena_elo=1234.5,
        open_llm_avg_score=None,
    )
    db.writer.flush()
    db.upsert_leaderboard_cache(
        "llama-3-3-70b",
        chatbot_arena_elo=None,
        open_llm_avg_score=88.8,
    )
    db.writer.flush()

    row = db.get_leaderboard_cache("llama-3-3-70b")
    db.writer.stop()

    assert row is not None
    assert row["chatbot_arena_elo"] == 1234.5
    assert row["open_llm_avg_score"] == 88.8


def test_prune_old_logs_removes_entries_older_than_retention(tmp_path):
    db = Database(str(tmp_path / "db-prune-logs.db"))
    db.init()
    db.writer.start()

    db.writer.enqueue(
        """
        INSERT INTO request_log(
            timestamp, request_source, selected_model_id, provider_id, success
        ) VALUES
            ('2025-01-01T00:00:00Z', 'client', 'model-old', 'openrouter', 1),
            ('2099-01-01T00:00:00Z', 'client', 'model-new', 'openrouter', 1)
        """
    )
    db.writer.flush()

    deleted = db.prune_old_logs(retention_days=30)

    with db.read_conn() as conn:
        rows = conn.execute(
            "SELECT selected_model_id FROM request_log ORDER BY selected_model_id"
        ).fetchall()

    db.writer.stop()

    assert deleted == 1
    assert [row["selected_model_id"] for row in rows] == ["model-new"]


def test_client_request_logs_respect_logging_policy_without_blocking_probe_logs(tmp_path):
    db = Database(
        str(tmp_path / "db-log-policy.db"),
        request_log_enabled=False,
        request_log_queue_size=1,
    )
    db.init()
    db.writer.start()

    client_logged = db.log_request(
        {
            "request_id": "client-1",
            "request_source": "client",
            "selected_model_id": "model-client",
            "provider_id": "openrouter",
            "success": True,
        }
    )
    probe_logged = db.log_request(
        {
            "request_id": "probe-1",
            "request_source": "probe",
            "selected_model_id": "model-probe",
            "provider_id": "openrouter",
            "success": True,
        }
    )
    db.writer.flush()

    with db.read_conn() as conn:
        rows = conn.execute(
            "SELECT request_id, request_source FROM request_log ORDER BY request_id"
        ).fetchall()

    db.writer.stop()

    assert client_logged is False
    assert probe_logged is True
    assert [(row["request_id"], row["request_source"]) for row in rows] == [
        ("probe-1", "probe"),
    ]


def test_client_request_logs_drop_when_low_priority_queue_is_full(tmp_path):
    db = Database(
        str(tmp_path / "db-log-cap.db"),
        request_log_enabled=True,
        request_log_queue_size=1,
    )

    first_logged = db.log_request(
        {
            "request_id": "client-1",
            "request_source": "client",
            "selected_model_id": "model-a",
            "provider_id": "openrouter",
            "success": True,
        }
    )
    second_logged = db.log_request(
        {
            "request_id": "client-2",
            "request_source": "client",
            "selected_model_id": "model-b",
            "provider_id": "openrouter",
            "success": True,
        }
    )

    assert first_logged is True
    assert second_logged is False
    assert db.writer.dropped_low_priority_logs() == 1


def test_high_priority_writes_use_reserved_queue_capacity(tmp_path):
    db = Database(
        str(tmp_path / "db-queue-reserve.db"),
        request_log_enabled=True,
        request_log_queue_size=1,
    )
    db.init()

    client_logged = db.log_request(
        {
            "request_id": "client-1",
            "request_source": "client",
            "selected_model_id": "model-a",
            "provider_id": "openrouter",
            "success": True,
        }
    )
    db.set_override("gateway.port", 8080)

    db.writer.start()
    db.writer.flush()

    with db.read_conn() as conn:
        override = conn.execute(
            "SELECT value FROM config_overrides WHERE key='gateway.port'"
        ).fetchone()

    db.writer.stop()

    assert client_logged is True
    assert override is not None
    assert override["value"] == "8080"


def test_writer_queue_capacity_reserves_space_for_metadata_writes(tmp_path):
    db = Database(
        str(tmp_path / "db-queue-capacity.db"),
        request_log_enabled=True,
        request_log_queue_size=3,
    )

    assert db.writer.queue_capacity() == 3 + HIGH_PRIORITY_QUEUE_RESERVE


def test_log_request_persists_token_estimation_observability_fields(tmp_path):
    db = Database(str(tmp_path / "db-token-observability.db"))
    db.init()
    db.writer.start()

    logged = db.log_request(
        {
            "request_id": "client-1",
            "request_source": "client",
            "selected_model_id": "openrouter/model-a",
            "provider_id": "openrouter",
            "selected_provider_model_id": "model-a",
            "selected_tokenizer_family": "cl100k_base",
            "estimated_prompt_tokens": 321,
            "selected_context_window": 16384,
            "prompt_tokens": 400,
            "success": True,
        }
    )
    db.writer.flush()

    with db.read_conn() as conn:
        row = conn.execute(
            """
            SELECT selected_provider_model_id, selected_tokenizer_family,
                   estimated_prompt_tokens, selected_context_window, prompt_tokens
            FROM request_log
            WHERE request_id='client-1'
            """
        ).fetchone()

    db.writer.stop()

    assert logged is True
    assert row is not None
    assert row["selected_provider_model_id"] == "model-a"
    assert row["selected_tokenizer_family"] == "cl100k_base"
    assert row["estimated_prompt_tokens"] == 321
    assert row["selected_context_window"] == 16384
    assert row["prompt_tokens"] == 400


def test_normalize_model_name_drops_common_noise_tokens():
    assert normalize_model_name("Meta-Llama/Llama-3.3-70B-Instruct:Free") == "meta llama llama 3 3 70b"


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


def test_discovery_marks_models_not_seen_inactive(tmp_path):
    import asyncio

    class _MutableProvider:
        name = "fake"

        def __init__(self) -> None:
            self._models = [
                {
                    "id": "fake/model-a",
                    "name": "Model A",
                    "provider_id": "fake",
                    "provider_model_id": "model-a",
                    "provider_base_url": "https://example.com",
                    "provider_api_key_env": "FAKE_API_KEY",
                },
                {
                    "id": "fake/model-b",
                    "name": "Model B",
                    "provider_id": "fake",
                    "provider_model_id": "model-b",
                    "provider_base_url": "https://example.com",
                    "provider_api_key_env": "FAKE_API_KEY",
                },
            ]

        async def discover_models(self):
            return list(self._models)

    class _MutableRegistry:
        def __init__(self, provider) -> None:
            self.provider = provider

        def all(self):
            return [self.provider]

    db = Database(str(tmp_path / "db-discovery-prune.db"))
    db.init()
    db.writer.start()

    provider = _MutableProvider()
    registry = _MutableRegistry(provider)

    asyncio.run(run_discovery(db, registry))
    db.writer.flush()

    provider._models = [
        {
            "id": "fake/model-b",
            "name": "Model B",
            "provider_id": "fake",
            "provider_model_id": "model-b",
            "provider_base_url": "https://example.com",
            "provider_api_key_env": "FAKE_API_KEY",
        }
    ]
    asyncio.run(run_discovery(db, registry))
    db.writer.flush()

    with db.read_conn() as conn:
        rows = conn.execute(
            "SELECT id, is_active FROM models WHERE provider_id='fake' ORDER BY id"
        ).fetchall()

    db.writer.stop()

    assert [(row["id"], row["is_active"]) for row in rows] == [
        ("fake/model-a", 0),
        ("fake/model-b", 1),
    ]


def test_discovery_applies_cached_benchmark_scores(tmp_path):
    import asyncio

    class _CachedProvider:
        name = "fake"

        async def discover_models(self):
            return [
                {
                    "id": "fake/meta-llama/llama-3.3-70b-instruct:free",
                    "name": "Meta Llama 3.3 70B Instruct",
                    "provider_id": "fake",
                    "provider_model_id": "meta-llama/llama-3.3-70b-instruct:free",
                    "provider_base_url": "https://example.com",
                    "provider_api_key_env": "FAKE_API_KEY",
                }
            ]

    class _CachedRegistry:
        def all(self):
            return [_CachedProvider()]

    db = Database(str(tmp_path / "db-discovery-cache.db"))
    db.init()
    db.writer.start()

    db.upsert_leaderboard_cache(
        normalize_model_name("llama-3.3-70b"),
        chatbot_arena_elo=1337.0,
        open_llm_avg_score=88.8,
    )
    db.writer.flush()

    asyncio.run(run_discovery(db, _CachedRegistry()))
    db.writer.flush()

    with db.read_conn() as conn:
        row = conn.execute(
            """
            SELECT chatbot_arena_elo, open_llm_score
            FROM models
            WHERE id='fake/meta-llama/llama-3.3-70b-instruct:free'
            """
        ).fetchone()

    db.writer.stop()

    assert row is not None
    assert row["chatbot_arena_elo"] == 1337.0
    assert row["open_llm_score"] == 88.8
