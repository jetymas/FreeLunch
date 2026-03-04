from __future__ import annotations

import importlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from src.db import Database, utc_now_iso
from src.providers.base import ProviderRetryableError, StreamResult
from src.providers.openrouter import OpenRouterAdapter


def _insert_backup_model(
    client,
    model_id: str = "openrouter/test-stream-backup",
    provider_model_id: str = "test-stream-backup",
):
    db = client.app.state.db
    now = "2026-03-04T00:00:00Z"
    db.writer.enqueue(
        """
        INSERT INTO models(
            id, name, provider_id, provider_model_id, provider_base_url, provider_api_key_env,
            context_window, supports_streaming, supports_tools, supports_vision, supports_structured_output,
            supports_system_messages, composite_score, discovered_at, last_seen_at, is_active, is_healthy
        ) VALUES (?, ?, 'openrouter', ?, 'https://example.com', 'OPENROUTER_API_KEY',
                  8192, 1, 1, 1, 0, 1, 50.0, ?, ?, 1, 1)
        """,
        (model_id, model_id, provider_model_id, now, now),
    )
    db.writer.flush()
    client.app.state.recompute_readiness()


def _build_client_with_config(tmp_path, monkeypatch: pytest.MonkeyPatch, config_text: str) -> TestClient:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_text.strip(), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)

    import src.main as main_module

    main_module = importlib.reload(main_module)
    return TestClient(main_module.app)


def test_models_endpoint(client):
    response = client.get("/v1/models")
    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "list"
    assert len(payload["data"]) >= 1


def test_chat_completion_streaming(client):
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "stream": True,
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "data: [DONE]" in response.text


def test_chat_completion_streaming_passes_through_provider_frames(client, monkeypatch):
    async def fake_stream(self, request_body, model):
        async def gen():
            yield b": keepalive\n\n"
            yield b'data: {"id":"chatcmpl-test","object":"chat.completion.chunk","model":"test","choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
            yield b"data: [DONE]\n\n"

        return StreamResult(events=gen())

    monkeypatch.setattr(OpenRouterAdapter, "stream_chat_completions", fake_stream)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "stream": True,
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )
    assert response.status_code == 200
    assert ": keepalive" not in response.text
    assert '"content":"hi"' in response.text
    assert response.text.count("data: [DONE]") == 1


def test_chat_completion_streaming_fails_over_before_first_event(client, monkeypatch):
    _insert_backup_model(client)

    async def fail_then_stream(self, request_body, model):
        if model == "openrouter/free":
            raise ProviderRetryableError("stream setup failed", category="PROVIDER_UNAVAILABLE")

        async def gen():
            yield b'data: {"id":"chatcmpl-test","object":"chat.completion.chunk","model":"test","choices":[{"index":0,"delta":{"content":"backup"},"finish_reason":null}]}\n\n'
            yield b"data: [DONE]\n\n"

        return StreamResult(events=gen())

    monkeypatch.setattr(OpenRouterAdapter, "stream_chat_completions", fail_then_stream)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "stream": True,
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )
    assert response.status_code == 200
    assert '"content":"backup"' in response.text


def test_chat_completion_streaming_logs_midstream_error_without_failover(client, monkeypatch):
    async def fake_stream(self, request_body, model):
        async def gen():
            yield b'data: {"id":"chatcmpl-test","object":"chat.completion.chunk","model":"test","choices":[{"index":0,"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
            yield b'data: {"error":{"message":"rate limit exceeded","code":"429","status_code":429}}\n\n'

        return StreamResult(events=gen())

    monkeypatch.setattr(OpenRouterAdapter, "stream_chat_completions", fake_stream)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "stream": True,
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )
    assert response.status_code == 200
    assert '"content":"partial"' in response.text
    assert "rate limit exceeded" in response.text
    assert "data: [DONE]" not in response.text

    logs = client.get("/admin/logs?limit=2").json()["logs"]
    assert logs[0]["success"] is False
    assert logs[0]["was_streaming"] is True
    assert logs[0]["gateway_error_category"] == "RATE_LIMITED"


