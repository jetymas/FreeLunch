from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime, timezone
from typing import cast

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.db import Database, utc_now_iso
from src.providers.base import (
    ChatResult,
    ProviderFatalError,
    ProviderRetryableError,
    ProviderRuntimeState,
    StreamResult,
)
from src.providers.openai import OpenAIAdapter
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


def _build_client_with_config(
    tmp_path, monkeypatch: pytest.MonkeyPatch, config_text: str
) -> TestClient:
    base_config = {
        "discovery": {
            "leaderboard": {
                "chatbot_arena": {"enabled": False},
                "open_llm": {"enabled": False},
            }
        },
        "health": {"startup_probe_limit": 0},
        "providers": {"openrouter": {"active_probe_enabled": False}},
    }
    user_config = yaml.safe_load(config_text.strip()) or {}
    _merge_test_config(base_config, user_config)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(base_config, sort_keys=False), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)

    import src.main as main_module

    main_module = importlib.reload(main_module)
    return TestClient(main_module.app)


def _merge_test_config(base: dict, override: dict) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge_test_config(base[key], value)
        else:
            base[key] = value


def test_models_endpoint(client):
    response = client.get("/v1/models")
    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "list"
    assert payload["data"][0] == {"id": "auto", "object": "model", "owned_by": "gateway"}
    assert len(payload["data"]) >= 2


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
    assert "secret_management" in health_payload
    assert "maintenance" in health_payload["scheduler"]["jobs"]
    assert "config_refresh" in health_payload["scheduler"]["jobs"]

    config_response = client.get("/admin/config")
    assert config_response.status_code == 200
    config_payload = config_response.json()
    assert "overrides" in config_payload
    assert "effective" in config_payload
    assert "effective_values" in config_payload
    assert "overridable_keys" in config_payload
    assert "logging.runtime_verbosity" in config_payload["effective"]

    secrets_response = client.get("/admin/secrets")
    assert secrets_response.status_code == 200
    secrets_payload = secrets_response.json()
    assert "secret_management" in secrets_payload
    assert secrets_payload["secrets"]


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


def test_admin_secret_endpoints_require_vault_for_mutation(client):
    response = client.put("/admin/secrets/openrouter_api_key", json={"value": "secret"})
    assert response.status_code == 409
    assert response.json()["detail"] == "secret vault is not configured"


def test_managed_provider_secret_updates_registry_and_secret_listing(tmp_path, monkeypatch):
    with _build_client_with_config(
        tmp_path,
        monkeypatch,
        """
providers:
  enabled:
    - openai
  openrouter:
    enabled: false
  openai:
    enabled: true
    discovery_enabled: true
    inference_enabled: true
    api_base: https://openai.custom/v1
""",
    ) as client:
        setup_response = client.post(
            "/admin/secrets/vault/setup",
            json={"password": "vault-password"},
        )
        assert setup_response.status_code == 200
        assert setup_response.json()["secret_management"]["configured"] is True
        assert setup_response.json()["secret_management"]["unlocked"] is True

        response = client.put(
            "/admin/secrets/providers.openai.api_key",
            json={"value": "managed-openai-key"},
        )
        assert response.status_code == 200

        secrets_payload = client.get("/admin/secrets").json()
        openai_secret = next(
            item for item in secrets_payload["secrets"] if item["key"] == "providers.openai.api_key"
        )
        assert openai_secret["source"] == "managed"
        assert openai_secret["configured"] is True

        registered = client.app.state.registry.get_registered("openai")
        assert registered.adapter.api_key == "managed-openai-key"
        assert registered.inference_enabled is True

        lock_response = client.post("/admin/secrets/vault/lock")
        assert lock_response.status_code == 200
        assert lock_response.json()["secret_management"]["unlocked"] is False

        locked_put_response = client.put(
            "/admin/secrets/providers.openai.api_key",
            json={"value": "new-managed-openai-key"},
        )
        assert locked_put_response.status_code == 423
        assert locked_put_response.json()["detail"] == "secret vault is locked"

        unlock_response = client.post(
            "/admin/secrets/vault/unlock",
            json={"password": "vault-password"},
        )
        assert unlock_response.status_code == 200
        assert unlock_response.json()["secret_management"]["unlocked"] is True

        unlocked_put_response = client.put(
            "/admin/secrets/providers.openai.api_key",
            json={"value": "new-managed-openai-key"},
        )
        assert unlocked_put_response.status_code == 200

        updated_registered = client.app.state.registry.get_registered("openai")
        assert updated_registered.adapter.api_key == "new-managed-openai-key"


