from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from src.security_throttle import FailureThrottle


def test_failure_throttle_blocks_resets_and_expires_with_monotonic_window():
    now = [10.0]
    throttle = FailureThrottle(2, 5, 2, clock=lambda: now[0])
    throttle.fail("peer")
    assert throttle.retry_after("peer") == 0
    throttle.fail("peer")
    assert throttle.retry_after("peer") == 5

    throttle.reset("peer")
    assert throttle.retry_after("peer") == 0
    throttle.fail("peer")
    throttle.fail("peer")
    now[0] += 5
    assert throttle.retry_after("peer") == 0


def test_failure_throttle_bounds_entries_and_does_not_retain_raw_keys():
    throttle = FailureThrottle(2, 5, 1, clock=lambda: 10.0)
    throttle.fail("sensitive-client-id")
    throttle.fail("another-client-id")

    assert len(throttle._entries) == 1
    assert "sensitive-client-id" not in repr(throttle._entries)
    assert "another-client-id" not in repr(throttle._entries)


def test_concurrent_first_failures_are_counted():
    throttle = FailureThrottle(20, 30, 10)
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda _index: throttle.fail("shared-peer"), range(20)))

    assert throttle.retry_after("shared-peer") > 0


def test_gateway_auth_throttle_returns_429_and_success_resets(client):
    assert client.put("/admin/gateway-auth", json={"key": "test-token"}).status_code == 200
    client.app.state.settings.security_auth_failure_limit = 2

    bad = client.get("/admin/models", headers={"Authorization": "Bearer bad"})
    assert bad.status_code == 401
    good = client.get("/admin/models", headers={"Authorization": "Bearer test-token"})
    assert good.status_code == 200
    assert (
        client.get("/admin/models", headers={"X-Forwarded-For": "198.51.100.2"}).status_code == 401
    )
    blocked = client.get("/admin/models", headers={"X-Forwarded-For": "203.0.113.3"})
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0


def test_vault_unlock_throttle_returns_429_and_success_resets(client):
    assert (
        client.post("/admin/secrets/vault/setup", json={"password": "good-password"}).status_code
        == 200
    )
    assert client.post("/admin/secrets/vault/lock").status_code == 200
    client.app.state.settings.security_auth_failure_limit = 2

    bad = client.post("/admin/secrets/vault/unlock", json={"password": "bad-password"})
    assert bad.status_code == 401
    good = client.post("/admin/secrets/vault/unlock", json={"password": "good-password"})
    assert good.status_code == 200
    assert client.post("/admin/secrets/vault/lock").status_code == 200
    assert (
        client.post("/admin/secrets/vault/unlock", json={"password": "bad-password"}).status_code
        == 401
    )
    blocked = client.post("/admin/secrets/vault/unlock", json={"password": "bad-password"})
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
