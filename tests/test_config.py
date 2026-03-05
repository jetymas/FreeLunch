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


def test_settings_post_init_fills_openrouter_defaults_and_empty_provider_guards():
    settings = Settings(
        providers_enabled=("dummy",),
        provider_enabled={"dummy": True},
        provider_discovery_enabled={"dummy": False},
        provider_inference_enabled={"dummy": True},
        provider_active_probe_enabled={"dummy": False},
        provider_bootstrap_config={"dummy": {"api_key_env": "DUMMY_API_KEY"}},
        health_daily_request_budget_by_provider={"dummy": 3},
        openrouter_enabled=False,
        openrouter_discovery_enabled=False,
        openrouter_inference_enabled=False,
        openrouter_active_probe_enabled=False,
    )

    assert settings.provider_enabled["openrouter"] is False
    assert settings.provider_discovery_enabled["openrouter"] is False
    assert settings.provider_inference_enabled["openrouter"] is False
    assert settings.provider_active_probe_enabled["openrouter"] is False
    assert settings.provider_bootstrap_config["openrouter"] == {}
    assert settings.startup_probe_limit == settings.health_startup_probe_limit
    assert settings.known_provider_ids == ("dummy", "openrouter")
    assert settings.is_provider_enabled("   ") is False
    assert settings.is_provider_discovery_enabled("   ") is False
    assert settings.is_provider_inference_enabled("   ") is False
    assert settings.is_provider_active_probe_enabled("   ") is False
    assert settings.get_provider_bootstrap_config("   ") == {}
    assert settings.get_provider_bootstrap_config("dummy") == {"api_key_env": "DUMMY_API_KEY"}


def test_apply_overrides_updates_runtime_policy_fields_and_provider_maps():
    settings = Settings(
        providers_enabled=("openrouter", "dummy"),
        provider_enabled={"openrouter": True, "dummy": True},
        provider_discovery_enabled={"openrouter": True, "dummy": True},
        provider_inference_enabled={"openrouter": True, "dummy": True},
        provider_active_probe_enabled={"openrouter": True, "dummy": False},
        health_daily_request_budget_by_provider={"openrouter": 5, "dummy": 1},
    )

    settings.apply_overrides(
        {
            "discovery.interval_minutes": 0,
            "ranking.interval_minutes": 0,
            "routing.max_attempts": "4",
            "routing.enable_request_preference_headers": 0,
            "health.probe_interval_minutes": 0,
            "health.probe_timeout_seconds": 0,
            "health.probe_concurrency": 0,
            "health.startup_probe_limit": -2,
            "health.max_probes_per_run": -1,
            "health.stale_after_minutes": 0,
            "health.top_n_stale_probe": -1,
            "health.consecutive_failures_threshold": 0,
            "health.cooldown_minutes": 0,
            "health.max_backoff_exponent": -5,
            "health.probe_max_tokens": 0,
            "ranking.fallback_model": "dummy/fallback",
            "logging.request_log_retention_days": 0,
            "logging.runtime_enabled": 0,
            "logging.runtime_verbosity": "debug",
            "providers.openrouter.active_probe_enabled": False,
            "providers.dummy.active_probe_enabled": True,
            "health.daily_request_budget_by_provider.openrouter": -3,
            "health.daily_request_budget_by_provider.dummy": 8,
            "ranking.weights.latency": "0.77",
        }
    )

    assert settings.discovery_interval_minutes == 1
    assert settings.ranking_interval_minutes == 1
    assert settings.routing_max_attempts == 4
    assert settings.routing_enable_request_preference_headers is False
    assert settings.health_probe_interval_minutes == 1
    assert settings.health_probe_timeout_seconds == 1
    assert settings.health_probe_concurrency == 1
    assert settings.health_startup_probe_limit == -2
    assert settings.health_max_probes_per_run == 0
    assert settings.health_stale_after_minutes == 1
    assert settings.health_top_n_stale_probe == 0
    assert settings.health_consecutive_failures_threshold == 1
    assert settings.health_cooldown_minutes == 1
    assert settings.health_max_backoff_exponent == 0
    assert settings.health_probe_max_tokens == 1
    assert settings.ranking_fallback_model == "dummy/fallback"
    assert settings.logging_request_log_retention_days == 1
    assert settings.logging_runtime_enabled is False
    assert settings.logging_runtime_verbosity == "debug"
    assert settings.provider_active_probe_enabled["openrouter"] is False
    assert settings.provider_active_probe_enabled["dummy"] is True
    assert settings.openrouter_active_probe_enabled is False
    assert settings.health_daily_request_budget_by_provider["openrouter"] == 0
    assert settings.health_daily_request_budget_by_provider["dummy"] == 8
    assert settings.ranking_weights["latency"] == 0.77


def test_settings_coercion_helpers_and_env_bool(monkeypatch):
    assert Settings._coerce_float_mapping(None) == {}
    assert Settings._coerce_int_mapping("not-a-mapping") == {}
    assert Settings._coerce_bool_mapping(123) == {}
    assert Settings._coerce_provider_bootstrap_config(None) == {}
    assert Settings._coerce_provider_bootstrap_config(
        {"valid": {"x": 1}, "invalid": "nope", "": {"ignored": True}}
    ) == {"valid": {"x": 1}}
    assert Settings._coerce_string_list("not-a-list") == []
    assert Settings._coerce_provider_sections("not-a-mapping") == {}
    assert Settings._coerce_provider_sections(
        {"enabled": ["dummy"], "dummy": {"x": 1}, "nope": 1}
    ) == {"dummy": {"x": 1}}
    assert (
        Settings._provider_from_probe_budget_key("health.daily_request_budget_by_provider.dummy")
        == "dummy"
    )
    assert (
        Settings._provider_from_probe_budget_key("health.daily_request_budget_by_provider.") is None
    )
    assert (
        Settings._provider_from_probe_budget_key("health.daily_request_budget_by_providerx.dummy")
        is None
    )

    monkeypatch.delenv("TEST_SETTINGS_BOOL", raising=False)
    assert Settings._env_bool("TEST_SETTINGS_BOOL", 0) is False
    monkeypatch.setenv("TEST_SETTINGS_BOOL", " yes ")
    assert Settings._env_bool("TEST_SETTINGS_BOOL", False) is True
    monkeypatch.setenv("TEST_SETTINGS_BOOL", "false")
    assert Settings._env_bool("TEST_SETTINGS_BOOL", True) is False
