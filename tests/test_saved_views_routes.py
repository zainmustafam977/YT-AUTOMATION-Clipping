"""Tests for EASE-02 saved views HTTP routes.

Every saved-view endpoint requires authentication, so these tests drive an
operator-authenticated client: a session cookie plus a matching CSRF token for
the mutating calls. Anonymous access is covered separately by
``tests/test_p0_security_hardening.py``.
"""
import pytest

from fastapi.testclient import TestClient

from shorts_engine.db.session import engine, SessionLocal, Base
from shorts_engine.db import models  # noqa: F401
from shorts_engine.dashboard.app import app
from shorts_engine.dashboard.auth import (
    create_session_token,
    generate_csrf_token,
    SESSION_COOKIE_NAME,
)
from shorts_engine.db.models import User, UserRole


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(scope="module")
def auth_token():
    """Session token for an operator account; saved-view routes require auth."""
    db = SessionLocal()
    user = db.query(User).filter_by(username="saved_views_test_operator").first()
    if not user:
        user = User(
            username="saved_views_test_operator",
            password_hash="dummy_hash",
            role=UserRole.OPERATOR,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    token = create_session_token(user.id)
    db.close()
    return token


@pytest.fixture
def client(auth_token):
    c = TestClient(app, headers={"X-CSRF-Token": generate_csrf_token(auth_token)})
    c.cookies.set(SESSION_COOKIE_NAME, auth_token)
    return c


def test_create_saved_view(client):
    import uuid
    name = f"ready-clips-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/saved-views", json={
        "name": name,
        "expression": "status=ready",
        "owner": f"alice-{uuid.uuid4().hex[:8]}",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["name"] == name
    assert "id" in data


def test_create_duplicate_returns_409(client):
    import uuid
    owner = f"bob-{uuid.uuid4().hex[:8]}"
    name = f"dup-{uuid.uuid4().hex[:8]}"
    body = {"name": name, "expression": "x", "owner": owner}
    r1 = client.post("/api/saved-views", json=body)
    assert r1.status_code == 200, r1.text
    r2 = client.post("/api/saved-views", json=body)
    assert r2.status_code == 409


def test_list_views(client):
    r = client.get("/api/saved-views")
    assert r.status_code == 200
    data = r.json()
    assert "views" in data
    assert isinstance(data["views"], list)


def test_list_views_filter_by_owner(client):
    import uuid
    owner = f"alice-{uuid.uuid4().hex[:8]}"
    name = f"v-{uuid.uuid4().hex[:8]}"
    client.post("/api/saved-views", json={"name": name, "expression": "", "owner": owner})
    r = client.get(f"/api/saved-views?owner={owner}")
    assert r.status_code == 200
    data = r.json()
    assert any(v["owner"] == owner for v in data["views"])


def test_get_view(client):
    import uuid
    cr = client.post("/api/saved-views", json={
        "name": f"getme-{uuid.uuid4().hex[:8]}",
        "expression": "y=z",
        "owner": f"tester-{uuid.uuid4().hex[:8]}",
    })
    assert cr.status_code == 200
    vid = cr.json()["id"]
    r = client.get(f"/api/saved-views/{vid}")
    assert r.status_code == 200
    assert r.json()["id"] == vid


def test_get_missing_404(client):
    r = client.get("/api/saved-views/does-not-exist")
    assert r.status_code == 404


def test_update_view(client):
    import uuid
    cr = client.post("/api/saved-views", json={
        "name": f"upd-{uuid.uuid4().hex[:8]}",
        "expression": "old",
        "owner": f"u-{uuid.uuid4().hex[:8]}",
    })
    assert cr.status_code == 200
    vid = cr.json()["id"]
    r = client.put(f"/api/saved-views/{vid}", json={
        "name": f"upd-{uuid.uuid4().hex[:8]}",
        "expression": "new",
        "owner": f"u-{uuid.uuid4().hex[:8]}",
    })
    assert r.status_code == 200
    assert r.json()["expression"] == "new"


def test_update_missing_404(client):
    r = client.put("/api/saved-views/missing", json={"name": "x", "expression": "y", "owner": "z"})
    assert r.status_code == 404


def test_delete_view(client):
    import uuid
    cr = client.post("/api/saved-views", json={
        "name": f"del-{uuid.uuid4().hex[:8]}",
        "expression": "",
        "owner": f"d-{uuid.uuid4().hex[:8]}",
    })
    assert cr.status_code == 200
    vid = cr.json()["id"]
    r = client.delete(f"/api/saved-views/{vid}")
    assert r.status_code == 200
    assert r.json()["deleted"] == vid
    # Confirm gone.
    r2 = client.get(f"/api/saved-views/{vid}")
    assert r2.status_code == 404


def test_delete_missing_404(client):
    r = client.delete("/api/saved-views/missing")
    assert r.status_code == 404
