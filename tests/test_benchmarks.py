from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

import httpx
import pytest

import src.benchmarks as benchmarks
from src.config import Settings
from src.db import Database
from src.discover import run_discovery

_BENCHMARK_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "benchmarks"


def _fixture_text(name: str) -> str:
    return (_BENCHMARK_FIXTURES_DIR / name).read_text(encoding="utf-8")


def _fixture_json(name: str) -> Any:
    return json.loads(_fixture_text(name))


class _FakeClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses

    async def get(self, url: str, params=None):
        request = httpx.Request("GET", url, params=params)
        payload = self.responses[url]
        if isinstance(payload, list | dict):
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


class _OpenLlmClientDynamicLimit:
    def __init__(self, *, max_length: int = 50, num_rows: int = 120) -> None:
        self.max_length = max_length
        self.num_rows = num_rows

    async def get(self, url: str, params=None):
        request = httpx.Request("GET", url, params=params)
        if url == benchmarks._OPEN_LLM_SIZE_URL:
            return httpx.Response(
                200,
                json={"size": {"dataset": {"num_rows": self.num_rows}}},
                request=request,
            )

        if url != benchmarks._OPEN_LLM_ROWS_URL:
            raise AssertionError(f"unexpected url: {url}")

        length = int(params["length"])
        offset = int(params["offset"])
        if length > self.max_length:
            return httpx.Response(
                422,
                json={"error": f"Parameter 'length' must not be greater than {self.max_length}"},
                request=request,
            )

        rows = []
        for index in range(offset, min(offset + length, self.num_rows)):
            rows.append({"row": {"fullname": f"model-{index}", "Average ⬆️": float(index)}})
        return httpx.Response(
            200,
            json={"rows": rows},
            request=request,
        )


class _OpenLlmClientAverageFallback:
    async def get(self, url: str, params=None):
        request = httpx.Request("GET", url, params=params)
        if url == benchmarks._OPEN_LLM_SIZE_URL:
            return httpx.Response(
                200,
                json={"size": {"dataset": {"num_rows": 1}}},
                request=request,
            )
        if url != benchmarks._OPEN_LLM_ROWS_URL:
            raise AssertionError(f"unexpected url: {url}")
        return httpx.Response(
            200,
            json={"rows": [{"row": {"model": "fallback-model", "Average": 42.5}}]},
            request=request,
        )


class _OpenLlmClientAlwaysUnparseable422:
    async def get(self, url: str, params=None):
        request = httpx.Request("GET", url, params=params)
        if url == benchmarks._OPEN_LLM_SIZE_URL:
            return httpx.Response(
                200,
                json={"size": {"dataset": {"num_rows": 5}}},
                request=request,
            )
        if url != benchmarks._OPEN_LLM_ROWS_URL:
            raise AssertionError(f"unexpected url: {url}")
        return httpx.Response(
            422,
            json={"error": "length too large"},
            request=request,
        )


class _OpenLlmClientZeroRows:
    async def get(self, url: str, params=None):
        request = httpx.Request("GET", url, params=params)
        if url == benchmarks._OPEN_LLM_SIZE_URL:
            return httpx.Response(
                200,
                json={"size": {"dataset": {"num_rows": 0}}},
                request=request,
            )
        raise AssertionError(f"unexpected url: {url}")


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


def test_parse_chatbot_arena_csv_detects_fallback_arena_score_key():
    scores = benchmarks._parse_chatbot_arena_csv(
        _fixture_text("leaderboard_table_arena_key_fallback.csv")
    )

    assert scores == {
        "meta llama llama 3 3 70b": 1311.4,
    }


