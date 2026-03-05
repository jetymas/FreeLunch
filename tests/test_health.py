from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest

from src.config import Settings
from src.db import Database, utc_now_iso
from src.health import (
    ROLLING_METRIC_ALPHA,
    _provider_model_label,
    bootstrap_health_check,
    get_probe_runtime_summary,
    get_provider_probe_usage,
    get_recent_probe_activity,
    get_token_estimation_review_summary,
    mark_failure,
    mark_success,
    run_health_checks,
)
from src.providers.base import ChatResult, ProviderRuntimeState, StreamResult
from src.providers.registry import ProviderRegistry


class _DummyProbeProvider:
    name = "dummy"

    def runtime_state(self) -> ProviderRuntimeState:
        return ProviderRuntimeState(discovery_available=True, inference_available=True)

    def categorize_error(self, status_code, error_code, message):
        return "PROVIDER_UNAVAILABLE", True

    async def discover_models(self):
        return []

    async def chat_completions(self, request_body, model):
        return ChatResult(payload={"id": "dummy", "model": model})

    async def stream_chat_completions(self, request_body, model):
        async def gen():
            yield b"data: [DONE]\n\n"

        return StreamResult(events=gen())

    async def probe(self, model, *, max_tokens=1, timeout_seconds=15):
        return ChatResult(payload={"id": "dummy-probe", "model": model})


class _NoProbeProvider:
    name = "noprobe"

    def runtime_state(self) -> ProviderRuntimeState:
        return ProviderRuntimeState(discovery_available=True, inference_available=True)

    def categorize_error(self, status_code, error_code, message):
        return "PROVIDER_UNAVAILABLE", True

    async def discover_models(self):
        return []

    async def chat_completions(self, request_body, model):
        return ChatResult(payload={"id": "noprobe", "model": model})

    async def stream_chat_completions(self, request_body, model):
        async def gen():
            yield b"data: [DONE]\n\n"

        return StreamResult(events=gen())


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


def test_mark_failure_noops_for_unknown_model(tmp_path):
    db = Database(str(tmp_path / "health-missing-model.db"))
    db.init()
    db.writer.start()

    mark_failure(db, "does-not-exist", "failure")
    db.writer.flush()
    with db.read_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM models").fetchone()
    db.writer.stop()

    assert count is not None
    assert count[0] == 0


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


