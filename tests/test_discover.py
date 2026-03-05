from __future__ import annotations

import asyncio

import pytest

import src.discover as discover
from src.benchmarks import normalize_model_name
from src.db import Database


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, None),
        (False, None),
        (0, None),
        (-4, None),
        ("0", None),
        ("-9", None),
        (7, 7),
        (3.8, 3),
        ("11", 11),
        (" 13 ", 13),
        ("3.14", None),
        ("abc", None),
        ({}, None),
        ([], None),
    ],
)
def test_coerce_rank_edge_matrix(value: object, expected: int | None) -> None:
    assert discover._coerce_rank(value) == expected


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ({"provider_id": "openrouter", "provider_rank": 5}, (5, 5)),
        ({"provider_id": "openrouter", "openrouter_rank": "7"}, (7, 7)),
        ({"provider_id": "fake", "provider_rank": "3"}, (3, None)),
        ({"provider_id": "fake", "openrouter_rank": "11"}, (11, 11)),
        ({"provider_id": "openrouter", "provider_rank": 8, "openrouter_rank": 2}, (8, 2)),
        ({"provider_id": "fake", "provider_rank": "oops", "openrouter_rank": "bad"}, (None, None)),
    ],
)
def test_resolve_rank_metadata_legacy_backfill(
    model: dict, expected: tuple[int | None, int | None]
) -> None:
    assert discover._resolve_rank_metadata(model) == expected


def test_apply_cached_benchmarks_fills_only_missing_scores(tmp_path) -> None:
    db = Database(str(tmp_path / "discover-cache-fill.db"))
    db.init()
    db.writer.start()

    try:
        db.upsert_leaderboard_cache(
            normalize_model_name("acme/model-a"),
            chatbot_arena_elo=1300.0,
            open_llm_avg_score=77.7,
        )
        db.writer.flush()

        enriched = discover._apply_cached_benchmarks(
            db,
            {
                "id": "acme/model-a",
                "name": "Model A",
                "provider_model_id": "acme/model-a",
                "chatbot_arena_elo": 1500.0,
                "open_llm_score": None,
            },
        )
    finally:
        db.writer.stop()

    assert enriched["chatbot_arena_elo"] == 1500.0
    assert enriched["open_llm_score"] == 77.7


def test_apply_cached_benchmarks_merges_partial_cache_hits_across_lookup_keys(tmp_path) -> None:
    db = Database(str(tmp_path / "discover-cache-partial.db"))
    db.init()
    db.writer.start()

    try:
        db.upsert_leaderboard_cache(
            normalize_model_name("acme/model-b"),
            chatbot_arena_elo=1401.0,
            open_llm_avg_score=None,
        )
        db.upsert_leaderboard_cache(
            normalize_model_name("model-b"),
            chatbot_arena_elo=None,
            open_llm_avg_score=82.2,
        )
        db.writer.flush()

        enriched = discover._apply_cached_benchmarks(
            db,
            {
                "id": "fake/model-b",
                "name": "Model B",
                "provider_model_id": "acme/model-b",
            },
        )
    finally:
        db.writer.stop()

    assert enriched["chatbot_arena_elo"] == 1401.0
    assert enriched["open_llm_score"] == 82.2


def _discovered_model(provider_id: str, model_slug: str) -> dict[str, str]:
    return {
        "id": f"{provider_id}/{model_slug}",
        "name": model_slug,
        "provider_id": provider_id,
        "provider_model_id": model_slug,
        "provider_base_url": "https://example.com",
        "provider_api_key_env": f"{provider_id.upper()}_API_KEY",
    }


class _MutableProvider:
    def __init__(self, name: str, models: list[dict[str, str]]) -> None:
        self.name = name
        self.models = models

    async def discover_models(self) -> list[dict[str, str]]:
        return list(self.models)


class _Registry:
    def __init__(self, providers: list[_MutableProvider]) -> None:
        self._providers = providers

    def all(self) -> list[_MutableProvider]:
        return self._providers


def test_discovery_reconciliation_provider_scope_empty_pass_and_rediscovery(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(discover, "schedule_tokenizer_preload", lambda _model_hint: False)

    db = Database(str(tmp_path / "discover-reconcile.db"))
    db.init()
    db.writer.start()

    try:
        alpha = _MutableProvider(
            "alpha",
            [
                _discovered_model("alpha", "a1"),
                _discovered_model("alpha", "a2"),
            ],
        )
        beta = _MutableProvider("beta", [_discovered_model("beta", "b1")])
        registry = _Registry([alpha, beta])

        assert asyncio.run(discover.run_discovery(db, registry)) == 3
        db.writer.flush()

        alpha.models = [_discovered_model("alpha", "a2")]
        assert asyncio.run(discover.run_discovery(db, registry)) == 2
        db.writer.flush()

        with db.read_conn() as conn:
            phase_two = conn.execute("SELECT id, is_active FROM models ORDER BY id").fetchall()

        assert [(row["id"], row["is_active"]) for row in phase_two] == [
            ("alpha/a1", 0),
            ("alpha/a2", 1),
            ("beta/b1", 1),
        ]

        alpha.models = []
        assert asyncio.run(discover.run_discovery(db, registry)) == 1
        db.writer.flush()

        with db.read_conn() as conn:
            phase_three_alpha = conn.execute(
                "SELECT id, is_active FROM models WHERE provider_id='alpha' ORDER BY id"
            ).fetchall()

        assert [(row["id"], row["is_active"]) for row in phase_three_alpha] == [
            ("alpha/a1", 0),
            ("alpha/a2", 0),
        ]

        alpha.models = [_discovered_model("alpha", "a1")]
        assert asyncio.run(discover.run_discovery(db, registry)) == 2
        db.writer.flush()

        with db.read_conn() as conn:
            final_rows = conn.execute("SELECT id, is_active FROM models ORDER BY id").fetchall()
    finally:
        db.writer.stop()

    assert [(row["id"], row["is_active"]) for row in final_rows] == [
        ("alpha/a1", 1),
        ("alpha/a2", 0),
        ("beta/b1", 1),
    ]
