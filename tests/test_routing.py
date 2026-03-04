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
            supports_structured_output, supports_system_messages, composite_score, discovered_at,
            last_seen_at, is_active, is_healthy
        ) VALUES (?, ?, 'openrouter', ?, 'https://example.com', 'OPENROUTER_API_KEY',
                  ?, ?, 1, 1, 1, 0, 1, ?, ?, ?, ?, ?)
        """,
        (
            model_id,
            model_id,
            provider_model_id or model_id,
            context_window,
            avg_latency_ms,
            composite_score,
            now,
            now,
            is_active,
            is_healthy,
        ),
    )


def test_pick_candidates_prefers_explicit_model_if_routable(tmp_path):
    db = Database(str(tmp_path / "routing-explicit.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "openrouter/model-a", provider_model_id="model-a", composite_score=20.0)
    _insert_model(db, "openrouter/model-b", provider_model_id="model-b", composite_score=90.0)
    db.writer.flush()

    candidates = pick_candidates(
        db,
        RoutingRequirements(requested_model="model-a"),
        limit=2,
    )
    db.writer.stop()

    assert candidates[0]["provider_model_id"] == "model-a"


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


def test_pick_candidates_includes_fallback_model_even_if_low_ranked(tmp_path):
    db = Database(str(tmp_path / "routing-fallback.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "openrouter/model-top-1", composite_score=90.0)
    _insert_model(db, "openrouter/model-top-2", composite_score=80.0)
    _insert_model(db, "openrouter/openrouter/free", composite_score=1.0)
    db.writer.flush()

    candidates = pick_candidates(
        db,
        RoutingRequirements(requested_model="auto"),
        fallback_model_id="openrouter/openrouter/free",
        limit=2,
    )
    db.writer.stop()

    assert any(candidate["id"] == "openrouter/openrouter/free" for candidate in candidates)


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