def test_secret_vault_unlock_rejects_wrong_password(client):
    setup_response = client.post(
        "/admin/secrets/vault/setup",
        json={"password": "correct-password"},
    )
    assert setup_response.status_code == 200

    lock_response = client.post("/admin/secrets/vault/lock")
    assert lock_response.status_code == 200

    unlock_response = client.post(
        "/admin/secrets/vault/unlock",
        json={"password": "wrong-password"},
    )
    assert unlock_response.status_code == 401
    assert unlock_response.json()["detail"] == "invalid vault password"


def test_admin_uninstall_endpoint_reports_host_side_action(client):
    response = client.get("/admin/uninstall")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert "Docker" in payload["reason"]
    assert any(item["label"] == "PowerShell" for item in payload["commands"])


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


def test_admin_models_include_ranking_and_runtime_metadata(client):
    response = client.get("/admin/models")
    assert response.status_code == 200
    model = response.json()["models"][0]
    assert "name" in model
    assert "provider_rank" in model
    assert "avg_latency_ms" in model
    assert "avg_ttfb_ms" in model
    assert "tokenizer_family" in model


def test_admin_config_exposes_groupable_effective_values(client):
    response = client.get("/admin/config")
    assert response.status_code == 200
    payload = response.json()
    first_entry = payload["effective_values"][0]
    assert {"key", "value", "type", "overridable", "section"} <= set(first_entry)
    assert "logging.runtime_verbosity" in payload["overridable_keys"]
    assert {"mode", "enabled", "source", "env_configured", "updated_at"} <= set(
        payload["gateway_auth"]
    )


def test_admin_model_endpoints_return_404_for_unknown_model(client):
    detail_response = client.get("/admin/models/openrouter/does-not-exist")
    assert detail_response.status_code == 404
    assert detail_response.json()["detail"] == "model not found"

    disable_response = client.post("/admin/models/openrouter/does-not-exist/disable")
    assert disable_response.status_code == 404
    assert disable_response.json()["detail"] == "model not found"

    enable_response = client.post("/admin/models/openrouter/does-not-exist/enable")
    assert enable_response.status_code == 404
    assert enable_response.json()["detail"] == "model not found"


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


def test_admin_refresh_returns_503_when_discovery_runner_is_unavailable(client):
    client.app.state.discovery_runner = None
    response = client.post("/admin/refresh")
    assert response.status_code == 503
    assert response.json()["detail"] == "discovery runner unavailable"


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
    assert "request_source" in payload["logs"][0]
    assert "selected_provider_model_id" in payload["logs"][0]
    assert "estimated_prompt_tokens" in payload["logs"][0]


def test_admin_logs_support_provider_and_source_filters(client):
    db = client.app.state.db
    now = utc_now_iso()
    db.writer.enqueue(
        """
        INSERT INTO request_log(
            request_id, timestamp, request_source, selected_model_id, provider_id, success
        ) VALUES
            ('provider-filter-1', ?, 'client', 'openrouter/openrouter/free', 'openrouter', 1),
            ('provider-filter-2', ?, 'probe', 'openrouter/openrouter/free', 'openrouter', 1)
        """,
        (now, now),
    )
    db.writer.flush()

    response = client.get("/admin/logs?limit=20&provider_id=openrouter&request_source=probe")
    assert response.status_code == 200
    payload = response.json()
    assert payload["filters"]["provider_id"] == "openrouter"
    assert payload["filters"]["request_source"] == "probe"
    assert payload["logs"]
    assert all(entry["provider_id"] == "openrouter" for entry in payload["logs"])
    assert all(entry["request_source"] == "probe" for entry in payload["logs"])


def test_admin_logs_success_only_filter_returns_expected_rows(client, monkeypatch):
    ok_response = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "successful request"}]},
    )
    assert ok_response.status_code == 200

    async def fatal_chat(self, request_body, model):
        raise ProviderFatalError("invalid payload", category="INVALID_REQUEST")

    monkeypatch.setattr(OpenRouterAdapter, "chat_completions", fatal_chat)
    fail_response = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "failing request"}]},
    )
    assert fail_response.status_code == 400

    success_logs = client.get("/admin/logs?limit=20&success_only=true").json()["logs"]
    assert success_logs
    assert all(entry["success"] is True for entry in success_logs)

    failure_logs = client.get("/admin/logs?limit=20&success_only=false").json()["logs"]
    assert failure_logs
    assert all(entry["success"] is False for entry in failure_logs)
    assert any(entry["gateway_error_category"] == "INVALID_REQUEST" for entry in failure_logs)


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


