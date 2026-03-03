from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from src.main import create_app
    from src.providers.openrouter import OpenRouterAdapter

    os.environ["OPENROUTER_API_KEY"] = "test-key"
    os.environ["DATABASE_URL"] = str(tmp_path / "test.db")
    os.environ["MAX_FAILOVER_ATTEMPTS"] = "3"

    def fake_discover(self):
        return [
            {
                "provider": "openrouter",
                "model_name": "openrouter/test-model",
                "display_name": "OpenRouter Test",
                "supports_tools": 1,
                "supports_vision": 0,
                "supports_streaming": 1,
                "is_healthy": 1,
                "score": 1.0,
            }
        ]

    monkeypatch.setattr(OpenRouterAdapter, "discover_models", fake_discover)

    app = create_app()
    with TestClient(app) as c:
        yield c
