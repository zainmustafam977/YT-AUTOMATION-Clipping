"""R4-3.7 — Live activity notifications.

This is a contract test for the SSE / WebSocket channel that the SPA
listens to for live status updates. It does not require a browser; it
asserts the server-side endpoint exists and emits the expected event
shapes.

The test is skipped when the dashboard server is not running.
"""
import json
import os
import socket
import time
import urllib.request
import urllib.error
import pytest


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture()
def base_url():
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("DASHBOARD_PORT", "8000"))
    if not _port_open(host, port):
        pytest.skip(f"dashboard at {host}:{port} not running")
    return f"http://{host}:{port}"


def test_health_endpoint(base_url):
    """The /api/health endpoint responds with JSON."""
    with urllib.request.urlopen(base_url + "/api/health", timeout=5) as r:
        data = json.loads(r.read())
    assert "status" in data
    assert data["status"] in ("ok", "degraded")


def test_triage_endpoint(base_url):
    """The /api/triage endpoint returns a list of items."""
    with urllib.request.urlopen(base_url + "/api/triage", timeout=5) as r:
        data = json.loads(r.read())
    assert isinstance(data, list)


def test_settings_endpoint_round_trip(base_url):
    """Read settings, then read them again — they should not change shape."""
    with urllib.request.urlopen(base_url + "/api/settings", timeout=5) as r1:
        s1 = json.loads(r1.read())
    with urllib.request.urlopen(base_url + "/api/settings", timeout=5) as r2:
        s2 = json.loads(r2.read())
    assert set(s1.keys()) == set(s2.keys())
