from __future__ import annotations

import pickle

import httpx
import pytest

import src.benchmarks as benchmarks
from src.config import Settings
from src.db import Database
from src.discover import run_discovery


class _FakeClient:
    def __init__(self, responses: dict[str, str | bytes | list[dict[str, str]]]) -> None:
        self.responses = responses

    async def get(self, url: str, params=None):
        request = httpx.Request("GET", url, params=params)
        payload = self.responses[url]
        if isinstance(payload, list):
            return httpx.Response(200, json=payload, request=request)
        if isinstance(payload, bytes):
            return httpx.Response(200, content=payload, request=request)
        return httpx.Response(200, text=payload, request=request)


class _OpenLlmClient:
    async def get(self, url: str, params=None):
        request = httpx.Request("GET", url, params=params)
        if url == benchmarks._OPEN_LLM_SIZE_URL:
            return httpx.Response(
                200,
                json={"size": {"dataset": {"num_rows": 150}}},
                request=request,
            )

        if url != benchmarks._OPEN_LLM_ROWS_URL:
            raise AssertionError(f"unexpected url: {url}")

        length = int(params["length"])
        offset = int(params["offset"])
        if length > 100:
            return httpx.Response(
                422,
                json={"error": "length too large"},
                request=request,
            )

        rows = []
        for index in range(offset, min(offset + length, 150)):
            rows.append(
                {
                    "row": {
                        "fullname": f"model-{index}",
                        "Average ⬆️": float(index),
                    }
                }
            )
        return httpx.Response(
            200,
            json={"rows": rows},
            request=request,
        )


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
async def test_refresh_leaderboard_cache_skips_fetch_when_source_cache_is_fresh(
    tmp_path, monkeypatch
):
    db = Database(str(tmp_path / "benchmarks-fresh.db"))
    db.init()
    db.writer.start()

    db.upsert_leaderboard_cache(
        benchmarks.normalize_model_name("meta-llama/llama-3.3-70b-instruct"),
        chatbot_arena_elo=1337.0,
        open_llm_avg_score=88.8,
    )
    db.writer.flush()

    async def fail_chatbot_arena_scores(client):
        raise AssertionError("chatbot arena fetch should have been skipped")

    async def fail_open_llm_scores(client, *, page_size=500):
        raise AssertionError("open llm fetch should have been skipped")

    monkeypatch.setattr(benchmarks, "fetch_chatbot_arena_scores", fail_chatbot_arena_scores)
    monkeypatch.setattr(benchmarks, "fetch_open_llm_scores", fail_open_llm_scores)

    outcome = await benchmarks.refresh_leaderboard_cache(db, Settings())
    db.writer.flush()
    db.writer.stop()

    assert outcome == {
        "chatbot_arena_entries": 1,
        "open_llm_entries": 1,
        "cache_updates": 0,
    }


@pytest.mark.asyncio
async def test_refresh_leaderboard_cache_fetches_only_stale_source(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "benchmarks-stale.db"))
    db.init()
    db.writer.start()

    db.writer.enqueue(
        """
        INSERT INTO leaderboard_cache(
            model_name_normalized, chatbot_arena_elo, open_llm_avg_score, fetched_at
        ) VALUES
            ('arena-model', 1200.0, NULL, '2025-01-01T00:00:00Z'),
            ('open-llm-model', NULL, 77.7, '2099-01-01T00:00:00Z')
        """
    )
    db.writer.flush()

    fetched = {"chatbot_arena": 0, "open_llm": 0}

    async def fake_chatbot_arena_scores(client):
        fetched["chatbot_arena"] += 1
        return {"arena model": 1337.0}

    async def fail_open_llm_scores(client, *, page_size=500):
        fetched["open_llm"] += 1
        raise AssertionError("open llm fetch should have been skipped")

    monkeypatch.setattr(benchmarks, "fetch_chatbot_arena_scores", fake_chatbot_arena_scores)
    monkeypatch.setattr(benchmarks, "fetch_open_llm_scores", fail_open_llm_scores)

    outcome = await benchmarks.refresh_leaderboard_cache(db, Settings())
    db.writer.flush()

    with db.read_conn() as conn:
        row = conn.execute(
            "SELECT chatbot_arena_elo FROM leaderboard_cache WHERE model_name_normalized='arena model'"
        ).fetchone()

    db.writer.stop()

    assert fetched == {"chatbot_arena": 1, "open_llm": 0}
    assert outcome["chatbot_arena_entries"] == 1
    assert outcome["open_llm_entries"] == 1
    assert outcome["cache_updates"] == 1
    assert row is not None
    assert row["chatbot_arena_elo"] == 1337.0