def test_admin_config_override_requires_value_field(client):
    response = client.put("/admin/config/routing.max_attempts", json={})
    assert response.status_code == 400
    assert response.json()["detail"] == "missing value"


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


def test_admin_can_set_and_use_managed_gateway_auth_key(client):
    update_response = client.put("/admin/gateway-auth", json={"key": "managed-token"})
    assert update_response.status_code == 200

    auth_payload = update_response.json()
    assert auth_payload["mode"] == "enabled"
    assert auth_payload["enabled"] is True
    assert auth_payload["source"] == "managed"

    unauthorized = client.get("/admin/models")
    assert unauthorized.status_code == 401

    old_placeholder = client.get("/admin/models", headers={"Authorization": "Bearer placeholder"})
    assert old_placeholder.status_code == 401

    authorized = client.get("/admin/models", headers={"Authorization": "Bearer managed-token"})
    assert authorized.status_code == 200

    public_models = client.get("/v1/models", headers={"Authorization": "Bearer managed-token"})
    assert public_models.status_code == 200


def test_admin_can_disable_gateway_auth_even_when_env_key_exists(tmp_path, monkeypatch):
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
        monkeypatch.setenv("GATEWAY_API_KEY", "env-token")
        client.app.state.reload_settings()

        unauthorized = client.get("/admin/models")
        assert unauthorized.status_code == 401

        disable_response = client.delete(
            "/admin/gateway-auth",
            headers={"Authorization": "Bearer env-token"},
        )
        assert disable_response.status_code == 200
        assert disable_response.json()["mode"] == "disabled"
        assert disable_response.json()["enabled"] is False

        after_disable = client.get("/admin/models")
        assert after_disable.status_code == 200

        public_models = client.get("/v1/models")
        assert public_models.status_code == 200


def test_admin_can_revert_gateway_auth_to_environment_inheritance(tmp_path, monkeypatch):
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
        monkeypatch.setenv("GATEWAY_API_KEY", "env-token")
        client.app.state.reload_settings()

        update_response = client.put(
            "/admin/gateway-auth",
            json={"key": "managed-token"},
            headers={"Authorization": "Bearer env-token"},
        )
        assert update_response.status_code == 200
        assert update_response.json()["source"] == "managed"

        env_after_managed = client.get(
            "/admin/models", headers={"Authorization": "Bearer env-token"}
        )
        assert env_after_managed.status_code == 401

        inherit_response = client.post(
            "/admin/gateway-auth/inherit",
            headers={"Authorization": "Bearer managed-token"},
        )
        assert inherit_response.status_code == 200
        inherit_payload = inherit_response.json()
        assert inherit_payload["mode"] == "inherit"
        assert inherit_payload["enabled"] is True
        assert inherit_payload["source"] == "env"

        env_authorized = client.get("/admin/models", headers={"Authorization": "Bearer env-token"})
        assert env_authorized.status_code == 200

        managed_after_inherit = client.get(
            "/admin/models",
            headers={"Authorization": "Bearer managed-token"},
        )
        assert managed_after_inherit.status_code == 401


def test_admin_config_rejects_unknown_override_key(client):
    response = client.put(
        "/admin/config/not.allowed",
        json={"value": "x"},
    )
    assert response.status_code == 400


def test_admin_config_accepts_provider_agnostic_probe_override_key(client):
    response = client.put(
        "/admin/config/providers.dummy.active_probe_enabled",
        json={"value": False},
    )
    assert response.status_code == 200
    assert client.app.state.settings.is_provider_active_probe_enabled("dummy") is False
    effective = client.get("/admin/config").json()["effective"]
    assert effective["providers.dummy.active_probe_enabled"] is False


