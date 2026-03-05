from __future__ import annotations

import random
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from src.db import Database, utc_now_iso
from src.routing import RoutingRequirements, pick_candidates

SEEDS = (7, 11, 19, 23, 31, 43, 47, 59)


@contextmanager
def _routing_db(tmp_path: Path, name: str) -> Iterator[Database]:
    db = Database(str(tmp_path / name))
    db.init()
    db.writer.start()
    try:
        yield db
    finally:
        db.writer.stop()


def _insert_model(
    db: Database,
    model_id: str,
    *,
    provider_model_id: str | None = None,
    composite_score: float = 50.0,
    context_window: int = 4096,
    avg_latency_ms: float | None = None,
    avg_ttfb_ms: float | None = None,
    is_healthy: int = 1,
    is_active: int = 1,
) -> None:
    now = utc_now_iso()
    db.writer.enqueue(
        """
        INSERT INTO models(
            id, name, provider_id, provider_model_id, provider_base_url, provider_api_key_env,
            context_window, avg_latency_ms, supports_tools, supports_streaming, supports_vision,
            supports_structured_output, supports_system_messages, composite_score, avg_ttfb_ms,
            consecutive_failures, backoff_level, discovered_at, last_seen_at, is_active, is_healthy
        ) VALUES (?, ?, 'openrouter', ?, 'https://example.com', 'OPENROUTER_API_KEY',
                  ?, ?, 1, 1, 1, 0, 1, ?, ?, 0, 0, ?, ?, ?, ?)
        """,
        (
            model_id,
            model_id,
            provider_model_id or model_id,
            context_window,
            avg_latency_ms,
            composite_score,
            avg_ttfb_ms,
            now,
            now,
            is_active,
            is_healthy,
        ),
    )


def _candidate_ids(candidates: list[dict[str, str]]) -> list[str]:
    return [candidate["id"] for candidate in candidates]


def _assert_bounded_and_unique(candidates: list[dict[str, str]], *, limit: int) -> None:
    ids = _candidate_ids(candidates)
    assert len(candidates) <= limit
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("seed", SEEDS)
def test_property_routing_fallback_insertion_bounded_and_unique(tmp_path: Path, seed: int) -> None:
    rng = random.Random(seed)
    pool_size = rng.randint(8, 16)
    limit = rng.randint(1, min(6, pool_size - 1))
    fallback_id = f"openrouter/fallback-{seed}"

    with _routing_db(tmp_path, f"property-routing-fallback-{seed}.db") as db:
        for index in range(pool_size):
            model_id = f"openrouter/model-{seed}-{index}"
            provider_model_id = f"model-{seed}-{index}"
            if rng.random() < 0.35:
                provider_model_id = f"{provider_model_id}:free"
            _insert_model(
                db,
                model_id,
                provider_model_id=provider_model_id,
                composite_score=float(rng.randint(25, 250)),
                avg_latency_ms=float(rng.randint(20, 2000)),
                avg_ttfb_ms=float(rng.randint(10, 1500)),
                context_window=rng.randint(2048, 32768),
            )

        _insert_model(
            db,
            fallback_id,
            provider_model_id=f"fallback-{seed}:free",
            composite_score=-1000.0,
            avg_latency_ms=2000.0,
            avg_ttfb_ms=1800.0,
            context_window=4096,
        )
        db.writer.flush()

        baseline = pick_candidates(db, RoutingRequirements(requested_model="auto"), limit=limit)
        with_fallback = pick_candidates(
            db,
            RoutingRequirements(requested_model="auto"),
            fallback_model_id=fallback_id,
            limit=limit,
        )

    _assert_bounded_and_unique(baseline, limit=limit)
    _assert_bounded_and_unique(with_fallback, limit=limit)

    baseline_ids = _candidate_ids(baseline)
    with_fallback_ids = _candidate_ids(with_fallback)
    assert fallback_id not in baseline_ids
    assert fallback_id in with_fallback_ids
    assert with_fallback_ids[-1] == fallback_id
    assert with_fallback_ids[:-1] == baseline_ids[: max(limit - 1, 0)]