@pytest.mark.asyncio
async def test_fetch_chatbot_arena_scores_prefers_richer_leaderboard_table():
    client = _FakeClient(
        {
            benchmarks._ARENA_TREE_URL: [
                {"path": "arena_hard_auto_leaderboard_v0.1.csv"},
                {"path": "leaderboard_table_20250804.csv"},
            ],
            benchmarks._ARENA_RAW_URL.format(path="leaderboard_table_20250804.csv"): (
                "Model,Arena Score,MT-bench (score)\n"
                "Meta-Llama/Llama-3.3-70B-Instruct,1265.5,8.5\n"
            ),
            benchmarks._ARENA_RAW_URL.format(path="arena_hard_auto_leaderboard_v0.1.csv"): (
                "model,score\nMeta-Llama/Llama-3.3-70B-Instruct,80.1\n"
            ),
        }
    )

    scores = await benchmarks.fetch_chatbot_arena_scores(client)

    assert scores == {
        "meta llama llama 3 3 70b": 1265.5,
    }


@pytest.mark.asyncio
async def test_fetch_chatbot_arena_scores_prefers_elo_snapshot_when_parseable():
    client = _FakeClient(
        {
            benchmarks._ARENA_TREE_URL: [
                {"path": "elo_results_20250829.pkl"},
                {"path": "leaderboard_table_20250804.csv"},
            ],
            benchmarks._ARENA_RAW_URL.format(path="elo_results_20250829.pkl"): pickle.dumps(
                {
                    "meta-llama/llama-3.3-70b-instruct": {"rating": 1401.2},
                    "qwen/qwen2.5-7b-instruct": 1277.7,
                }
            ),
            benchmarks._ARENA_RAW_URL.format(path="leaderboard_table_20250804.csv"): (
                "Model,Arena Score\n"
                "Meta-Llama/Llama-3.3-70B-Instruct,1265.5\n"
            ),
        }
    )

    scores = await benchmarks.fetch_chatbot_arena_scores(client)

    assert scores == {
        "meta llama llama 3 3 70b": 1401.2,
        "qwen qwen2 5 7b": 1277.7,
    }


@pytest.mark.asyncio
async def test_fetch_chatbot_arena_scores_falls_back_when_snapshot_cannot_be_parsed():
    client = _FakeClient(
        {
            benchmarks._ARENA_TREE_URL: [
                {"path": "elo_results_20250829.pkl"},
                {"path": "leaderboard_table_20250804.csv"},
            ],
            benchmarks._ARENA_RAW_URL.format(path="elo_results_20250829.pkl"): b"not-a-pickle",
            benchmarks._ARENA_RAW_URL.format(path="leaderboard_table_20250804.csv"): (
                "Model,Arena ELO\n"
                "Meta-Llama/Llama-3.3-70B-Instruct,1265.5\n"
            ),
        }
    )

    scores = await benchmarks.fetch_chatbot_arena_scores(client)

    assert scores == {
        "meta llama llama 3 3 70b": 1265.5,
    }


@pytest.mark.asyncio
async def test_fetch_chatbot_arena_scores_uses_older_parseable_snapshot_when_latest_is_bad():
    client = _FakeClient(
        {
            benchmarks._ARENA_TREE_URL: [
                {"path": "elo_results_20250828.pkl"},
                {"path": "elo_results_20250829.pkl"},
            ],
            benchmarks._ARENA_RAW_URL.format(path="elo_results_20250828.pkl"): pickle.dumps(
                {
                    "meta-llama/llama-3.3-70b-instruct": {"rating": 1401.2},
                }
            ),
            benchmarks._ARENA_RAW_URL.format(path="elo_results_20250829.pkl"): b"not-a-pickle",
        }
    )

    scores = await benchmarks.fetch_chatbot_arena_scores(client)

    assert scores == {
        "meta llama llama 3 3 70b": 1401.2,
    }


@pytest.mark.asyncio
async def test_fetch_open_llm_scores_uses_dataset_server_page_limit_fallback():
    client = _OpenLlmClient()

    scores = await benchmarks.fetch_open_llm_scores(client, page_size=500)

    assert len(scores) == 150
    assert scores["model 0"] == 0.0
    assert scores["model 149"] == 149.0


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