def test_provider_module_descriptor_bootstraps_registration(tmp_path, monkeypatch):
    from src.providers.registry import ProviderBootstrapContext, ProviderBootstrapDescriptor

    class DummyFactoryAdapter:
        name = "dummyfactory"

        def __init__(self, bootstrap_label: str) -> None:
            self.bootstrap_label = bootstrap_label

        def runtime_state(self) -> ProviderRuntimeState:
            return ProviderRuntimeState(discovery_available=True, inference_available=True)

        def categorize_error(self, status_code, error_code, message):
            return "PROVIDER_UNAVAILABLE", True

        async def discover_models(self):
            return [
                {
                    "id": "dummyfactory/free",
                    "name": "dummyfactory/free",
                    "provider_id": "dummyfactory",
                    "provider_model_id": "dummyfactory/free",
                    "provider_base_url": "https://dummy.example/v1",
                    "provider_api_key_env": "DUMMY_API_KEY",
                    "context_window": 4096,
                    "supports_streaming": 1,
                    "supports_tools": 1,
                    "supports_vision": 0,
                    "supports_structured_output": 0,
                    "supports_system_messages": 1,
                    "provider_rank": 1,
                    "is_healthy": 1,
                }
            ]

        async def chat_completions(self, request_body, model):
            return ChatResult(
                payload={
                    "id": "chatcmpl-dummy",
                    "object": "chat.completion",
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "dummy reply"},
                            "finish_reason": "stop",
                        }
                    ],
                }
            )

        async def stream_chat_completions(self, request_body, model):
            async def gen():
                yield b"data: [DONE]\n\n"

            return StreamResult(events=gen())

        async def probe(self, model, *, max_tokens=1, timeout_seconds=15):
            return await self.chat_completions({"messages": []}, model)

    def _build_dummy_adapter(context: ProviderBootstrapContext):
        bootstrap_label = str(context.provider_config.get("bootstrap_label", "missing"))
        return DummyFactoryAdapter(bootstrap_label)

    module_name = "src.providers.dummyfactory"
    dummy_module = types.ModuleType(module_name)
    dummy_module.PROVIDER_BOOTSTRAP_DESCRIPTOR = ProviderBootstrapDescriptor(
        provider_id="dummyfactory",
        factory=_build_dummy_adapter,
    )
    monkeypatch.setitem(sys.modules, module_name, dummy_module)

    with _build_client_with_config(
        tmp_path,
        monkeypatch,
        """
providers:
  enabled:
    - dummyfactory
  openrouter:
    enabled: false
  dummyfactory:
    enabled: true
    discovery_enabled: true
    inference_enabled: true
    bootstrap_label: configured
""",
    ) as client:
        registered = client.app.state.registry.get_registered("dummyfactory")
        assert registered.name == "dummyfactory"
        assert registered.discovery_enabled is True
        assert registered.inference_enabled is True
        assert isinstance(registered.adapter, DummyFactoryAdapter)
        assert registered.adapter.bootstrap_label == "configured"

        ready = client.get("/readyz")
        assert ready.status_code == 200

        models = client.get("/v1/models").json()["data"]
        assert any(item["id"] == "dummyfactory/free" for item in models)


def test_provider_module_transitive_import_error_is_not_suppressed(tmp_path, monkeypatch):
    from src.providers import registry as registry_module

    original_import_module = registry_module.importlib.import_module

    def fake_import_module(module_name: str, package: str | None = None):
        if module_name == "src.providers.transitivebroken":
            raise ModuleNotFoundError(
                "No module named 'missing_transitive_dependency'",
                name="missing_transitive_dependency",
            )
        return original_import_module(module_name, package)

    monkeypatch.setattr(registry_module.importlib, "import_module", fake_import_module)

    with (
        pytest.raises(ModuleNotFoundError) as exc_info,
        _build_client_with_config(
            tmp_path,
            monkeypatch,
            """
providers:
  enabled:
    - transitivebroken
  openrouter:
    enabled: false
  transitivebroken:
    enabled: true
    discovery_enabled: true
    inference_enabled: true
""",
        ),
    ):
        pass

    assert exc_info.value.name == "missing_transitive_dependency"


@pytest.mark.parametrize(
    ("provider_id", "module_attrs"),
    [
        ("nobootstrap", {}),
        (
            "invalidbootstrap",
            {
                "PROVIDER_BOOTSTRAP_DESCRIPTOR": object(),
                "build_provider_adapter": "not-callable",
            },
        ),
    ],
)
def test_provider_module_without_valid_bootstrap_is_skipped_and_startup_not_ready(
    tmp_path,
    monkeypatch,
    provider_id: str,
    module_attrs: dict[str, object],
):
    module_name = f"src.providers.{provider_id}"
    dummy_module = types.ModuleType(module_name)
    for attr_name, attr_value in module_attrs.items():
        setattr(dummy_module, attr_name, attr_value)
    monkeypatch.setitem(sys.modules, module_name, dummy_module)

    with _build_client_with_config(
        tmp_path,
        monkeypatch,
        f"""
providers:
  enabled:
    - {provider_id}
  openrouter:
    enabled: false
  {provider_id}:
    enabled: true
    discovery_enabled: true
    inference_enabled: true
""",
    ) as client:
        assert client.app.state.settings.is_provider_enabled(provider_id) is True
        assert client.app.state.settings.is_provider_discovery_enabled(provider_id) is True
        assert client.app.state.settings.is_provider_inference_enabled(provider_id) is True
        with pytest.raises(KeyError):
            client.app.state.registry.get_registered(provider_id)
        assert client.app.state.registry.all() == []

        ready = client.get("/readyz")
        assert ready.status_code == 503


