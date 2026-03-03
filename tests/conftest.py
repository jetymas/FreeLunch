from __future__ import annotations

import os
import sys
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def client(tmp_path) -> Generator[TestClient, None, None]:
    os.environ["DATABASE_URL"] = str(tmp_path / "test.db")
    os.environ.pop("GATEWAY_API_KEY", None)
    from src.main import app

    with TestClient(app) as c:
        yield c