def test_mark_success_uses_rolling_latency_and_ttfb(tmp_path):
    db = Database(str(tmp_path / "health-rolling.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "model-a")
    db.writer.enqueue("UPDATE models SET avg_latency_ms=100.0, avg_ttfb_ms=50.0 WHERE id='model-a'")
    db.writer.flush()

    mark_success(db, "model-a", latency_ms=220.0, ttfb_ms=110.0)
    db.writer.flush()

    with db.read_conn() as conn:
        row = conn.execute(
            "SELECT avg_latency_ms, avg_ttfb_ms FROM models WHERE id='model-a'"
        ).fetchone()

    db.writer.stop()

    assert row is not None
    assert row[0] == pytest.approx(
        (100.0 * (1.0 - ROLLING_METRIC_ALPHA)) + (220.0 * ROLLING_METRIC_ALPHA)
    )
    assert row[1] == pytest.approx(
        (50.0 * (1.0 - ROLLING_METRIC_ALPHA)) + (110.0 * ROLLING_METRIC_ALPHA)
    )


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


@pytest.mark.asyncio
async def test_run_health_checks_respects_provider_active_probe_gate_for_non_openrouter(tmp_path):
    db = Database(str(tmp_path / "health-probe-gating.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "dummy-model", provider_id="dummy")
    db.writer.flush()

    registry = ProviderRegistry()
    registry.register(_DummyProbeProvider())
    settings = Settings(
        health_max_probes_per_run=1,
        health_daily_request_budget_by_provider={"dummy": 5},
        provider_active_probe_enabled={"dummy": False},
    )

    outcome = await run_health_checks(db, registry, settings)
    db.writer.flush()
    db.writer.stop()

    assert outcome["probed"] == 0
    assert outcome["skipped"] == 1


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


def test_provider_model_label_fallbacks_to_selected_model_or_unknown():
    assert _provider_model_label("provider/model", "selected-model") == "provider/model"
    assert _provider_model_label("", "selected-model") == "selected-model"
    assert _provider_model_label("", "   ") == "unknown"


def test_get_recent_probe_activity_summarizes_probe_and_bootstrap_logs(tmp_path):
    db = Database(str(tmp_path / "health-activity.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "model-a")
    _insert_model(db, "model-b")
    db.writer.enqueue(
        """
        INSERT INTO request_log(
            timestamp, request_source, selected_model_id, provider_id, success
        ) VALUES
            (?, 'probe', 'model-a', 'openrouter', 1),
            (?, 'bootstrap', 'model-a', 'openrouter', 0),
            (?, 'client', 'model-b', 'openrouter', 1)
        """,
        (utc_now_iso(), utc_now_iso(), utc_now_iso()),
    )
    db.writer.flush()

    summary = get_recent_probe_activity(db)
    db.writer.stop()

    assert summary["total_requests"] == 2
    assert summary["successes"] == 1
    assert summary["failures"] == 1
    assert {item["request_source"] for item in summary["by_source"]} == {"bootstrap", "probe"}
    assert summary["by_provider"][0]["provider_id"] == "openrouter"
    assert summary["by_provider"][0]["total_requests"] == 2


def test_get_probe_runtime_summary_reports_bucket_counts_and_candidate_preview(tmp_path):
    db = Database(str(tmp_path / "health-runtime.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "cooldown-recovery")
    _insert_model(db, "never-probed")
    _insert_model(db, "active-cooldown")
    db.writer.enqueue(
        """
        UPDATE models
        SET cooldown_until='2025-01-01T00:00:00Z', last_probe_at='2025-01-01T00:00:00Z'
        WHERE id='cooldown-recovery'
        """
    )
    db.writer.enqueue(
        """
        UPDATE models
        SET cooldown_until='2099-01-01T00:00:00Z', last_probe_at='2025-01-01T00:00:00Z'
        WHERE id='active-cooldown'
        """
    )
    db.writer.flush()

    summary = get_probe_runtime_summary(
        db,
        Settings(
            health_max_probes_per_run=2,
            health_stale_after_minutes=1,
            health_daily_request_budget_by_provider={"openrouter": 5},
        ),
    )
    db.writer.stop()

    assert summary["policy"]["max_probes_per_run"] == 2
    assert summary["buckets"]["cooldown_recovery"] == 1
    assert summary["buckets"]["never_probed"] == 1
    assert summary["buckets"]["active_cooldowns"] == 1
    assert len(summary["next_candidates"]) == 2
    assert summary["next_candidates"][0]["model_id"] == "cooldown-recovery"
    assert summary["next_candidates"][0]["reason"] == "cooldown_recovery"
    assert summary["next_candidates"][1]["model_id"] == "never-probed"
    assert summary["next_candidates"][1]["reason"] == "never_probed"


def test_get_token_estimation_review_summary_flags_context_failure_rates(tmp_path):
    db = Database(str(tmp_path / "health-token-context.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "model-cl100k")
    _insert_model(db, "model-llama")
    db.writer.enqueue("UPDATE models SET tokenizer_family='cl100k_base' WHERE id='model-cl100k'")
    db.writer.enqueue("UPDATE models SET tokenizer_family='llama3' WHERE id='model-llama'")

    now = utc_now_iso()
    db.writer.enqueue(
        """
        INSERT INTO request_log(
            request_id, timestamp, request_source, selected_model_id, provider_id, success, gateway_error_category
        ) VALUES
            ('ctx-1', ?, 'client', 'model-llama', 'openrouter', 0, 'CONTEXT_EXCEEDED'),
            ('ctx-2', ?, 'client', 'model-llama', 'openrouter', 0, 'CONTEXT_EXCEEDED'),
            ('ctx-3', ?, 'client', 'model-llama', 'openrouter', 0, 'CONTEXT_EXCEEDED'),
            ('ctx-4', ?, 'client', 'model-llama', 'openrouter', 0, 'CONTEXT_EXCEEDED'),
            ('ctx-5', ?, 'client', 'model-llama', 'openrouter', 0, 'CONTEXT_EXCEEDED'),
            ('ok-1', ?, 'client', 'model-llama', 'openrouter', 1, NULL),
            ('ok-2', ?, 'client', 'model-cl100k', 'openrouter', 1, NULL),
            ('ok-3', ?, 'client', 'model-cl100k', 'openrouter', 1, NULL)
        """,
        (now, now, now, now, now, now, now, now),
    )
    db.writer.flush()

    summary = get_token_estimation_review_summary(db)
    db.writer.stop()

    llama_row = next(
        row
        for row in summary["context_exceeded_by_tokenizer_family"]
        if row["tokenizer_family"] == "llama3"
    )
    assert llama_row["context_exceeded_failures"] == 5
    assert llama_row["flagged_for_review"] is True
    assert {item["tokenizer_family"] for item in summary["review_flags"]["tokenizer_families"]} == {
        "llama3"
    }


def test_get_token_estimation_review_summary_handles_legacy_request_log_schema(tmp_path):
    db_path = tmp_path / "health-token-legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE models (
            id TEXT PRIMARY KEY,
            provider_model_id TEXT,
            tokenizer_family TEXT,
            context_window INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE request_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT,
            timestamp TEXT NOT NULL,
            request_source TEXT NOT NULL,
            selected_model_id TEXT,
            attempt_index INTEGER,
            success INTEGER,
            gateway_error_category TEXT,
            prompt_tokens INTEGER
        )
        """
    )
    conn.commit()
    conn.close()

    db = Database(str(db_path))
    summary = get_token_estimation_review_summary(db)

    mismatch = summary["estimation_mismatch_by_tokenizer_family"]
    assert mismatch["available"] is False
    assert mismatch["reason"] == "estimated_prompt_tokens column unavailable"
    assert summary["context_failover_recoveries"]["total_requests"] == 0


def test_get_token_estimation_review_summary_flags_failover_recoveries(tmp_path):
    db = Database(str(tmp_path / "health-token-failover.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "model-small")
    _insert_model(db, "model-large")
    db.writer.enqueue(
        "UPDATE models SET tokenizer_family='qwen2', context_window=200 WHERE id='model-small'"
    )
    db.writer.enqueue(
        "UPDATE models SET tokenizer_family='qwen2', context_window=16000 WHERE id='model-large'"
    )

    now = utc_now_iso()
    db.writer.enqueue(
        """
        INSERT INTO request_log(
            request_id, timestamp, request_source, selected_model_id, provider_id,
            attempt_index, success, gateway_error_category, selected_context_window
        ) VALUES
            ('req-1', ?, 'client', 'model-small', 'openrouter', 0, 0, 'CONTEXT_EXCEEDED', 200),
            ('req-1', ?, 'client', 'model-large', 'openrouter', 1, 1, NULL, 16000),
            ('req-2', ?, 'client', 'model-small', 'openrouter', 0, 0, 'CONTEXT_EXCEEDED', 200),
            ('req-2', ?, 'client', 'model-large', 'openrouter', 1, 1, NULL, 16000),
            ('req-3', ?, 'client', 'model-small', 'openrouter', 0, 0, 'CONTEXT_EXCEEDED', 200),
            ('req-3', ?, 'client', 'model-large', 'openrouter', 1, 1, NULL, 16000)
        """,
        (now, now, now, now, now, now),
    )
    db.writer.enqueue("UPDATE models SET context_window=32000 WHERE id='model-small'")
    db.writer.enqueue("UPDATE models SET context_window=256 WHERE id='model-large'")
    db.writer.flush()

    summary = get_token_estimation_review_summary(db)
    db.writer.stop()

    family_row = next(
        row
        for row in summary["context_failover_recoveries"]["by_tokenizer_family"]
        if row["tokenizer_family"] == "qwen2"
    )
    assert family_row["recovered_requests"] == 3
    assert family_row["flagged_for_review"] is True
    assert summary["context_failover_recoveries"]["total_requests"] == 3


def test_get_token_estimation_review_summary_flags_prompt_token_mismatch_ratio(tmp_path):
    db = Database(str(tmp_path / "health-token-mismatch-unavailable.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "model-qwen")
    db.writer.enqueue("UPDATE models SET tokenizer_family='qwen2' WHERE id='model-qwen'")
    now = utc_now_iso()
    mismatch_rows = []
    for index in range(20):
        mismatch_rows.append(
            (
                f"mismatch-{index}",
                now,
                "client",
                "model-qwen",
                "openrouter",
                "qwen-model",
                "qwen2",
                80,
                120,
            )
        )
    for row in mismatch_rows:
        db.writer.enqueue(
            """
            INSERT INTO request_log(
                request_id, timestamp, request_source, selected_model_id, provider_id,
                selected_provider_model_id, selected_tokenizer_family,
                estimated_prompt_tokens, prompt_tokens, success
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            row,
        )
    db.writer.flush()

    summary = get_token_estimation_review_summary(db)
    db.writer.stop()

    mismatch = summary["estimation_mismatch_by_tokenizer_family"]
    assert mismatch["available"] is True
    qwen_row = next(row for row in mismatch["entries"] if row["tokenizer_family"] == "qwen2")
    assert qwen_row["sample_count"] == 20
    assert qwen_row["median_ratio"] == pytest.approx(1.5)
    assert qwen_row["flagged_for_review"] is True


def test_get_token_estimation_review_summary_skips_nonpositive_estimates_from_result_rows():
    class _Cursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class _Connection:
        def execute(self, query, params=()):
            del params
            normalized_query = " ".join(query.split())
            if normalized_query == "PRAGMA table_info(request_log)":
                return _Cursor(
                    [
                        {"name": "selected_tokenizer_family"},
                        {"name": "selected_provider_model_id"},
                        {"name": "selected_context_window"},
                        {"name": "estimated_prompt_tokens"},
                    ]
                )
            if "ORDER BY context_exceeded_failures DESC" in normalized_query:
                return _Cursor([])
            if "ORDER BY rl.request_id, rl.attempt_index ASC, rl.id ASC" in normalized_query:
                return _Cursor([])
            if "AND rl.prompt_tokens IS NOT NULL" in normalized_query:
                return _Cursor(
                    [
                        {
                            "tokenizer_family": "qwen2",
                            "prompt_tokens": 10,
                            "estimated_prompt_tokens": 0,
                        }
                    ]
                )
            return _Cursor([])

    class _FakeDb:
        @contextmanager
        def read_conn(self):
            yield _Connection()

    summary = get_token_estimation_review_summary(_FakeDb())
    mismatch = summary["estimation_mismatch_by_tokenizer_family"]
    assert mismatch["available"] is True
    assert mismatch["entries"] == []


@pytest.mark.asyncio
async def test_run_health_checks_returns_empty_counts_when_no_candidates(tmp_path):
    db = Database(str(tmp_path / "health-empty.db"))
    db.init()
    db.writer.start()

    outcome = await run_health_checks(
        db,
        ProviderRegistry(),
        Settings(
            health_max_probes_per_run=1, health_daily_request_budget_by_provider={"openrouter": 5}
        ),
    )
    db.writer.stop()

    assert outcome == {"considered": 0, "probed": 0, "recovered": 0, "failed": 0, "skipped": 0}


@pytest.mark.asyncio
async def test_run_health_checks_deactivates_missing_provider_candidates(tmp_path):
    db = Database(str(tmp_path / "health-missing-provider.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "ghost-model", provider_id="ghost")
    db.writer.flush()

    outcome = await run_health_checks(
        db,
        ProviderRegistry(),
        Settings(
            health_max_probes_per_run=1,
            health_daily_request_budget_by_provider={"ghost": 5},
        ),
    )
    db.writer.flush()
    with db.read_conn() as conn:
        row = conn.execute("SELECT is_active FROM models WHERE id='ghost-model'").fetchone()
    db.writer.stop()

    assert outcome["skipped"] == 1
    assert row is not None
    assert row["is_active"] == 0


@pytest.mark.asyncio
async def test_run_health_checks_skips_provider_without_probe_method(tmp_path):
    db = Database(str(tmp_path / "health-no-probe-method.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "noprobe-model", provider_id="noprobe")
    db.writer.flush()

    registry = ProviderRegistry()
    registry.register(_NoProbeProvider())
    outcome = await run_health_checks(
        db,
        registry,
        Settings(
            health_max_probes_per_run=1,
            health_daily_request_budget_by_provider={"noprobe": 5},
        ),
    )
    db.writer.stop()

    assert outcome["skipped"] == 1
    assert outcome["probed"] == 0


@pytest.mark.asyncio
async def test_run_health_checks_records_probe_exceptions_as_failures(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "health-probe-failure.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "model-a")
    db.writer.flush()

    registry = ProviderRegistry()
    registry.register_openrouter(api_key="")

    async def failing_probe(self, model, *, max_tokens=1, timeout_seconds=15):
        raise RuntimeError("probe crashed")

    monkeypatch.setattr(type(registry.get("openrouter")), "probe", failing_probe)
    outcome = await run_health_checks(
        db,
        registry,
        Settings(
            health_max_probes_per_run=1, health_daily_request_budget_by_provider={"openrouter": 5}
        ),
    )
    db.writer.flush()
    with db.read_conn() as conn:
        log_row = conn.execute(
            """
            SELECT gateway_error_category, error_message
            FROM request_log
            WHERE request_source='probe'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        model_row = conn.execute(
            "SELECT consecutive_failures FROM models WHERE id='model-a'"
        ).fetchone()
    db.writer.stop()

    assert outcome["failed"] == 1
    assert log_row is not None
    assert log_row["gateway_error_category"] == "provider_error"
    assert "probe crashed" in str(log_row["error_message"])
    assert model_row is not None
    assert model_row["consecutive_failures"] == 1


@pytest.mark.asyncio
async def test_run_health_checks_deduplicates_candidates_across_probe_buckets(
    tmp_path, monkeypatch
):
    db = Database(str(tmp_path / "health-dedup-buckets.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "model-a")
    db.writer.enqueue(
        """
        UPDATE models
        SET cooldown_until='2025-01-01T00:00:00Z', last_probe_at=NULL, last_success_at=NULL
        WHERE id='model-a'
        """
    )
    db.writer.flush()

    registry = ProviderRegistry()
    registry.register_openrouter(api_key="")

    async def fake_probe(self, model, *, max_tokens=1, timeout_seconds=15):
        return ChatResult(payload={"id": "probe", "model": model}, latency_ms=10.0, ttfb_ms=5.0)

    monkeypatch.setattr(type(registry.get("openrouter")), "probe", fake_probe)
    outcome = await run_health_checks(
        db,
        registry,
        Settings(
            health_max_probes_per_run=5,
            health_top_n_stale_probe=5,
            health_daily_request_budget_by_provider={"openrouter": 5},
        ),
    )
    db.writer.stop()

    assert outcome["considered"] == 1
    assert outcome["probed"] == 1


@pytest.mark.asyncio
async def test_bootstrap_health_check_deduplicates_fallback_and_respects_limit(
    tmp_path, monkeypatch
):
    db = Database(str(tmp_path / "health-bootstrap.db"))
    db.init()
    db.writer.start()
    _insert_model(db, "fallback-model")
    _insert_model(db, "model-b")
    _insert_model(db, "model-c")
    db.writer.enqueue("UPDATE models SET composite_score=100.0 WHERE id='fallback-model'")
    db.writer.enqueue("UPDATE models SET composite_score=90.0 WHERE id='model-b'")
    db.writer.enqueue("UPDATE models SET composite_score=80.0 WHERE id='model-c'")
    db.writer.flush()

    registry = ProviderRegistry()
    registry.register_openrouter(api_key="")

    async def fake_probe(self, model, *, max_tokens=1, timeout_seconds=15):
        return ChatResult(payload={"id": "probe", "model": model}, latency_ms=12.0, ttfb_ms=6.0)

    monkeypatch.setattr(type(registry.get("openrouter")), "probe", fake_probe)
    outcome = await bootstrap_health_check(
        db,
        registry,
        Settings(
            ranking_fallback_model="fallback-model",
            health_startup_probe_limit=2,
            health_daily_request_budget_by_provider={"openrouter": 5},
        ),
    )
    db.writer.flush()
    with db.read_conn() as conn:
        rows = conn.execute(
            """
            SELECT selected_model_id
            FROM request_log
            WHERE request_source='bootstrap'
            ORDER BY id ASC
            """
        ).fetchall()
    db.writer.stop()

    assert outcome["considered"] == 2
    assert outcome["probed"] == 2
    assert [row["selected_model_id"] for row in rows] == ["fallback-model", "model-b"]
