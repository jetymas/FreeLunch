from __future__ import annotations


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