def test_admin_endpoints(client):
    models_resp = client.get("/admin/models")
    assert models_resp.status_code == 200
    models_payload = models_resp.json()
    assert models_payload["models"]

    model_id = models_payload["models"][0]["id"]
    detail_resp = client.get(f"/admin/models/{model_id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["model"]["id"] == model_id

    health_response = client.get("/admin/health")
    assert health_response.status_code == 200
    health_payload = health_response.json()
    assert "bootstrap" in health_payload
    assert "db" in health_payload
    assert "runtime_logging" in health_payload
    assert "models" in health_payload
    assert "scheduler" in health_payload
    assert "probe_budgets" in health_payload
    assert "probe_state" in health_payload
    assert "recent_probe_activity" in health_payload
    assert "token_estimation_review" in health_payload
    assert "maintenance" in health_payload["scheduler"]["jobs"]
    assert "config_refresh" in health_payload["scheduler"]["jobs"]

    config_response = client.get("/admin/config")
    assert config_response.status_code == 200
    config_payload = config_response.json()
    assert "overrides" in config_payload
    assert "effective" in config_payload
    assert "logging.runtime_verbosity" in config_payload["effective"]


def test_admin_health_reports_probe_budget_usage(client):
    db = client.app.state.db
    utc_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db.writer.enqueue(
        """
        INSERT INTO request_log(
            timestamp, request_source, selected_model_id, provider_id, success
        ) VALUES
            (?, 'probe', 'openrouter/openrouter/free', 'openrouter', 1),
            (?, 'bootstrap', 'openrouter/openrouter/free', 'openrouter', 1)
        """,
        (f"{utc_day}T00:00:00Z", f"{utc_day}T01:00:00Z"),
    )
    db.writer.flush()

    response = client.get("/admin/health")
    assert response.status_code == 200
    payload = response.json()
    budget = next(item for item in payload["probe_budgets"] if item["provider_id"] == "openrouter")
    assert budget["limit"] == 5
    assert budget["used"] >= 2
    assert budget["remaining"] == budget["limit"] - budget["used"]


def test_admin_health_reports_probe_state_preview_and_recent_activity(client):
    response = client.put(
        "/admin/config/health.max_probes_per_run",
        json={"value": 2},
    )
    assert response.status_code == 200

    db = client.app.state.db
    now = utc_now_iso()
    db.writer.enqueue(
        """
        INSERT INTO models(
            id, name, provider_id, provider_model_id, provider_base_url, provider_api_key_env,
            discovered_at, last_seen_at, is_active, is_healthy
        ) VALUES
            ('openrouter/cooldown-preview', 'openrouter/cooldown-preview', 'openrouter', 'cooldown-preview', 'https://example.com', 'OPENROUTER_API_KEY', ?, ?, 1, 1),
            ('openrouter/never-probed-preview', 'openrouter/never-probed-preview', 'openrouter', 'never-probed-preview', 'https://example.com', 'OPENROUTER_API_KEY', ?, ?, 1, 1)
        """,
        (now, now, now, now),
    )
    db.writer.enqueue(
        """
        UPDATE models
        SET cooldown_until='2025-01-01T00:00:00Z', last_probe_at='2025-01-01T00:00:00Z'
        WHERE id='openrouter/cooldown-preview'
        """
    )
    db.writer.enqueue(
        """
        INSERT INTO request_log(
            timestamp, request_source, selected_model_id, provider_id, success
        ) VALUES
            (?, 'probe', 'openrouter/cooldown-preview', 'openrouter', 1),
            (?, 'bootstrap', 'openrouter/cooldown-preview', 'openrouter', 0)
        """,
        (now, now),
    )
    db.writer.flush()

    health_response = client.get("/admin/health")
    assert health_response.status_code == 200
    payload = health_response.json()

    assert payload["probe_state"]["policy"]["max_probes_per_run"] == 2
    assert payload["probe_state"]["buckets"]["cooldown_recovery"] >= 1
    assert payload["probe_state"]["buckets"]["never_probed"] >= 1
    assert payload["probe_state"]["next_candidates"][0]["reason"] == "cooldown_recovery"
    assert payload["probe_state"]["next_candidates"][0]["model_id"] == "openrouter/cooldown-preview"

    assert payload["recent_probe_activity"]["total_requests"] >= 2
    assert payload["recent_probe_activity"]["failures"] >= 1
    by_source = {item["request_source"] for item in payload["recent_probe_activity"]["by_source"]}
    assert "probe" in by_source
    assert "bootstrap" in by_source


def test_admin_health_reports_token_estimation_review_summary(client):
    db = client.app.state.db
    now = utc_now_iso()
    db.writer.enqueue(
        """
        UPDATE models
        SET tokenizer_family='qwen2', context_window=200
        WHERE id='openrouter/openrouter/free'
        """
    )
    db.writer.enqueue(
        """
        INSERT INTO request_log(
            request_id, timestamp, request_source, selected_model_id, provider_id,
            selected_provider_model_id, selected_tokenizer_family,
            attempt_index, estimated_prompt_tokens, prompt_tokens, success, gateway_error_category
        ) VALUES
            ('token-review-1', ?, 'client', 'openrouter/openrouter/free', 'openrouter', 'openrouter/free', 'qwen2', 0, 80, 120, 0, 'CONTEXT_EXCEEDED'),
            ('token-review-2', ?, 'client', 'openrouter/openrouter/free', 'openrouter', 'openrouter/free', 'qwen2', 0, 80, 120, 0, 'CONTEXT_EXCEEDED'),
            ('token-review-3', ?, 'client', 'openrouter/openrouter/free', 'openrouter', 'openrouter/free', 'qwen2', 0, 80, 120, 0, 'CONTEXT_EXCEEDED'),
            ('token-review-4', ?, 'client', 'openrouter/openrouter/free', 'openrouter', 'openrouter/free', 'qwen2', 0, 80, 120, 0, 'CONTEXT_EXCEEDED'),
            ('token-review-5', ?, 'client', 'openrouter/openrouter/free', 'openrouter', 'openrouter/free', 'qwen2', 0, 80, 120, 0, 'CONTEXT_EXCEEDED'),
            ('token-review-6', ?, 'client', 'openrouter/openrouter/free', 'openrouter', 'openrouter/free', 'qwen2', 0, 80, 120, 1, NULL)
        """,
        (now, now, now, now, now, now),
    )
    db.writer.flush()

    response = client.get("/admin/health")
    assert response.status_code == 200
    payload = response.json()

    review = payload["token_estimation_review"]
    assert review["review_window_days"] == 7
    flagged_families = {
        item["tokenizer_family"] for item in review["review_flags"]["tokenizer_families"]
    }
    assert "qwen2" in flagged_families


def test_admin_enable_disable_model_impacts_readiness(client):
    models = client.get("/admin/models").json()["models"]
    assert models
    model_id = models[0]["id"]

    disable_response = client.post(f"/admin/models/{model_id}/disable")
    assert disable_response.status_code == 200
    assert disable_response.json()["status"] == "disabled"

    ready_after_disable = client.get("/readyz")
    assert ready_after_disable.status_code == 503

    enable_response = client.post(f"/admin/models/{model_id}/enable")
    assert enable_response.status_code == 200
    assert enable_response.json()["status"] == "enabled"

    ready_after_enable = client.get("/readyz")
    assert ready_after_enable.status_code == 200


def test_admin_refresh_triggers_discovery_immediately(client):
    before = client.get("/admin/health")
    assert before.status_code == 200
    before_jobs = before.json()["scheduler"]["jobs"]
    before_run_count = int(before_jobs.get("discovery", {}).get("run_count", 0))

    refresh_response = client.post("/admin/refresh")
    assert refresh_response.status_code == 200
    refresh_payload = refresh_response.json()
    assert refresh_payload["status"] == "completed"
    assert "outcome" in refresh_payload

    after = client.get("/admin/health")
    assert after.status_code == 200
    discovery_job = after.json()["scheduler"]["jobs"]["discovery"]
    assert int(discovery_job["run_count"]) == before_run_count + 1
    assert discovery_job["last_started_at"]
    assert discovery_job["last_success_at"]


def test_admin_logs_returns_recent_entries(client):
    response = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hello logs"}]},
    )
    assert response.status_code == 200

    logs_response = client.get("/admin/logs?limit=5")
    assert logs_response.status_code == 200
    payload = logs_response.json()
    assert payload["count"] >= 1
    assert payload["limit"] == 5
    assert payload["logs"][0]["request_id"]


