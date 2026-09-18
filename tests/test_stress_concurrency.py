from __future__ import annotations

import asyncio

import pytest

from src.config import Settings
from src.db import Database, utc_now_iso
from src.health import run_health_checks
from src.providers.registry import ProviderRegistry


def _insert_model(
    db: Database,
    model_id: str,
    *,
    provider_id: str = "openrouter",
    provider_model_id: str | None = None,
) -> None:
    now = utc_now_iso()
    db.writer.enqueue(
        """
        INSERT INTO models(
            id, name, provider_id, provider_model_id, provider_base_url, provider_api_key_env,
            discovered_at, last_seen_at, is_active, is_healthy
        ) VALUES (?, ?, ?, ?, 'https://example.com', ?, ?, ?, 1, 1)
        """,
        (
            model_id,
            model_id,
            provider_id,
            provider_model_id or model_id,
            f"{provider_id.upper()}_API_KEY",
            now,
            now,
        ),
    )


@pytest.mark.asyncio
async def test_run_health_checks_enforces_provider_budget_under_concurrent_faults(
    tmp_path, monkeypatch
):
    db = Database(str(tmp_path / "stress-budget-race.db"))
    db.init()
    db.writer.start()
    for index in range(3):
        _insert_model(db, f"model-{index}")
    db.writer.flush()

    registry = ProviderRegistry()
    registry.register_openrouter(api_key="")

    async def failing_probe(self, model, *, max_tokens=1, timeout_seconds=15):
        del self, model, max_tokens, timeout_seconds
        await asyncio.sleep(0.02)
        raise RuntimeError("injected concurrent probe failure")

    monkeypatch.setattr(type(registry.get("openrouter")), "probe", failing_probe)

    settings = Settings(
        health_max_probes_per_run=3,
        health_probe_concurrency=3,
        health_daily_request_budget_by_provider={"openrouter": 1},
    )
    outcome = await run_health_checks(db, registry, settings)
    db.writer.flush()
    with db.read_conn() as conn:
        log_count = conn.execute(
            "SELECT COUNT(*) FROM request_log WHERE request_source='probe'"
        ).fetchone()[0]
    db.writer.stop()

    assert outcome["considered"] == 3
    assert outcome["probed"] == 1
    assert outcome["failed"] == 1
    assert outcome["skipped"] == 2
    assert log_count == 1
