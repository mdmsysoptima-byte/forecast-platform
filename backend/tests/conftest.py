"""Test fixtures. A throwaway database and store are built önce per session."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

TMP = Path(tempfile.mkdtemp(prefix="forecast-tests-"))
os.environ["APP_DB_URL"] = f"sqlite:///{(TMP / 'app.db').as_posix()}"
os.environ["STORE_PATH"] = (TMP / "store.db").as_posix()
os.environ["STORE_BACKEND"] = "local"
os.environ["SECTOR"] = "demo"
os.environ["DRAFT_TTL_DAYS"] = "7"


@pytest.fixture(scope="session", autouse=True)
def demo_environment():
    from app.demo.loader import load_demo

    summary = load_demo(reset=True)
    assert summary["series"] == 200
    yield summary


@pytest.fixture()
def db():
    from app.core.db import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def token_for(client, email: str) -> dict:
    res = client.post("/api/v1/auth/login", json={"email": email, "password": "demo"})
    assert res.status_code == 200, res.text
    return {"authorization": f"Bearer {res.json()['token']}"}


@pytest.fixture()
def planner_headers(client):
    return token_for(client, "planner@demo.local")


@pytest.fixture()
def sales_headers(client):
    return token_for(client, "sales@demo.local")


@pytest.fixture()
def finance_headers(client):
    return token_for(client, "finance@demo.local")


@pytest.fixture()
def admin_headers(client):
    return token_for(client, "admin@demo.local")
