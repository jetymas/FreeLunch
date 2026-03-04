from __future__ import annotations

import pytest

from src.config import Settings
from src.db import Database, utc_now_iso
from src.health import get_provider_probe_usage, mark_failure, mark_success, run_health_checks
from src.providers.base import ChatResult
from src.providers.registry import ProviderRegistry


def _insert_model(db: Database, model_id: str) -> None:
    now = utc_now_iso()
    db.writer.enqueue(
        """
        INSERT INTO models(
            id, name, provider_id, provider_model_id, provider_base_url, provider_api_key_env,
            discovered_at, last_seen_at, is_active, is_healthy
        ) VALUES (?, ?, 'openrouter', ?, 'https://example.com', 'OPENROUTER_API_KEY', ?, ?, 1, 1)
        """,
        (model_id, model_id, model_id, now, now),
    )


def test_mark_failure_applies_cooldown_and_backoff(tmp_path):
    db = Database(str(tmp_path / "health.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "model-a")
    db.writer.flush()

    mark_failure(db, "model-a", "first failure")
    db.writer.flush()

    with db.read_conn() as conn:
        row = conn.execute(
            "SELECT consecutive_failures, backoff_level, cooldown_until FROM models WHERE id='model-a'"
        ).fetchone()

    db.writer.stop()

    assert row is not None
    assert row[0] == 1
    assert row[1] == 1
    assert row[2] is not None


def test_mark_success_clears_backoff_state(tmp_path):
    db = Database(str(tmp_path / "health-success.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "model-a")
    db.writer.flush()

    mark_failure(db, "model-a", "failure")
    db.writer.flush()
    mark_success(db, "model-a")
    db.writer.flush()

    with db.read_conn() as conn:
        row = conn.execute(
            "SELECT consecutive_failures, backoff_level, cooldown_until, is_healthy FROM models WHERE id='model-a'"
        ).fetchone()

    db.writer.stop()

    assert row is not None
    assert row[0] == 0
    assert row[1] == 0
    assert row[2] is None
    assert row[3] == 1


@pytest.mark.asyncio
async def test_run_health_checks_respects_probe_budget(tmp_path):
    db = Database(str(tmp_path / "health-budget.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "model-a")
    db.writer.enqueue(
        """
        INSERT INTO request_log(timestamp, request_source, selected_model_id, provider_id, success)
        VALUES (?, 'probe', 'model-a', 'openrouter', 1)
        """,
        (utc_now_iso(),),
    )
    db.writer.flush()

    registry = ProviderRegistry()
    registry.register_openrouter(api_key="")
    settings = Settings(
        health_max_probes_per_run=1, health_daily_request_budget_by_provider={"openrouter": 1}
    )

    outcome = await run_health_checks(db, registry, settings)
    db.writer.flush()

    db.writer.stop()

    assert outcome["probed"] == 0
    assert outcome["skipped"] == 1


@pytest.mark.asyncio
async def test_run_health_checks_recovers_stale_model(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "health-probe.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "model-a")
    db.writer.enqueue(
        "UPDATE models SET last_success_at=?, last_probe_at=?, is_healthy=0 WHERE id='model-a'",
        ("2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
    )
    db.writer.flush()

    registry = ProviderRegistry()
    registry.register_openrouter(api_key="")

    async def fake_probe(self, model, *, max_tokens=1, timeout_seconds=15):
        return ChatResult(payload={"id": "probe", "model": model}, latency_ms=42.0, ttfb_ms=21.0)

    monkeypatch.setattr(type(registry.get("openrouter")), "probe", fake_probe)
    settings = Settings(
        health_max_probes_per_run=1,
        health_daily_request_budget_by_provider={"openrouter": 5},
        health_stale_after_minutes=1,
    )

    outcome = await run_health_checks(db, registry, settings)
    db.writer.flush()

    with db.read_conn() as conn:
        row = conn.execute(
            "SELECT is_healthy, consecutive_failures, avg_latency_ms, avg_ttfb_ms FROM models WHERE id='model-a'"
        ).fetchone()

    db.writer.stop()

    assert outcome["probed"] == 1
    assert outcome["recovered"] == 1
    assert row is not None
    assert tuple(row) == (1, 0, 42.0, 21.0)


def test_get_provider_probe_usage_counts_bootstrap_and_probe(tmp_path):
    db = Database(str(tmp_path / "health-usage.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "model-a")
    db.writer.enqueue(
        """
        INSERT INTO request_log(timestamp, request_source, selected_model_id, provider_id, success)
        VALUES
            (?, 'probe', 'model-a', 'openrouter', 1),
            (?, 'bootstrap', 'model-a', 'openrouter', 1),
            (?, 'client', 'model-a', 'openrouter', 1)
        """,
        (utc_now_iso(), utc_now_iso(), utc_now_iso()),
    )
    db.writer.flush()

    usage = get_provider_probe_usage(db, "openrouter", utc_now_iso()[:10])
    db.writer.stop()

    assert usage == 2
