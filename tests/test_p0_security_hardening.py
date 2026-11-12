"""Regression tests for the P0 security hardening.

Covers:
* configurable Host header allowlist (reverse-proxy deployments);
* anonymous access is rejected for overlay and saved-view endpoints.
"""
import pytest
from fastapi.testclient import TestClient

from shorts_engine.config.settings import settings
from shorts_engine.dashboard.app import create_dashboard_app


def _app_with_hosts(monkeypatch, hosts: str):
    monkeypatch.setattr(settings, "DASHBOARD_ALLOWED_HOSTS", hosts)
    # The middleware reads the allowlist when the app is constructed.
    return create_dashboard_app()


def test_public_domain_allowed_when_configured(monkeypatch):
    app = _app_with_hosts(monkeypatch, "localhost,127.0.0.1,shorts.example.com")
    client = TestClient(app)
    assert client.get("/login", headers={"Host": "shorts.example.com"}).status_code == 200
    assert client.get("/login", headers={"Host": "localhost:8080"}).status_code == 200


def test_unlisted_host_still_rejected(monkeypatch):
    app = _app_with_hosts(monkeypatch, "localhost,shorts.example.com")
    client = TestClient(app)
    response = client.get("/login", headers={"Host": "evil.example.com"})
    assert response.status_code == 400
    assert "DASHBOARD_ALLOWED_HOSTS" in response.json()["detail"]


def test_wildcard_disables_host_filtering(monkeypatch):
    app = _app_with_hosts(monkeypatch, "*")
    client = TestClient(app)
    assert client.get("/login", headers={"Host": "anything.example.net"}).status_code == 200


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/overlays/presets"),
    ("GET", "/api/overlays/audit"),
    ("POST", "/api/overlays/seed-builtins"),
    ("GET", "/api/saved-views"),
    ("POST", "/api/saved-views"),
])
def test_overlay_and_saved_view_endpoints_reject_anonymous(method, path):
    client = TestClient(create_dashboard_app())
    assert client.request(method, path).status_code == 401