def test_parse_chatbot_arena_snapshot_handles_recursive_mixed_shapes():
    payload = _fixture_json("snapshot_recursive_mixed_shapes.json")
    payload["older"]["bucket"][0]["mistralai/mixtral-8x7b-instruct"][1] = "ignored"

    scores = benchmarks._parse_chatbot_arena_snapshot((payload, {"noise": "ignored"}))

    assert scores == {
        "meta llama llama 3 3 70b": 1401.2,
        "qwen qwen2 5 7b": 1277.7,
        "mistralai mixtral 8x7b": 1300.5,
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
                "Model,Arena Score\n" "Meta-Llama/Llama-3.3-70B-Instruct,1265.5\n"
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
                "Model,Arena ELO\n" "Meta-Llama/Llama-3.3-70B-Instruct,1265.5\n"
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
async def test_fetch_chatbot_arena_scores_falls_back_to_arena_hard_when_table_has_no_scores():
    client = _FakeClient(
        {
            benchmarks._ARENA_TREE_URL: _fixture_json("arena_tree_source_fallback.json"),
            benchmarks._ARENA_RAW_URL.format(path="leaderboard_table_20250804.csv"): _fixture_text(
                "leaderboard_table_no_scores.csv"
            ),
            benchmarks._ARENA_RAW_URL.format(
                path="arena_hard_auto_leaderboard_20250701.csv"
            ): _fixture_text("arena_hard_auto_scores.csv"),
        }
    )

    scores = await benchmarks.fetch_chatbot_arena_scores(client)

    assert scores == {
        "meta llama llama 3 3 70b": 80.1,
    }


@pytest.mark.asyncio
async def test_fetch_chatbot_arena_scores_returns_empty_for_non_list_tree_payload():
    client = _FakeClient(
        {
            benchmarks._ARENA_TREE_URL: _fixture_json("arena_tree_non_list.json"),
        }
    )

    scores = await benchmarks.fetch_chatbot_arena_scores(client)

    assert scores == {}


@pytest.mark.asyncio
async def test_fetch_open_llm_scores_uses_dataset_server_page_limit_fallback():
    client = _OpenLlmClient()

    scores = await benchmarks.fetch_open_llm_scores(client, page_size=500)

    assert len(scores) == 150
    assert scores["model 0"] == 0.0
    assert scores["model 149"] == 149.0


@pytest.mark.asyncio
async def test_fetch_open_llm_scores_adapts_to_dynamic_lower_dataset_server_limit():
    client = _OpenLlmClientDynamicLimit(max_length=50, num_rows=120)

    scores = await benchmarks.fetch_open_llm_scores(client, page_size=100)

    assert len(scores) == 120
    assert scores["model 0"] == 0.0
    assert scores["model 119"] == 119.0


@pytest.mark.asyncio
async def test_fetch_open_llm_scores_accepts_average_column_fallback():
    client = _OpenLlmClientAverageFallback()

    scores = await benchmarks.fetch_open_llm_scores(client, page_size=100)

    assert scores == {"fallback model": 42.5}


@pytest.mark.asyncio
async def test_fetch_open_llm_scores_raises_when_422_limit_message_is_unparseable():
    client = _OpenLlmClientAlwaysUnparseable422()

    with pytest.raises(httpx.HTTPStatusError):
        await benchmarks.fetch_open_llm_scores(client, page_size=100)


@pytest.mark.asyncio
async def test_fetch_open_llm_scores_skips_malformed_rows_and_stops_on_empty_page(monkeypatch):
    class _SizeClient:
        async def get(self, url: str, params=None):
            request = httpx.Request("GET", url, params=params)
            if url == benchmarks._OPEN_LLM_SIZE_URL:
                return httpx.Response(
                    200,
                    json={"size": {"dataset": {"num_rows": 6}}},
                    request=request,
                )
            raise AssertionError(f"unexpected url: {url}")

    rows_page = _fixture_json("open_llm_rows_malformed_page.json")

    async def fake_rows_page(client, *, offset: int, length: int):
        if offset == 0:
            return list(rows_page)
        return []

    monkeypatch.setattr(benchmarks, "_fetch_open_llm_rows_page", fake_rows_page)

    scores = await benchmarks.fetch_open_llm_scores(_SizeClient(), page_size=10)

    assert scores == {"model good": 91.1}


@pytest.mark.asyncio
async def test_fetch_open_llm_scores_returns_empty_when_dataset_has_no_rows():
    client = _OpenLlmClientZeroRows()

    scores = await benchmarks.fetch_open_llm_scores(client, page_size=10)

    assert scores == {}


@pytest.mark.asyncio
async def test_refresh_leaderboard_cache_partial_source_failure_keeps_successful_source(
    tmp_path, monkeypatch
):
    db = Database(str(tmp_path / "benchmarks-partial-failure.db"))
    db.init()
    db.writer.start()

    async def fake_chatbot_arena_scores(client):
        return {benchmarks.normalize_model_name("meta-llama/llama-3.3-70b-instruct"): 1337.0}

    async def fail_open_llm_scores(client, *, page_size=500):
        raise RuntimeError("simulated open-llm failure")

    monkeypatch.setattr(benchmarks, "fetch_chatbot_arena_scores", fake_chatbot_arena_scores)
    monkeypatch.setattr(benchmarks, "fetch_open_llm_scores", fail_open_llm_scores)

    outcome = await benchmarks.refresh_leaderboard_cache(db, Settings())
    db.writer.flush()

    with db.read_conn() as conn:
        rows = conn.execute(
            """
            SELECT model_name_normalized, chatbot_arena_elo, open_llm_avg_score
            FROM leaderboard_cache
            """
        ).fetchall()

    db.writer.stop()

    assert outcome == {
        "chatbot_arena_entries": 1,
        "open_llm_entries": 0,
        "cache_updates": 1,
    }
    assert [tuple(row) for row in rows] == [("meta llama llama 3 3 70b", 1337.0, None)]


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