def test_admin_config_override_updates_runtime_settings(client):
    update_response = client.put(
        "/admin/config/routing.max_attempts",
        json={"value": 5},
    )
    assert update_response.status_code == 200
    assert client.app.state.settings.routing_max_attempts == 5

    config_response = client.get("/admin/config")
    assert config_response.status_code == 200
    config_payload = config_response.json()
    assert any(item["key"] == "routing.max_attempts" for item in config_payload["overrides"])
    assert config_payload["effective"]["routing.max_attempts"] == 5

    delete_response = client.delete("/admin/config/routing.max_attempts")
    assert delete_response.status_code == 200

    after_delete = client.get("/admin/config").json()
    assert not any(item["key"] == "routing.max_attempts" for item in after_delete["overrides"])
    assert after_delete["effective"]["routing.max_attempts"] == 3


def test_periodic_config_refresh_picks_up_db_overrides(client):
    db = client.app.state.db
    db.set_override("routing.max_attempts", 7)
    db.writer.flush()

    client.app.state.config_refresh_runner()

    assert client.app.state.settings.routing_max_attempts == 7


def test_admin_config_override_updates_log_retention_setting(client):
    response = client.put(
        "/admin/config/logging.request_log_retention_days",
        json={"value": 14},
    )
    assert response.status_code == 200
    assert client.app.state.settings.logging_request_log_retention_days == 14

    config_response = client.get("/admin/config")
    assert config_response.status_code == 200
    assert config_response.json()["effective"]["logging.request_log_retention_days"] == 14


