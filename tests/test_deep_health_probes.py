import pytest
from starlette.testclient import TestClient
from shorts_engine.dashboard.app import app


@pytest.fixture
def client():
    return TestClient(app)


def test_livez_probe(client):
    """Verifies liveness probe returns HTTP 200 with status=alive and valid uptime."""
    resp = client.get("/livez")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "alive"
    assert "uptime_seconds" in data
    assert data["uptime_seconds"] >= 0


def test_readyz_probe(client):
    """Verifies readiness probe executes deep checks and returns HTTP 200 when healthy."""
    resp = client.get("/readyz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["is_ready"] is True
    checks = data["checks"]
    assert "database" in checks
    assert checks["database"]["status"] == "healthy"
    assert "storage" in checks
    assert "media_tools" in checks
    assert "resources" in checks


def test_healthz_probe(client):
    """Verifies top-level health summary probe returns HTTP 200 with healthy state."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "database" in data["checks"]
