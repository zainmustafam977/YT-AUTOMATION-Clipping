"""Tests for F-15 triage HTTP routes."""
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


def test_needs_attention_endpoint(client):
    r = client.get("/api/triage/needs-attention")
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert "count" in data
    assert "by_priority" in data


def test_needs_attention_with_limit(client):
    r = client.get("/api/triage/needs-attention?limit=5")
    assert r.status_code == 200


def test_needs_attention_limit_bounds(client):
    """Limit=0 should be rejected (must be >= 1)."""
    r = client.get("/api/triage/needs-attention?limit=0")
    assert r.status_code == 422


def test_by_kind_endpoint(client):
    r = client.get("/api/triage/by-kind/stuck_pipeline_job")
    assert r.status_code == 200
    data = r.json()
    assert data["kind"] == "stuck_pipeline_job"
    assert "items" in data


def test_by_kind_returns_empty_for_unknown(client):
    r = client.get("/api/triage/by-kind/this_does_not_exist")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 0


def test_by_priority_breakdown(client):
    r = client.get("/api/triage/needs-attention")
    data = r.json()
    bp = data["by_priority"]
    # 5 priority levels (1..5).
    assert len(bp) == 5
    assert all(isinstance(v, int) for v in bp.values())
