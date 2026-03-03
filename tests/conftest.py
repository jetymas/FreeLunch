from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path) -> Generator[TestClient, None, None]:
    os.environ["DATABASE_URL"] = str(tmp_path / "test.db")
    from src.main import app

    with TestClient(app) as c:
        yield c
