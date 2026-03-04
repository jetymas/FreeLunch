from __future__ import annotations

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
        if model == "openrouter/auto":
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
    assert "models" in health_payload
    assert "scheduler" in health_payload

    config_response = client.get("/admin/config")
    assert config_response.status_code == 200
    config_payload = config_response.json()
    assert "overrides" in config_payload
    assert "effective" in config_payload


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


def test_admin_config_override_reschedules_health_job(client):
    response = client.put(
        "/admin/config/health.probe_interval_minutes",
        json={"value": 10},
    )
    assert response.status_code == 200
    job = client.app.state.scheduler.get_job("health")
    assert job is not None
    assert int(job.trigger.interval.total_seconds()) == 600


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
