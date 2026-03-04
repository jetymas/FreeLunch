from __future__ import annotations

import pytest

import src.benchmarks as benchmarks
from src.config import Settings
from src.db import Database
from src.discover import run_discovery


@pytest.mark.asyncio
async def test_refresh_leaderboard_cache_merges_source_results(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "benchmarks.db"))
    db.init()
    db.writer.start()

    async def fake_chatbot_arena_scores(client):
        return {
            benchmarks.normalize_model_name("meta-llama/llama-3.3-70b-instruct"): 1337.0,
        }

    async def fake_open_llm_scores(client, *, page_size=500):
        return {
            benchmarks.normalize_model_name("meta-llama/llama-3.3-70b-instruct"): 88.8,
            benchmarks.normalize_model_name("qwen/qwen2.5-7b-instruct"): 74.2,
        }

    monkeypatch.setattr(benchmarks, "fetch_chatbot_arena_scores", fake_chatbot_arena_scores)
    monkeypatch.setattr(benchmarks, "fetch_open_llm_scores", fake_open_llm_scores)

    outcome = await benchmarks.refresh_leaderboard_cache(db, Settings())
    db.writer.flush()

    with db.read_conn() as conn:
        rows = conn.execute(
            """
            SELECT model_name_normalized, chatbot_arena_elo, open_llm_avg_score
            FROM leaderboard_cache
            ORDER BY model_name_normalized
            """
        ).fetchall()

    db.writer.stop()

    assert outcome == {
        "chatbot_arena_entries": 1,
        "open_llm_entries": 2,
        "cache_updates": 2,
    }
    assert [tuple(row) for row in rows] == [
        ("meta llama llama 3 3 70b", 1337.0, 88.8),
        ("qwen qwen2 5 7b", None, 74.2),
    ]


@pytest.mark.asyncio
async def test_run_discovery_refreshes_benchmark_cache_before_model_upsert(tmp_path, monkeypatch):
    class _Provider:
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

    class _Registry:
        def all(self):
            return [_Provider()]

    db = Database(str(tmp_path / "benchmarks-discovery.db"))
    db.init()
    db.writer.start()

    async def fake_chatbot_arena_scores(client):
        return {benchmarks.normalize_model_name("llama-3.3-70b"): 1337.0}

    async def fake_open_llm_scores(client, *, page_size=500):
        return {benchmarks.normalize_model_name("llama-3.3-70b"): 88.8}

    monkeypatch.setattr(benchmarks, "fetch_chatbot_arena_scores", fake_chatbot_arena_scores)
    monkeypatch.setattr(benchmarks, "fetch_open_llm_scores", fake_open_llm_scores)

    await run_discovery(db, _Registry(), settings=Settings())
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
