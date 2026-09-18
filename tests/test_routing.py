from __future__ import annotations

from src.db import Database, utc_now_iso
from src.routing import (
    NoHealthyModelsError,
    RoutingPreferences,
    RoutingRequirements,
    pick_candidates,
)


def _insert_model(
    db: Database,
    model_id: str,
    *,
    provider_model_id: str | None = None,
    composite_score: float = 50.0,
    avg_latency_ms: float | None = None,
    avg_ttfb_ms: float | None = None,
    consecutive_failures: int = 0,
    backoff_level: int = 0,
    context_window: int = 4096,
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
                  ?, ?, 1, 1, 1, 0, 1, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            model_id,
            model_id,
            provider_model_id or model_id,
            context_window,
            avg_latency_ms,
            composite_score,
            avg_ttfb_ms,
            consecutive_failures,
            backoff_level,
            now,
            now,
            is_active,
            is_healthy,
        ),
    )


def test_pick_candidates_uses_latency_preference_for_reranking(tmp_path):
    db = Database(str(tmp_path / "routing-latency.db"))
    db.init()
    db.writer.start()
    _insert_model(
        db,
        "openrouter/model-fast",
        provider_model_id="model-fast",
        composite_score=70.0,
        avg_latency_ms=100,
    )
    _insert_model(
        db,
        "openrouter/model-slow",
        provider_model_id="model-slow",
        composite_score=80.0,
        avg_latency_ms=3000,
    )
    db.writer.flush()

    candidates = pick_candidates(
        db,
        RoutingRequirements(requested_model="auto"),
        preferences=RoutingPreferences(preference="latency", max_latency_ms=500),
        limit=2,
    )
    db.writer.stop()

    assert candidates[0]["provider_model_id"] == "model-fast"


def test_pick_candidates_quality_preference_prioritizes_composite_score_branch(tmp_path):
    db = Database(str(tmp_path / "routing-quality.db"))
    db.init()
    db.writer.start()
    _insert_model(
        db,
        "openrouter/model-quality",
        provider_model_id="model-quality",
        composite_score=65.0,
        avg_latency_ms=5000.0,
    )
    _insert_model(
        db,
        "openrouter/model-fast-lower-score",
        provider_model_id="model-fast-lower-score",
        composite_score=60.0,
        avg_latency_ms=10.0,
    )
    db.writer.flush()

    candidates = pick_candidates(
        db,
        RoutingRequirements(requested_model="auto"),
        preferences=RoutingPreferences(preference="quality"),
        limit=2,
    )
    db.writer.stop()

    assert candidates[0]["provider_model_id"] == "model-quality"


def test_pick_candidates_context_preference_prioritizes_context_branch(tmp_path):
    db = Database(str(tmp_path / "routing-context.db"))
    db.init()
    db.writer.start()
    _insert_model(
        db,
        "openrouter/model-large-context",
        provider_model_id="model-large-context",
        composite_score=60.0,
        context_window=8192,
        avg_latency_ms=3000.0,
    )
    _insert_model(
        db,
        "openrouter/model-small-context",
        provider_model_id="model-small-context",
        composite_score=62.0,
        context_window=1024,
        avg_latency_ms=10.0,
    )
    db.writer.flush()

    candidates = pick_candidates(
        db,
        RoutingRequirements(requested_model="auto"),
        preferences=RoutingPreferences(preference="context"),
        limit=2,
    )
    db.writer.stop()

    assert candidates[0]["provider_model_id"] == "model-large-context"


def test_pick_candidates_reliability_preference_penalizes_failures_and_backoff(tmp_path):
    db = Database(str(tmp_path / "routing-reliability.db"))
    db.init()
    db.writer.start()
    _insert_model(
        db,
        "openrouter/model-unreliable",
        provider_model_id="model-unreliable",
        composite_score=90.0,
        avg_latency_ms=2000.0,
        consecutive_failures=5,
        backoff_level=3,
    )
    _insert_model(
        db,
        "openrouter/model-reliable",
        provider_model_id="model-reliable",
        composite_score=60.0,
        avg_latency_ms=50.0,
        consecutive_failures=0,
        backoff_level=0,
    )
    db.writer.flush()

    candidates = pick_candidates(
        db,
        RoutingRequirements(requested_model="auto"),
        preferences=RoutingPreferences(preference="reliability"),
        limit=2,
    )
    db.writer.stop()

    assert candidates[0]["provider_model_id"] == "model-reliable"


def test_pick_candidates_max_latency_penalty_can_flip_quality_order(tmp_path):
    db = Database(str(tmp_path / "routing-max-latency.db"))
    db.init()
    db.writer.start()
    _insert_model(
        db,
        "openrouter/model-slow-high-score",
        provider_model_id="model-slow-high-score",
        composite_score=70.0,
        avg_latency_ms=600.0,
    )
    _insert_model(
        db,
        "openrouter/model-fast-lower-score",
        provider_model_id="model-fast-lower-score",
        composite_score=60.0,
        avg_latency_ms=100.0,
    )
    db.writer.flush()

    without_penalty = pick_candidates(
        db,
        RoutingRequirements(requested_model="auto"),
        preferences=RoutingPreferences(preference="quality"),
        limit=2,
    )
    with_penalty = pick_candidates(
        db,
        RoutingRequirements(requested_model="auto"),
        preferences=RoutingPreferences(preference="quality", max_latency_ms=500),
        limit=2,
    )
    db.writer.stop()

    assert without_penalty[0]["provider_model_id"] == "model-slow-high-score"
    assert with_penalty[0]["provider_model_id"] == "model-fast-lower-score"


def test_pick_candidates_min_context_preference_filters_small_models(tmp_path):
    db = Database(str(tmp_path / "routing-min-context.db"))
    db.init()
    db.writer.start()
    _insert_model(
        db,
        "openrouter/model-small-context",
        provider_model_id="model-small-context",
        composite_score=95.0,
        context_window=2048,
    )
    _insert_model(
        db,
        "openrouter/model-large-context",
        provider_model_id="model-large-context",
        composite_score=60.0,
        context_window=8192,
    )
    db.writer.flush()

    candidates = pick_candidates(
        db,
        RoutingRequirements(requested_model="auto"),
        preferences=RoutingPreferences(preference="balanced", min_context_tokens=4096),
        limit=2,
    )
    db.writer.stop()

    assert [candidate["provider_model_id"] for candidate in candidates] == ["model-large-context"]


def test_pick_candidates_raises_when_no_healthy_models(tmp_path):
    db = Database(str(tmp_path / "routing-empty.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "openrouter/model-a", is_healthy=0)
    db.writer.flush()

    try:
        pick_candidates(db, RoutingRequirements(requested_model="auto"))
    except NoHealthyModelsError:
        raised = True
    else:
        raised = False

    db.writer.stop()
    assert raised is True
