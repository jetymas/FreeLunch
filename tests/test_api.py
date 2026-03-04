from __future__ import annotations

from src.providers.base import ChatResult, ProviderFatalError, ProviderRetryableError
from src.providers.openrouter import OpenRouterAdapter


def _insert_backup_model(
    client, model_id: str = "openrouter/test-backup", provider_model_id: str = "test-backup"
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


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readyz(client):
    response = client.get("/readyz")
    assert response.status_code == 200


def test_chat_completions_auto(client, monkeypatch):
    async def fake_chat(self, request_body, model):
        last = request_body["messages"][-1]["content"]
        return ChatResult(
            payload={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": f"Echo: {last}"},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    monkeypatch.setattr(OpenRouterAdapter, "chat_completions", fake_chat)
    response = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "Echo: hi"


def test_chat_completions_returns_503_after_retryable_failures(client, monkeypatch):
    async def fail_chat(self, request_body, model):
        raise ProviderRetryableError("temporary provider outage")

    monkeypatch.setattr(OpenRouterAdapter, "chat_completions", fail_chat)
    response = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 502


def test_chat_completions_returns_auth_error_without_failover(client, monkeypatch):
    async def fail_chat(self, request_body, model):
        raise ProviderFatalError("bad api key", category="AUTH_ERROR", status_code=401)

    monkeypatch.setattr(OpenRouterAdapter, "chat_completions", fail_chat)
    response = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 401


def test_chat_completions_fails_over_to_next_candidate_on_retryable_error(client, monkeypatch):
    _insert_backup_model(client)

    async def fail_then_succeed(self, request_body, model):
        if model == "openrouter/auto":
            raise ProviderRetryableError(
                "temporary provider outage", category="PROVIDER_UNAVAILABLE"
            )
        return ChatResult(
            payload={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "backup reply"},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    monkeypatch.setattr(OpenRouterAdapter, "chat_completions", fail_then_succeed)
    response = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "backup reply"

    logs = client.get("/admin/logs?limit=5").json()["logs"]
    assert len(logs) >= 2
    latest = logs[0]
    previous = logs[1]
    assert latest["success"] is True
    assert latest["was_fallback"] is True
    assert previous["success"] is False
    assert previous["gateway_error_category"] == "PROVIDER_UNAVAILABLE"


def test_request_preference_header_reranks_candidates(client, monkeypatch):
    _insert_backup_model(client, model_id="openrouter/model-fast", provider_model_id="model-fast")
    db = client.app.state.db
    db.writer.enqueue(
        "UPDATE models SET composite_score=80.0, avg_latency_ms=3000 WHERE id='openrouter/openrouter/auto'"
    )
    db.writer.enqueue(
        "UPDATE models SET composite_score=60.0, avg_latency_ms=50 WHERE id='openrouter/model-fast'"
    )
    db.writer.flush()

    async def fake_chat(self, request_body, model):
        return ChatResult(
            payload={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": model},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    monkeypatch.setattr(OpenRouterAdapter, "chat_completions", fake_chat)
    response = client.post(
        "/v1/chat/completions",
        headers={"X-Gateway-Preference": "latency", "X-Gateway-Max-Latency-Ms": "500"},
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
    assert response.json()["model"] == "model-fast"


def test_request_preference_headers_can_be_disabled(client, monkeypatch):
    client.app.state.settings.routing_enable_request_preference_headers = False
    _insert_backup_model(
        client, model_id="openrouter/model-fast-2", provider_model_id="model-fast-2"
    )
    db = client.app.state.db
    db.writer.enqueue(
        "UPDATE models SET composite_score=80.0, avg_latency_ms=3000 WHERE id='openrouter/openrouter/auto'"
    )
    db.writer.enqueue(
        "UPDATE models SET composite_score=60.0, avg_latency_ms=50 WHERE id='openrouter/model-fast-2'"
    )
    db.writer.flush()

    async def fake_chat(self, request_body, model):
        return ChatResult(
            payload={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": model},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    monkeypatch.setattr(OpenRouterAdapter, "chat_completions", fake_chat)
    response = client.post(
        "/v1/chat/completions",
        headers={"X-Gateway-Preference": "latency", "X-Gateway-Max-Latency-Ms": "500"},
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
    assert response.json()["model"] == "openrouter/auto"
