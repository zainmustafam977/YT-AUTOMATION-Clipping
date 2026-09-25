import pytest
from starlette.testclient import TestClient
from shorts_engine.dashboard.app import app


@pytest.fixture
def client():
    return TestClient(app)


def test_prometheus_metrics_endpoint(client):
    """Verifies Prometheus /metrics endpoint returns standard text format and key metrics."""
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    text = resp.text

    # Verify standard metrics
    assert "shorts_uptime_seconds" in text
    assert "shorts_host_memory_used_percent" in text
    assert "shorts_host_memory_available_bytes" in text
    assert "shorts_configured_api_keys" in text
    assert 'shorts_configured_api_keys{provider="groq"}' in text
    assert 'shorts_configured_api_keys{provider="gemini"}' in text
    assert 'shorts_configured_api_keys{provider="openrouter"}' in text