def test_startup_discovery_failure_degrades_but_keeps_gateway_alive(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "discovery": {
                    "leaderboard": {
                        "chatbot_arena": {"enabled": False},
                        "open_llm": {"enabled": False},
                    }
                },
                "health": {"startup_probe_limit": 0},
                "providers": {
                    "openrouter": {
                        "enabled": False,
                        "active_probe_enabled": False,
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)

    import src.main as main_module

    main_module = importlib.reload(main_module)

    async def _fail_pipeline(*args, **kwargs):
        raise ProviderRetryableError("forced startup failure", category="PROVIDER_UNAVAILABLE")

    monkeypatch.setattr(main_module, "run_discovery_pipeline", _fail_pipeline)

    with TestClient(main_module.app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        ready = client.get("/readyz")
        assert ready.status_code == 503


def test_provider_module_bootstrap_sanitizes_whitespace_provider_id(tmp_path, monkeypatch):
    from src.providers.registry import ProviderBootstrapContext

    class WhitespaceFactoryAdapter:
        name = "dummywhitespace"

        def runtime_state(self) -> ProviderRuntimeState:
            return ProviderRuntimeState(discovery_available=True, inference_available=True)

        def categorize_error(self, status_code, error_code, message):
            return "PROVIDER_UNAVAILABLE", True

        async def discover_models(self):
            return []

        async def chat_completions(self, request_body, model):
            return ChatResult(payload={"id": "unused", "choices": []})

        async def stream_chat_completions(self, request_body, model):
            async def gen():
                yield b"data: [DONE]\n\n"

            return StreamResult(events=gen())

        async def probe(self, model, *, max_tokens=1, timeout_seconds=15):
            return await self.chat_completions({"messages": []}, model)

    def _build_whitespace_adapter(context: ProviderBootstrapContext):
        assert context.provider_id == "dummywhitespace"
        return WhitespaceFactoryAdapter()

    module_name = "src.providers.dummywhitespace"
    dummy_module = types.ModuleType(module_name)
    dummy_module.build_provider_adapter = _build_whitespace_adapter
    monkeypatch.setitem(sys.modules, module_name, dummy_module)

    with _build_client_with_config(
        tmp_path,
        monkeypatch,
        """
providers:
  enabled:
    - "  dummywhitespace  "
  openrouter:
    enabled: false
  "  dummywhitespace  ":
    enabled: true
    discovery_enabled: true
    inference_enabled: true
""",
    ) as client:
        registered = client.app.state.registry.get_registered("dummywhitespace")
        assert registered.name == "dummywhitespace"
        assert registered.discovery_enabled is False
        assert registered.inference_enabled is False
        with pytest.raises(KeyError):
            client.app.state.registry.get("dummywhitespace")

        ready = client.get("/readyz")
        assert ready.status_code == 503


def test_openai_module_bootstrap_uses_provider_section_api_env_and_base(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_CUSTOM_KEY", "openai-test-key")

    async def fake_discover_models(self):
        assert self.api_key == "openai-test-key"
        assert self.api_base == "https://openai.custom/v1"
        return [
            {
                "id": "openai/gpt-4o-mini",
                "name": "gpt-4o-mini",
                "provider_id": "openai",
                "provider_model_id": "gpt-4o-mini",
                "provider_base_url": self.api_base,
                "provider_api_key_env": self.provider_api_key_env,
                "context_window": 128000,
                "supports_streaming": 1,
                "supports_tools": 1,
                "supports_vision": 1,
                "supports_structured_output": 1,
                "supports_system_messages": 1,
                "provider_rank": 1,
                "is_healthy": 1,
            }
        ]

    monkeypatch.setattr(OpenAIAdapter, "discover_models", fake_discover_models)

    with _build_client_with_config(
        tmp_path,
        monkeypatch,
        """
providers:
  enabled:
    - openai
  openrouter:
    enabled: false
  openai:
    enabled: true
    discovery_enabled: true
    inference_enabled: true
    api_base: https://openai.custom/v1
    api_key_env: OPENAI_CUSTOM_KEY
""",
    ) as client:
        registered = client.app.state.registry.get_registered("openai")
        assert registered.discovery_enabled is True
        assert registered.inference_enabled is True
        assert isinstance(registered.adapter, OpenAIAdapter)
        assert registered.adapter.api_base == "https://openai.custom/v1"
        assert registered.adapter.provider_api_key_env == "OPENAI_CUSTOM_KEY"

        ready = client.get("/readyz")
        assert ready.status_code == 200

        models = client.get("/v1/models").json()["data"]
        assert any(item["owned_by"] == "openai" and item["id"] == "gpt-4o-mini" for item in models)


def test_openai_runtime_gating_disables_provider_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with _build_client_with_config(
        tmp_path,
        monkeypatch,
        """
providers:
  enabled:
    - openai
  openrouter:
    enabled: false
  openai:
    enabled: true
    discovery_enabled: true
    inference_enabled: true
""",
    ) as client:
        registered = client.app.state.registry.get_registered("openai")
        assert isinstance(registered.adapter, OpenAIAdapter)
        assert registered.discovery_enabled is False
        assert registered.inference_enabled is False
        assert client.app.state.registry.all() == []
        with pytest.raises(KeyError):
            client.app.state.registry.get("openai")

        ready = client.get("/readyz")
        assert ready.status_code == 503


@pytest.mark.parametrize(
    ("provider_id", "api_key_env"),
    [
        ("together", "TOGETHER_API_KEY"),
        ("groq", "GROQ_API_KEY"),
        ("deepseek", "DEEPSEEK_API_KEY"),
        ("xai", "XAI_API_KEY"),
        ("cerebras", "CEREBRAS_API_KEY"),
        ("perplexity", "PERPLEXITY_API_KEY"),
        ("nvidia", "NVIDIA_API_KEY"),
    ],
)
def test_openai_compatible_runtime_gating_disables_provider_without_api_key(
    tmp_path,
    monkeypatch,
    provider_id: str,
    api_key_env: str,
):
    monkeypatch.delenv(api_key_env, raising=False)

    with _build_client_with_config(
        tmp_path,
        monkeypatch,
        f"""
providers:
  enabled:
    - {provider_id}
  openrouter:
    enabled: false
  {provider_id}:
    enabled: true
    discovery_enabled: true
    inference_enabled: true
""",
    ) as client:
        app = cast(FastAPI, client.app)
        registered = app.state.registry.get_registered(provider_id)
        assert registered.adapter.name == provider_id
        assert registered.discovery_enabled is False
        assert registered.inference_enabled is False
        assert app.state.registry.all() == []
        with pytest.raises(KeyError):
            app.state.registry.get(provider_id)

        ready = client.get("/readyz")
        assert ready.status_code == 503


def test_missing_openai_key_deactivates_persisted_models(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

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
            'openai/stale-model',
            'stale-model',
            'openai',
            'stale-model',
            'https://api.openai.com/v1',
            'OPENAI_API_KEY',
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
    - openai
  openrouter:
    enabled: false
  openai:
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
                WHERE id='openai/stale-model'
                """
            ).fetchone()

        assert row is not None
        assert int(row["is_active"] or 0) == 0
        with pytest.raises(KeyError):
            client.app.state.registry.get("openai")


def test_unregistered_provider_rows_are_deactivated_when_runtime_inference_is_unavailable(
    tmp_path, monkeypatch
):
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
            'dummy/stale-model',
            'dummy-stale-model',
            'dummy',
            'dummy/stale-model',
            'https://example.com',
            'DUMMY_API_KEY',
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
    - dummy
  dummy:
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
                WHERE id='dummy/stale-model'
                """
            ).fetchone()

        assert row is not None
        assert int(row["is_active"] or 0) == 0


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
        registered = client.app.state.registry.get_registered("openrouter")
        assert registered.name == "openrouter"
        assert registered.discovery_enabled is False
        assert registered.inference_enabled is False
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
        registered = client.app.state.registry.get_registered("openrouter")
        assert registered.name == "openrouter"
        assert registered.discovery_enabled is False
        assert registered.inference_enabled is True
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
        registered = client.app.state.registry.get_registered("openrouter")
        assert registered.name == "openrouter"
        assert registered.inference_enabled is False
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
