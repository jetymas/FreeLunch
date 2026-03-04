from __future__ import annotations

import sys
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    (tmp_path / "config.yaml").write_text(
        """
discovery:
  leaderboard:
    chatbot_arena:
      enabled: false
    open_llm:
      enabled: false
health:
  startup_probe_limit: 0
providers:
  enabled:
    - openrouter
  openrouter:
    enabled: true
    discovery_enabled: true
    inference_enabled: true
    dev_stub_enabled: true
    active_probe_enabled: false
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", str(tmp_path / "test.db"))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)
    from src.main import app

    with TestClient(app) as c:
        yield c
