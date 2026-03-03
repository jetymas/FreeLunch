from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.providers.base import ChatResult, ProviderRetryableError
from src.providers.openrouter import OpenRouterAdapter


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readyz(client):
    response = client.get("/readyz")
    assert response.status_code == 200


def test_chat_completions_auto(client, monkeypatch):
    def fake_chat(self, request_body, model):
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
    def fail_chat(self, request_body, model):
        raise ProviderRetryableError("temporary provider outage")

    monkeypatch.setattr(OpenRouterAdapter, "chat_completions", fail_chat)
    response = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "temporary provider outage"