def test_admin_config_override_updates_runtime_log_verbosity(client):
    response = client.put(
        "/admin/config/logging.runtime_verbosity",
        json={"value": "debug"},
    )
    assert response.status_code == 200
    assert client.app.state.settings.logging_runtime_verbosity == "debug"

    health_response = client.get("/admin/health")
    assert health_response.status_code == 200
    assert health_response.json()["runtime_logging"]["verbosity"] == "debug"


def test_admin_config_override_reschedules_health_job(client):
    response = client.put(
        "/admin/config/health.probe_interval_minutes",
        json={"value": 10},
    )
    assert response.status_code == 200
    job = client.app.state.scheduler.get_job("health")
    assert job is not None
    assert int(job.trigger.interval.total_seconds()) == 600


def test_admin_config_override_reschedules_discovery_job(client):
    response = client.put(
        "/admin/config/discovery.interval_minutes",
        json={"value": 45},
    )
    assert response.status_code == 200
    job = client.app.state.scheduler.get_job("discovery")
    assert job is not None
    assert int(job.trigger.interval.total_seconds()) == 2700


def test_admin_config_override_reschedules_ranking_job(client):
    response = client.put(
        "/admin/config/ranking.interval_minutes",
        json={"value": 20},
    )
    assert response.status_code == 200
    job = client.app.state.scheduler.get_job("ranking")
    assert job is not None
    assert int(job.trigger.interval.total_seconds()) == 1200


def test_admin_endpoints_require_auth_when_gateway_key_is_set(client):
    client.app.state.settings.gateway_api_key = "secret"

    unauthorized = client.get("/admin/models")
    assert unauthorized.status_code == 401

    authorized = client.get("/admin/models", headers={"Authorization": "Bearer secret"})
    assert authorized.status_code == 200

    ready = client.get("/readyz")
    assert ready.status_code == 200


def test_admin_config_rejects_unknown_override_key(client):
    response = client.put(
        "/admin/config/not.allowed",
        json={"value": "x"},
    )
    assert response.status_code == 400


def test_providers_enabled_can_disable_openrouter_startup(tmp_path, monkeypatch):
    with _build_client_with_config(
        tmp_path,
        monkeypatch,
        """
providers:
  enabled: []
  openrouter:
    enabled: true
""",
    ) as client:
        assert client.app.state.settings.public_settings()["providers.enabled"] == []
        assert client.app.state.settings.openrouter_enabled is False
        assert client.app.state.registry.all() == []
        with pytest.raises(KeyError):
            client.app.state.registry.get("openrouter")

        ready = client.get("/readyz")
        assert ready.status_code == 503


def test_discovery_enabled_false_keeps_provider_out_of_discovery_registry(tmp_path, monkeypatch):
    with _build_client_with_config(
        tmp_path,
        monkeypatch,
        """
providers:
  enabled:
    - openrouter
  openrouter:
    enabled: true
    discovery_enabled: false
    inference_enabled: true
    dev_stub_enabled: true
""",
    ) as client:
        assert client.app.state.settings.openrouter_discovery_enabled is False
        assert client.app.state.registry.all() == []
        provider = client.app.state.registry.get("openrouter")
        assert isinstance(provider, OpenRouterAdapter)

        ready = client.get("/readyz")
        assert ready.status_code == 503


