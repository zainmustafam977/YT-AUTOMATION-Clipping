"""Tests for E-021 search HTTP routes."""
import pytest

from fastapi.testclient import TestClient

from shorts_engine.db.session import engine, Base
from shorts_engine.db import models  # noqa: F401
from shorts_engine.dashboard.app import app


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def test_search_route(client):
    r = client.get("/api/search?q=sourdough")
    assert r.status_code == 200
    data = r.json()
    assert data["query"] == "sourdough"
    assert "results" in data
    assert "count" in data


def test_search_empty_query_422(client):
    r = client.get("/api/search?q=")
    assert r.status_code == 422


def test_command_palette_route(client):
    r = client.get("/api/command-palette?q=test")
    assert r.status_code == 200
    data = r.json()
    assert data["query"] == "test"
    assert "commands" in data


def test_command_palette_empty(client):
    r = client.get("/api/command-palette?q=zzzznotfound")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 0
