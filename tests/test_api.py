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


def _update_model(client, model_id: str, **fields):
    assignments = ", ".join(f"{key}=?" for key in fields)
    db = client.app.state.db
    db.writer.enqueue(
        f"UPDATE models SET {assignments} WHERE id=?",
        (*fields.values(), model_id),
    )
    db.writer.flush()


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


def test_success_logs_token_estimation_observability_fields(client, monkeypatch):
    _update_model(
        client,
        "openrouter/openrouter/free",
        tokenizer_family="cl100k_base",
    )

    async def fake_chat(self, request_body, model):
        return ChatResult(
            payload={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": model,
                "choices": [],
            },
            prompt_tokens=48,
            completion_tokens=8,
            total_tokens=56,
            ttfb_ms=10,
        )

    monkeypatch.setattr(OpenRouterAdapter, "chat_completions", fake_chat)
    response = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "Count these tokens."}]},
    )
    assert response.status_code == 200

    db = client.app.state.db
    db.writer.flush()
    with db.read_conn() as conn:
        row = conn.execute(
            """
            SELECT selected_provider_model_id, selected_tokenizer_family,
                   estimated_prompt_tokens, prompt_tokens
            FROM request_log
            WHERE request_source='client'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert row is not None
    assert row["selected_provider_model_id"] == "openrouter/free"
    assert row["selected_tokenizer_family"] == "cl100k_base"
    assert int(row["estimated_prompt_tokens"] or 0) > 0
    assert row["prompt_tokens"] == 48


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
        if model == "openrouter/free":
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
        "UPDATE models SET composite_score=80.0, avg_latency_ms=3000 WHERE id='openrouter/openrouter/free'"
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
        "UPDATE models SET composite_score=80.0, avg_latency_ms=3000 WHERE id='openrouter/openrouter/free'"
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
    assert response.json()["model"] == "openrouter/free"


def test_token_estimation_routes_away_from_small_context_models(client, monkeypatch):
    _insert_backup_model(
        client, model_id="openrouter/model-large-context", provider_model_id="model-large-context"
    )
    _update_model(
        client,
        "openrouter/openrouter/free",
        composite_score=80.0,
        context_window=64,
    )
    _update_model(
        client,
        "openrouter/model-large-context",
        composite_score=60.0,
        context_window=8192,
    )

    async def fake_chat(self, request_body, model):
        return ChatResult(
            payload={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": model,
                "choices": [],
            }
        )

    monkeypatch.setattr(OpenRouterAdapter, "chat_completions", fake_chat)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "x" * 1200}],
        },
    )
    assert response.status_code == 200
    assert response.json()["model"] == "model-large-context"


def test_tokenizer_family_estimation_routes_away_from_tighter_tokenizers(client, monkeypatch):
    _insert_backup_model(
        client,
        model_id="openrouter/model-roomy-tokenizer",
        provider_model_id="model-roomy-tokenizer",
    )
    _update_model(
        client,
        "openrouter/openrouter/free",
        composite_score=80.0,
        context_window=380,
        tokenizer_family="llama3",
    )
    _update_model(
        client,
        "openrouter/model-roomy-tokenizer",
        composite_score=60.0,
        context_window=500,
        tokenizer_family="cl100k_base",
    )

    async def fake_chat(self, request_body, model):
        return ChatResult(
            payload={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": model,
                "choices": [],
            }
        )

    monkeypatch.setattr(OpenRouterAdapter, "chat_completions", fake_chat)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "x" * 1900}],
        },
    )
    assert response.status_code == 200
    assert response.json()["model"] == "model-roomy-tokenizer"


def test_structured_message_metadata_counts_toward_context_requirements(client, monkeypatch):
    _insert_backup_model(
        client,
        model_id="openrouter/model-metadata-room",
        provider_model_id="model-metadata-room",
    )
    _update_model(
        client,
        "openrouter/openrouter/free",
        composite_score=80.0,
        context_window=120,
    )
    _update_model(
        client,
        "openrouter/model-metadata-room",
        composite_score=60.0,
        context_window=4096,
    )

    async def fake_chat(self, request_body, model):
        return ChatResult(
            payload={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": model,
                "choices": [],
            }
        )

    monkeypatch.setattr(OpenRouterAdapter, "chat_completions", fake_chat)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [
                {"role": "user", "content": "Use a tool."},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "lookup",
                                "arguments": '{"query":"' + ("widgets-" * 40) + '"}',
                            },
                        }
                    ],
                },
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["model"] == "model-metadata-room"


def test_structured_multimodal_requests_require_vision_models(client, monkeypatch):
    _insert_backup_model(
        client, model_id="openrouter/model-vision", provider_model_id="model-vision"
    )
    _update_model(
        client,
        "openrouter/openrouter/free",
        composite_score=80.0,
        supports_vision=0,
    )
    _update_model(
        client,
        "openrouter/model-vision",
        composite_score=60.0,
        supports_vision=1,
    )

    async def fake_chat(self, request_body, model):
        return ChatResult(
            payload={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": model,
                "choices": [],
            }
        )

    monkeypatch.setattr(OpenRouterAdapter, "chat_completions", fake_chat)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this image"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "https://example.com/image.png"},
                        },
                    ],
                }
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["model"] == "model-vision"


def test_max_completion_tokens_filters_models_by_output_limit(client, monkeypatch):
    _insert_backup_model(
        client, model_id="openrouter/model-output", provider_model_id="model-output"
    )
    _update_model(
        client,
        "openrouter/openrouter/free",
        composite_score=80.0,
        max_output_tokens=32,
    )
    _update_model(
        client,
        "openrouter/model-output",
        composite_score=60.0,
        max_output_tokens=512,
    )

    async def fake_chat(self, request_body, model):
        return ChatResult(
            payload={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": model,
                "choices": [],
            }
        )

    monkeypatch.setattr(OpenRouterAdapter, "chat_completions", fake_chat)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "max_completion_tokens": 128,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert response.status_code == 200
    assert response.json()["model"] == "model-output"


def test_context_exceeded_returns_400_without_penalizing_model_health(client, monkeypatch):
    _update_model(
        client,
        "openrouter/openrouter/free",
        tokenizer_family="llama3",
    )

    async def fail_chat(self, request_body, model):
        raise ProviderRetryableError("too many tokens", category="CONTEXT_EXCEEDED")

    monkeypatch.setattr(OpenRouterAdapter, "chat_completions", fail_chat)
    response = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 400

    db = client.app.state.db
    db.writer.flush()
    with db.read_conn() as conn:
        row = conn.execute(
            "SELECT consecutive_failures, is_healthy FROM models WHERE id='openrouter/openrouter/free'"
        ).fetchone()
        log_row = conn.execute(
            """
            SELECT selected_provider_model_id, selected_tokenizer_family,
                   estimated_prompt_tokens, gateway_error_category
            FROM request_log
            WHERE request_source='client'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert row is not None
    assert row["consecutive_failures"] == 0
    assert row["is_healthy"] == 1
    assert log_row is not None
    assert log_row["selected_provider_model_id"] == "openrouter/free"
    assert log_row["selected_tokenizer_family"] == "llama3"
    assert int(log_row["estimated_prompt_tokens"] or 0) > 0
    assert log_row["gateway_error_category"] == "CONTEXT_EXCEEDED"
