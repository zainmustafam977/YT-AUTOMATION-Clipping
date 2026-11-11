"""Tests for E-001..E-010 batch HTTP CSRF and auth guards (AUD-008).

Batch mutating endpoints (/preview, /run, /{batch_id}/undo) must reject:
- anonymous callers (401)
- cookie-only authenticated callers without a valid CSRF token (403)
"""

import pytest

from fastapi.testclient import TestClient

from shorts_engine.db.session import engine, SessionLocal, Base
from shorts_engine.db.models import User, UserRole
from shorts_engine.dashboard.app import app
from shorts_engine.dashboard.auth import (
    create_session_token,
    generate_csrf_token,
    SESSION_COOKIE_NAME,
)


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    Base.metadata.create_all(engine)
    yield


def _ensure_user(username: str) -> str:
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


@pytest.fixture
def operator_token():
    return _ensure_user("batch_csrf_operator")


def test_batch_run_requires_authentication():
    anon = TestClient(app)
    assert anon.post("/api/batch/run", json={"operation": "approve", "item_ids": []}).status_code == 401


def test_batch_preview_requires_authentication():
    anon = TestClient(app)
    assert anon.post("/api/batch/preview", json={"operation": "approve", "item_ids": []}).status_code == 401


def test_batch_undo_requires_authentication():
    anon = TestClient(app)
    assert anon.post("/api/batch/does-not-exist/undo", json={"item_ids": []}).status_code == 401


def test_batch_mutations_require_csrf(operator_token):
    """A valid session cookie alone must not authorize the three mutating batch endpoints."""
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE_NAME, operator_token)
    # The payloads must be schema-valid: an empty item_ids list fails request
    # validation (422) before the CSRF dependency runs, which would test the
    # wrong layer. With a valid body only the missing CSRF token can explain 403.
    body = {"operation": "approve", "item_ids": ["clip-csrf-probe"]}
    assert c.post("/api/batch/preview", json=body).status_code == 403
    assert c.post("/api/batch/run", json=body).status_code == 403
    assert c.post(
        "/api/batch/does-not-exist/undo",
        json={"item_ids": ["clip-csrf-probe"]},
    ).status_code == 403
