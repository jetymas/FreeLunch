from __future__ import annotations

from src.config import Settings
from src.db import Database


def test_settings_from_env_reads_gateway_logging_and_database_sections(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
gateway:
  host: "127.0.0.1"
  port: 9000
  workers: 2
  log_level: "debug"

logging:
  request_log_enabled: false
  log_queue_size: 1234
  request_log_retention_days: 14
  runtime_enabled: true
  runtime_verbosity: debug
  runtime_queue_size: 321

database:
  path: "custom.db"
  busy_timeout_ms: 3210
""".strip(),
        encoding="utf-8",
    )

    settings = Settings.from_env(str(config_path))

    assert settings.gateway_host == "127.0.0.1"
    assert settings.gateway_port == 9000
    assert settings.gateway_workers == 2
    assert settings.gateway_log_level == "debug"
    assert settings.database_url == "custom.db"
    assert settings.database_busy_timeout_ms == 3210
    assert settings.logging_request_log_enabled is False
    assert settings.logging_log_queue_size == 1234
    assert settings.logging_request_log_retention_days == 14
    assert settings.logging_runtime_enabled is True
    assert settings.logging_runtime_verbosity == "debug"
    assert settings.logging_runtime_queue_size == 321
    assert settings.public_settings()["database.busy_timeout_ms"] == 3210


def test_database_connections_apply_busy_timeout(tmp_path):
    db = Database(str(tmp_path / "busy-timeout.db"), busy_timeout_ms=3210)
    db.init()

    with db.read_conn() as conn:
        row = conn.execute("PRAGMA busy_timeout").fetchone()

    assert row is not None
    assert row[0] == 3210


def test_settings_from_env_builds_provider_agnostic_gating_maps(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_ACTIVE_PROBE_ENABLED", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
providers:
  enabled:
    - openrouter
    - dummy
  openrouter:
    enabled: true
    discovery_enabled: true
    inference_enabled: true
    active_probe_enabled: false
  dummy:
    enabled: true
    discovery_enabled: false
    inference_enabled: true
    active_probe_enabled: false
health:
  daily_request_budget_by_provider:
    dummy: 9
""".strip(),
        encoding="utf-8",
    )

    settings = Settings.from_env(str(config_path))

    assert settings.is_provider_enabled("openrouter") is True
    assert settings.is_provider_enabled("dummy") is True
    assert settings.is_provider_discovery_enabled("dummy") is False
    assert settings.is_provider_inference_enabled("dummy") is True
    assert settings.is_provider_active_probe_enabled("dummy") is False
    assert settings.health_daily_request_budget_by_provider["openrouter"] == 5
    assert settings.health_daily_request_budget_by_provider["dummy"] == 9
    public = settings.public_settings()
    assert public["providers.dummy.enabled"] is True
    assert public["providers.dummy.discovery_enabled"] is False
    assert public["providers.dummy.inference_enabled"] is True
    assert public["providers.dummy.active_probe_enabled"] is False


def test_settings_accepts_provider_agnostic_probe_override_keys():
    assert Settings.is_overridable("providers.openrouter.active_probe_enabled") is True
    assert Settings.is_overridable("providers.dummy.active_probe_enabled") is True
    assert Settings.is_overridable("health.daily_request_budget_by_provider.dummy") is True
