from __future__ import annotations

import asyncio
import concurrent.futures
import threading

import pytest

from src import tokens as tokens_module
from src.config import Settings
from src.db import Database, utc_now_iso
from src.health import run_health_checks
from src.providers.registry import ProviderRegistry


def test_concurrent_tokenizer_preload_schedules_one_load(monkeypatch) -> None:
    """Concurrent discovery calls should share one pending tokenizer load."""

    class CountingExecutor:
        def __init__(self) -> None:
            self.submissions = 0
            self.future: concurrent.futures.Future[object | None] = concurrent.futures.Future()

        def submit(self, *args: object) -> concurrent.futures.Future[object | None]:
            del args
            self.submissions += 1
            return self.future

    hint = "qwen/qwen2.5-7b-instruct:free"
    executor = CountingExecutor()
    barrier = threading.Barrier(16)
    tokens_module._clear_hf_tokenizer_cache()
    monkeypatch.setattr(tokens_module, "AutoTokenizer", object())
    monkeypatch.setattr(tokens_module, "_ensure_hf_tokenizer_executor", lambda: executor)

    def schedule() -> bool:
        barrier.wait(timeout=5)
        return tokens_module.schedule_tokenizer_preload(hint)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(lambda _: schedule(), range(16)))

        assert results.count(True) == 1
        assert results.count(False) == 15
        assert executor.submissions == 1
    finally:
        executor.future.cancel()
        tokens_module._clear_hf_tokenizer_cache()


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