def test_reload_settings_deactivates_models_when_inference_is_disabled(tmp_path, monkeypatch):
    with _build_client_with_config(
        tmp_path,
        monkeypatch,
        """
providers:
  enabled:
    - openrouter
  openrouter:
    enabled: true
    discovery_enabled: true
    inference_enabled: true
    dev_stub_enabled: true
""",
    ) as client:
        models = client.get("/admin/models").json()["models"]
        assert models

        (tmp_path / "config.yaml").write_text(
            """
providers:
  enabled:
    - openrouter
  openrouter:
    enabled: true
    discovery_enabled: true
    inference_enabled: false
""".strip(),
            encoding="utf-8",
        )

        client.app.state.reload_settings()

        assert client.app.state.settings.openrouter_inference_enabled is False
        with pytest.raises(KeyError):
            client.app.state.registry.get("openrouter")

        ready = client.get("/readyz")
        assert ready.status_code == 503

        db = client.app.state.db
        with db.read_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS active_count
                FROM models
                WHERE provider_id='openrouter' AND is_active=1
                """
            ).fetchone()
        assert row is not None
        assert int(row["active_count"] or 0) == 0


def test_request_log_enabled_false_suppresses_client_request_logs(tmp_path, monkeypatch):
    with _build_client_with_config(
        tmp_path,
        monkeypatch,
        """
logging:
  request_log_enabled: false
  log_queue_size: 5
providers:
  enabled:
    - openrouter
  openrouter:
    enabled: true
    discovery_enabled: true
    inference_enabled: true
    dev_stub_enabled: true
""",
    ) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"model": "auto", "messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status_code == 200

        db = client.app.state.db
        with db.read_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS client_logs
                FROM request_log
                WHERE request_source='client'
                """
            ).fetchone()
        assert row is not None
        assert int(row["client_logs"] or 0) == 0
        assert db.writer.dropped_low_priority_logs() >= 1


def test_missing_openrouter_key_does_not_enable_stub_by_default(tmp_path, monkeypatch):
    with _build_client_with_config(
        tmp_path,
        monkeypatch,
        """
providers:
  enabled:
    - openrouter
  openrouter:
    enabled: true
    discovery_enabled: true
    inference_enabled: true
""",
    ) as client:
        assert client.app.state.settings.openrouter_dev_stub_enabled is False
        ready = client.get("/readyz")
        assert ready.status_code == 503


def test_missing_openrouter_key_deactivates_persisted_models(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "freelunch.db"))
    db.init()
    db.writer.start()
    now = utc_now_iso()
    db.writer.enqueue(
        """
        INSERT INTO models(
            id, name, provider_id, provider_model_id, provider_base_url, provider_api_key_env,
            context_window, supports_streaming, supports_tools, supports_vision, supports_structured_output,
            supports_system_messages, composite_score, discovered_at, last_seen_at, is_active, is_healthy
        ) VALUES (
            'openrouter/stale-model',
            'stale-model',
            'openrouter',
            'stale-model',
            'https://openrouter.ai/api/v1',
            'OPENROUTER_API_KEY',
            8192,
            1,
            1,
            0,
            0,
            1,
            10.0,
            ?,
            ?,
            1,
            1
        )
        """,
        (now, now),
    )
    db.writer.flush()
    db.writer.stop()

    with _build_client_with_config(
        tmp_path,
        monkeypatch,
        """
providers:
  enabled:
    - openrouter
  openrouter:
    enabled: true
    discovery_enabled: true
    inference_enabled: true
""",
    ) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 503

        with client.app.state.db.read_conn() as conn:
            row = conn.execute(
                """
                SELECT is_active
                FROM models
                WHERE id='openrouter/stale-model'
                """
            ).fetchone()

        assert row is not None
        assert int(row["is_active"] or 0) == 0
        with pytest.raises(KeyError):
            client.app.state.registry.get("openrouter")


def test_openrouter_dev_stub_can_be_enabled_explicitly_in_dev(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    with _build_client_with_config(
        tmp_path,
        monkeypatch,
        """
providers:
  enabled:
    - openrouter
  openrouter:
    enabled: true
    discovery_enabled: true
    inference_enabled: true
    dev_stub_enabled: true
""",
    ) as client:
        assert client.app.state.settings.openrouter_dev_stub_enabled is True
        ready = client.get("/readyz")
        assert ready.status_code == 200


def test_openrouter_dev_stub_flag_is_ignored_outside_dev_env(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    with _build_client_with_config(
        tmp_path,
        monkeypatch,
        """
providers:
  enabled:
    - openrouter
  openrouter:
    enabled: true
    discovery_enabled: true
    inference_enabled: true
    dev_stub_enabled: true
""",
    ) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 503
