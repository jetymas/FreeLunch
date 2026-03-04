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
    assert settings.public_settings()["database.busy_timeout_ms"] == 3210


def test_database_connections_apply_busy_timeout(tmp_path):
    db = Database(str(tmp_path / "busy-timeout.db"), busy_timeout_ms=3210)
    db.init()

    with db.read_conn() as conn:
        row = conn.execute("PRAGMA busy_timeout").fetchone()

    assert row is not None
    assert row[0] == 3210