@pytest.mark.parametrize("seed", SEEDS)
def test_property_routing_explicit_model_precedence(tmp_path: Path, seed: int) -> None:
    rng = random.Random(seed)
    pool_size = rng.randint(7, 15)
    limit = rng.randint(2, 6)
    base_name = f"explicit-{seed}"
    base_model_id = f"openrouter/{base_name}"
    free_model_id = f"openrouter/{base_name}-free"

    with _routing_db(tmp_path, f"property-routing-explicit-{seed}.db") as db:
        _insert_model(
            db,
            base_model_id,
            provider_model_id=base_name,
            composite_score=-500.0,
            avg_latency_ms=1500.0,
            avg_ttfb_ms=1400.0,
        )
        _insert_model(
            db,
            free_model_id,
            provider_model_id=f"{base_name}:free",
            composite_score=-400.0,
            avg_latency_ms=1400.0,
            avg_ttfb_ms=1300.0,
        )

        for index in range(pool_size):
            _insert_model(
                db,
                f"openrouter/random-{seed}-{index}",
                provider_model_id=f"random-{seed}-{index}",
                composite_score=float(rng.randint(25, 300)),
                avg_latency_ms=float(rng.randint(10, 1800)),
                avg_ttfb_ms=float(rng.randint(5, 1200)),
                context_window=rng.randint(2048, 32768),
            )
        db.writer.flush()

        mode = seed % 3
        if mode == 0:
            requested_model = base_model_id
            expected_first = base_model_id
        elif mode == 1:
            requested_model = base_name
            expected_first = base_model_id
        else:
            requested_model = f"{base_name}:free"
            expected_first = free_model_id

        candidates = pick_candidates(
            db,
            RoutingRequirements(requested_model=requested_model),
            limit=limit,
        )

    _assert_bounded_and_unique(candidates, limit=limit)
    assert candidates[0]["id"] == expected_first


@pytest.mark.parametrize("seed", SEEDS)
def test_property_routing_free_only_pool_compatibility(tmp_path: Path, seed: int) -> None:
    rng = random.Random(seed)
    pool_size = rng.randint(6, 12)
    limit = rng.randint(1, min(5, pool_size))
    fallback_id = f"openrouter/free-fallback-{seed}"
    explicit_free_provider_model = f"free-explicit-{seed}:free"

    with _routing_db(tmp_path, f"property-routing-free-only-{seed}.db") as db:
        for index in range(pool_size):
            base_name = f"family-{seed}-{index}"
            _insert_model(
                db,
                f"openrouter/{base_name}-free",
                provider_model_id=f"{base_name}:free",
                composite_score=float(rng.randint(20, 250)),
                avg_latency_ms=float(rng.randint(10, 1600)),
                avg_ttfb_ms=float(rng.randint(5, 1200)),
                is_active=1,
            )
            _insert_model(
                db,
                f"openrouter/{base_name}",
                provider_model_id=base_name,
                composite_score=999.0,
                avg_latency_ms=1.0,
                avg_ttfb_ms=1.0,
                is_active=0,
            )

        _insert_model(
            db,
            f"openrouter/free-explicit-{seed}",
            provider_model_id=explicit_free_provider_model,
            composite_score=35.0,
            avg_latency_ms=400.0,
            avg_ttfb_ms=300.0,
            is_active=1,
        )
        _insert_model(
            db,
            fallback_id,
            provider_model_id=f"free-fallback-{seed}:free",
            composite_score=-900.0,
            avg_latency_ms=2000.0,
            avg_ttfb_ms=1800.0,
            is_active=1,
        )
        db.writer.flush()

        baseline = pick_candidates(db, RoutingRequirements(requested_model="auto"), limit=limit)
        with_fallback = pick_candidates(
            db,
            RoutingRequirements(requested_model="auto"),
            fallback_model_id=fallback_id,
            limit=limit,
        )
        explicit = pick_candidates(
            db,
            RoutingRequirements(requested_model=explicit_free_provider_model),
            limit=limit,
        )

    _assert_bounded_and_unique(baseline, limit=limit)
    _assert_bounded_and_unique(with_fallback, limit=limit)
    _assert_bounded_and_unique(explicit, limit=limit)

    assert all(candidate["provider_model_id"].endswith(":free") for candidate in baseline)
    assert all(candidate["provider_model_id"].endswith(":free") for candidate in with_fallback)
    assert all(candidate["provider_model_id"].endswith(":free") for candidate in explicit)

    baseline_ids = _candidate_ids(baseline)
    with_fallback_ids = _candidate_ids(with_fallback)
    assert fallback_id not in baseline_ids
    assert fallback_id in with_fallback_ids
    assert with_fallback_ids[-1] == fallback_id
    assert explicit[0]["provider_model_id"] == explicit_free_provider_model
