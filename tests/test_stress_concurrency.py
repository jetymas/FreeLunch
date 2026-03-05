from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Callable
from contextlib import suppress

import pytest

import src.tokens as tokens_module
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


def test_schedule_tokenizer_preload_deduplicates_concurrent_threadpool_scheduling(monkeypatch):
    hint = "qwen/qwen2.5-7b-instruct:free"
    tokens_module._clear_hf_tokenizer_cache()

    class _FakeAutoTokenizer:
        @staticmethod
        def from_pretrained(repo_id, use_fast=True, trust_remote_code=False):
            del repo_id, use_fast, trust_remote_code
            raise AssertionError("executor stub should not execute preload worker")

    class _RaceExecutor:
        def __init__(self) -> None:
            self.submit_calls = 0
            self.barrier = threading.Barrier(2)
            self.futures: list[concurrent.futures.Future[object | None]] = []

        def submit(self, fn: Callable[..., object], *args, **kwargs):
            del fn, args, kwargs
            self.submit_calls += 1
            with suppress(threading.BrokenBarrierError):
                self.barrier.wait(timeout=0.1)
            future: concurrent.futures.Future[object | None] = concurrent.futures.Future()
            self.futures.append(future)
            return future

    executor = _RaceExecutor()
    monkeypatch.setattr(tokens_module, "AutoTokenizer", _FakeAutoTokenizer)
    monkeypatch.setattr(tokens_module, "_ensure_hf_tokenizer_executor", lambda: executor)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(tokens_module.schedule_tokenizer_preload, hint) for _ in range(2)]
        results = [future.result(timeout=1) for future in futures]

    tokens_module._clear_hf_tokenizer_cache()

    assert results.count(True) == 1
    assert results.count(False) == 1
    assert executor.submit_calls == 1
