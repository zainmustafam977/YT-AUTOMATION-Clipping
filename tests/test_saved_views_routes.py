"""Tests for EASE-02 saved views HTTP routes.

Every saved-view endpoint requires authentication, so these tests drive
operator-authenticated clients: a session cookie plus a matching CSRF token for
the mutating calls. Ownership is derived from the session, so the suite also
covers that one user cannot see, rename or delete another user's views.

Anonymous access is covered separately by ``tests/test_p0_security_hardening.py``.
"""
import uuid

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

OWNER_A = "saved_views_test_operator"
OWNER_B = "saved_views_test_operator_b"


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    Base.metadata.create_all(engine)
    yield


def _token_for(username: str) -> str:
    db = SessionLocal()
    user = db.query(User).filter_by(username=username).first()
    if not user:
        user = User(
            username=username,
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


def _client(token: str) -> TestClient:
    c = TestClient(app, headers={"X-CSRF-Token": generate_csrf_token(token)})
    c.cookies.set(SESSION_COOKIE_NAME, token)
    return c


@pytest.fixture(scope="module")
def auth_token():
    """Session token for an operator account; saved-view routes require auth."""
    return _token_for(OWNER_A)


@pytest.fixture
def client(auth_token):
    return _client(auth_token)


@pytest.fixture(scope="module")
def other_client():
    """An authenticated operator who owns none of the first user's views."""
    return _client(_token_for(OWNER_B))


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def test_create_saved_view(client):
    name = _unique("ready-clips")
    r = client.post("/api/saved-views", json={
        "name": name,
        "expression": "status=ready",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["name"] == name
    assert "id" in data
    # Owner comes from the session, not the payload.
    assert data["owner"] == OWNER_A


def test_create_ignores_client_supplied_owner(client):
    """A spoofed owner in the body must not influence ownership."""
    name = _unique("spoof")
    r = client.post("/api/saved-views", json={
        "name": name,
        "expression": "",
        "owner": "somebody-else",
    })
    assert r.status_code == 200, r.text
    assert r.json()["owner"] == OWNER_A

    listed = client.get("/api/saved-views").json()["views"]
    assert all(v["owner"] == OWNER_A for v in listed)


def test_create_duplicate_returns_409(client):
    name = _unique("dup")
    body = {"name": name, "expression": "x"}
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
    assert all(v["owner"] == OWNER_A for v in data["views"])


def test_list_views_scoped_to_caller(client, other_client):
    """Another user's view never appears in the caller's list."""
    shared_name = _unique("other-users-view")
    created = other_client.post("/api/saved-views", json={
        "name": shared_name,
        "expression": "status=failed",
    })
    assert created.status_code == 200, created.text

    mine = client.get("/api/saved-views").json()["views"]
    assert all(v["id"] != created.json()["id"] for v in mine)
    assert all(v["name"] != shared_name for v in mine)

    theirs = other_client.get("/api/saved-views").json()["views"]
    assert any(v["id"] == created.json()["id"] for v in theirs)


def test_list_rejects_foreign_owner_filter(client):
    r = client.get(f"/api/saved-views?owner={OWNER_B}")
    assert r.status_code == 403, r.text


def test_get_view(client):
    cr = client.post("/api/saved-views", json={
        "name": _unique("getme"),
        "expression": "y=z",
    })
    assert cr.status_code == 200
    vid = cr.json()["id"]
    r = client.get(f"/api/saved-views/{vid}")
    assert r.status_code == 200
    assert r.json()["id"] == vid


def test_get_missing_404(client):
    r = client.get("/api/saved-views/does-not-exist")
    assert r.status_code == 404


def test_get_other_users_view_404(client, other_client):
    """A view owned by someone else is reported as missing, not as forbidden."""
    created = other_client.post("/api/saved-views", json={
        "name": _unique("not-yours"),
        "expression": "",
    })
    assert created.status_code == 200, created.text
    vid = created.json()["id"]
    assert client.get(f"/api/saved-views/{vid}").status_code == 404


def test_update_view(client):
    cr = client.post("/api/saved-views", json={
        "name": _unique("upd"),
        "expression": "old",
    })
    assert cr.status_code == 200
    vid = cr.json()["id"]
    r = client.put(f"/api/saved-views/{vid}", json={
        "name": _unique("upd"),
        "expression": "new",
    })
    assert r.status_code == 200
    assert r.json()["expression"] == "new"
    assert r.json()["owner"] == OWNER_A


def test_update_missing_404(client):
    r = client.put("/api/saved-views/missing", json={"name": "x", "expression": "y"})
    assert r.status_code == 404


def test_update_other_users_view_404(client, other_client):
    created = other_client.post("/api/saved-views", json={
        "name": _unique("cant-edit"),
        "expression": "original",
    })
    assert created.status_code == 200, created.text
    vid = created.json()["id"]

    r = client.put(f"/api/saved-views/{vid}", json={"name": "hijacked", "expression": "x"})
    assert r.status_code == 404

    # And the original is untouched.
    still = other_client.get(f"/api/saved-views/{vid}")
    assert still.status_code == 200
    assert still.json()["expression"] == "original"


def test_delete_view(client):
    cr = client.post("/api/saved-views", json={
        "name": _unique("del"),
        "expression": "",
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


def test_delete_other_users_view_404(client, other_client):
    created = other_client.post("/api/saved-views", json={
        "name": _unique("cant-delete"),
        "expression": "",
    })
    assert created.status_code == 200, created.text
    vid = created.json()["id"]

    assert client.delete(f"/api/saved-views/{vid}").status_code == 404
    # Still there for its owner.
    assert other_client.get(f"/api/saved-views/{vid}").status_code == 200
