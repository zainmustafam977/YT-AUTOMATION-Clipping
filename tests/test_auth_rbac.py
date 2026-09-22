"""
Unit and integration tests for Multi-User RBAC, Session Auth, and CSRF Protection.
"""

import time
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.db.models import User, UserRole, Clip, ClipStatus, Channel, SourceVideo, ClipCandidate
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.auth import (
    hash_password,
    verify_password,
    create_session_token,
    verify_session_token,
    generate_csrf_token,
    verify_csrf_token,
    SESSION_COOKIE_NAME,
)
from shorts_engine.dashboard.routes import auth as auth_route
from shorts_engine.dashboard.routes import queue as queue_route
from shorts_engine.dashboard.routes import status as status_route
from shorts_engine.dashboard.routes import channels as channels_route


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def auth_client(test_db):
    app = create_dashboard_app()

    def override_get_db():
        yield test_db

    app.dependency_overrides[auth_route.get_db] = override_get_db
    app.dependency_overrides[queue_route.get_db] = override_get_db
    app.dependency_overrides[status_route.get_db] = override_get_db
    app.dependency_overrides[channels_route.get_db] = override_get_db

    client = TestClient(app, raise_server_exceptions=True)
    return client


# ── Password Hashing Tests ────────────────────────────────────────────────

def test_password_hashing_roundtrip():
    raw = "SuperSecretPassword123!"
    hashed = hash_password(raw)
    assert hashed.startswith("pbkdf2_sha256$600000$")
    assert verify_password(raw, hashed) is True
    assert verify_password("WrongPassword!", hashed) is False


def test_password_hash_uniqueness():
    raw = "SamePasswordAcrossUsers"
    hash1 = hash_password(raw)
    hash2 = hash_password(raw)
    assert hash1 != hash2  # Unique random salt per hash
    assert verify_password(raw, hash1) is True
    assert verify_password(raw, hash2) is True


# ── Session Token & Signature Tests ───────────────────────────────────────

def test_session_token_roundtrip():
    user_id = "user-abc-123"
    token = create_session_token(user_id)
    recovered_id = verify_session_token(token)
    assert recovered_id == user_id


def test_session_token_tampering_rejected():
    token = create_session_token("user-legit")
    parts = token.split(".")
    tampered = f"user-hacked.{parts[1]}.{parts[2]}"
    assert verify_session_token(tampered) is None


def test_session_token_expired_rejected():
    user_id = "user-expired"
    past_timestamp = int(time.time()) - 1000
    token = create_session_token(user_id, timestamp=past_timestamp)
    # Verification with max_age of 60s should fail
    assert verify_session_token(token, max_age_secs=60) is None


# ── CSRF Token Tests ──────────────────────────────────────────────────────

def test_csrf_token_verification():
    session_token = create_session_token("user-csrf-test")
    csrf_token = generate_csrf_token(session_token)
    assert verify_csrf_token(session_token, csrf_token) is True
    assert verify_csrf_token(session_token, "invalid_csrf_token") is False
    assert verify_csrf_token("different_session_token", csrf_token) is False


def test_csrf_protection_on_state_changing_post(auth_client, test_db):
    """A browser with a valid session cookie must be blocked (403) if CSRF token is absent."""
    admin = User(
        username="admin_csrf",
        password_hash=hash_password("adminpassword123"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    test_db.add(admin)
    test_db.commit()

    token = create_session_token(admin.id)
    auth_client.cookies.set(SESSION_COOKIE_NAME, token)

    # Attempt POST without CSRF token
    resp_no_csrf = auth_client.post("/api/users", json={
        "username": "new_user_fail",
        "password": "validpassword123",
        "role": "operator"
    })
    assert resp_no_csrf.status_code == 403
    assert "CSRF" in resp_no_csrf.json()["detail"]

    # Provide valid CSRF token in header -> should succeed
    csrf = generate_csrf_token(token)
    resp_with_csrf = auth_client.post(
        "/api/users",
        json={
            "username": "new_user_success",
            "password": "validpassword123",
            "role": "operator"
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_with_csrf.status_code == 200
    assert resp_with_csrf.json()["created"] is True


# ── RBAC Endpoint Tests ───────────────────────────────────────────────────

def test_rbac_admin_can_manage_users(auth_client, test_db):
    admin = User(
        username="superadmin",
        password_hash=hash_password("supersecretadmin123"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    test_db.add(admin)
    test_db.commit()

    token = create_session_token(admin.id)
    auth_client.cookies.set(SESSION_COOKIE_NAME, token)
    csrf = generate_csrf_token(token)

    # 1. Create operator user
    create_resp = auth_client.post(
        "/api/users",
        json={"username": "operator1", "password": "operatorpassword123", "role": "operator"},
        headers={"X-CSRF-Token": csrf},
    )
    assert create_resp.status_code == 200
    new_user_id = create_resp.json()["id"]

    # 2. List users
    list_resp = auth_client.get("/api/users")
    assert list_resp.status_code == 200
    usernames = [u["username"] for u in list_resp.json()]
    assert "superadmin" in usernames
    assert "operator1" in usernames

    # 3. Delete user
    del_resp = auth_client.delete(f"/api/users/{new_user_id}", headers={"X-CSRF-Token": csrf})
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True


def test_rbac_operator_restricted_from_user_management(auth_client, test_db):
    operator = User(
        username="op_user",
        password_hash=hash_password("oppassword1234"),
        role=UserRole.OPERATOR,
        is_active=True,
    )
    test_db.add(operator)
    test_db.commit()

    token = create_session_token(operator.id)
    auth_client.cookies.set(SESSION_COOKIE_NAME, token)
    csrf = generate_csrf_token(token)

    # Operator trying to access /api/users must get 403
    list_resp = auth_client.get("/api/users")
    assert list_resp.status_code == 403

    # Operator trying to create user must get 403
    create_resp = auth_client.post(
        "/api/users",
        json={"username": "rogue", "password": "roguepassword123", "role": "admin"},
        headers={"X-CSRF-Token": csrf},
    )
    assert create_resp.status_code == 403


def test_login_and_logout_flow(auth_client, test_db):
    user = User(
        username="login_test_user",
        password_hash=hash_password("correctpassword123"),
        role=UserRole.OPERATOR,
        is_active=True,
    )
    test_db.add(user)
    test_db.commit()

    # Wrong password -> 401
    bad_resp = auth_client.post("/login", data={"username": "login_test_user", "password": "wrong"})
    assert bad_resp.status_code == 401

    # Correct password -> 200 + session cookie
    good_resp = auth_client.post("/login", data={"username": "login_test_user", "password": "correctpassword123"})
    assert good_resp.status_code == 200
    assert SESSION_COOKIE_NAME in good_resp.cookies

    # Inspect /api/me
    auth_client.cookies.set(SESSION_COOKIE_NAME, good_resp.cookies[SESSION_COOKIE_NAME])
    me_resp = auth_client.get("/api/me")
    assert me_resp.status_code == 200
    assert me_resp.json()["username"] == "login_test_user"
    assert me_resp.json()["role"] == "operator"

    # Logout (API caller)
    logout_resp = auth_client.post("/logout")
    assert logout_resp.status_code == 200
    assert logout_resp.json() == {"status": "logged_out"}


def test_browser_logout_redirects_to_login(auth_client, test_db):
    """Verifies that opening /logout in a web browser redirects to /login with 303."""
    user = User(
        username="browser_logout_user",
        password_hash=hash_password("validpassword123"),
        role=UserRole.VIEWER,
        is_active=True,
    )
    test_db.add(user)
    test_db.commit()

    token = create_session_token(user.id)
    auth_client.cookies.set(SESSION_COOKIE_NAME, token)

    # GET /logout from browser
    resp = auth_client.get("/logout", headers={"Accept": "text/html", "Sec-Fetch-Dest": "document"}, follow_redirects=False)
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]
    assert "logged+out" in resp.headers["location"]
    # Session cookie must be cleared
    assert SESSION_COOKIE_NAME not in resp.cookies or resp.cookies[SESSION_COOKIE_NAME] == ""

    # POST /logout from browser form
    auth_client.cookies.set(SESSION_COOKIE_NAME, token)
    resp2 = auth_client.post("/logout", headers={"Accept": "text/html", "Sec-Fetch-Dest": "document"}, follow_redirects=False)
    assert resp2.status_code == 303
    assert "/login" in resp2.headers["location"]


def test_basic_auth_header_rejected(auth_client, test_db):
    """Verifies that legacy HTTP Basic Auth headers are completely rejected (401)."""
    import base64
    legacy_header = "Basic " + base64.b64encode(b"admin:change-me-in-prod").decode("utf-8")
    resp = auth_client.get("/api/status", headers={"Authorization": legacy_header})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Authentication required"


def test_viewer_restricted_from_approve_queue(auth_client, test_db):
    """Verifies that a user with VIEWER role is restricted from approve/reject actions (403)."""
    viewer = User(
        username="read_only_viewer",
        password_hash=hash_password("viewerpassword123"),
        role=UserRole.VIEWER,
        is_active=True,
    )
    test_db.add(viewer)
    test_db.commit()

    token = create_session_token(viewer.id)
    auth_client.cookies.set(SESSION_COOKIE_NAME, token)
    csrf = generate_csrf_token(token)

    # Viewer can access read-only status
    status_resp = auth_client.get("/api/status")
    assert status_resp.status_code == 200

    # Viewer CANNOT approve a clip (requires OPERATOR or ADMIN)
    approve_resp = auth_client.post(
        "/queue/fake-clip-id/approve",
        headers={"X-CSRF-Token": csrf},
    )
    assert approve_resp.status_code == 403
    assert "viewer" in approve_resp.json()["detail"].lower()


def test_bearer_token_authentication(auth_client, test_db):
    """Verifies that non-browser API scripts can authenticate using Authorization: Bearer <session_token>."""
    operator = User(
        username="script_operator",
        password_hash=hash_password("operatorpass123"),
        role=UserRole.OPERATOR,
        is_active=True,
    )
    test_db.add(operator)
    test_db.commit()

    token = create_session_token(operator.id)
    resp = auth_client.get("/api/status", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_cli_create_user_admin_bootstrap():
    """Verifies that 'python run.py create-user' bootstraps the admin account into the database."""
    from typer.testing import CliRunner
    from cli import app
    from shorts_engine.db.session import SessionLocal
    from shorts_engine.db.models import User, UserRole
    from shorts_engine.dashboard.auth import verify_password

    runner = CliRunner()

    # 1. Reject passwords under 12 characters
    short_res = runner.invoke(app, [
        "create-user",
        "--username", "short_admin",
        "--password", "shortpass",
        "--role", "admin",
    ])
    assert short_res.exit_code == 1
    assert "Password must be at least 12 characters" in short_res.stdout

    # 2. Successfully bootstrap admin user
    success_res = runner.invoke(app, [
        "create-user",
        "--username", "bootstrap_admin_suite",
        "--password", "StrongBootstrapPass123!",
        "--role", "admin",
    ])
    assert success_res.exit_code == 0
    assert "saved successfully" in success_res.stdout

    # 3. Confirm database record, PBKDF2 hash, and ADMIN role
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username="bootstrap_admin_suite").first()
        assert user is not None
        assert user.role == UserRole.ADMIN
        assert user.is_active is True
        assert verify_password("StrongBootstrapPass123!", user.password_hash) is True
    finally:
        db.close()


def test_browser_form_csrf_and_redirects(auth_client, test_db):
    """Verifies that templates embed CSRF tokens and browser POST forms validate CSRF and redirect with 303."""
    from shorts_engine.db.models import SourceVideoStatus

    operator = User(
        username="op_browser_tester",
        password_hash=hash_password("operatorsecret123"),
        role=UserRole.OPERATOR,
        is_active=True,
    )
    test_db.add(operator)
    test_db.commit()

    channel = Channel(
        youtube_channel_id="UC_browser_test",
        name="Browser Test Channel",
        watch_enabled=True,
        human_review_required=True,
    )
    test_db.add(channel)
    test_db.commit()

    video = SourceVideo(
        channel_id=channel.id,
        youtube_video_id="browser_vid_1",
        title="Browser Video",
        status=SourceVideoStatus.DONE,
    )
    test_db.add(video)
    test_db.commit()

    cand = ClipCandidate(
        source_video_id=video.id,
        start_ms=1000,
        end_ms=25000,
        score=0.9,
        reason="Good hook",
        hook_text="Hook",
        selected=True,
    )
    test_db.add(cand)
    test_db.commit()

    clip = Clip(
        clip_candidate_id=cand.id,
        status=ClipStatus.WAITING_APPROVAL,
        rendered_path="/test/browser_vid_1_short_1000_25000.mp4",
    )
    test_db.add(clip)
    test_db.commit()

    token = create_session_token(operator.id)
    auth_client.cookies.set(SESSION_COOKIE_NAME, token)
    csrf = generate_csrf_token(token)

    # 1. HTML pages include meta tag and hidden form field with csrf_token
    q_resp = auth_client.get("/queue")
    assert q_resp.status_code == 200
    assert f'<meta name="csrf-token" content="{csrf}">' in q_resp.text
    assert f'<input type="hidden" name="csrf_token" value="{csrf}">' in q_resp.text

    ch_resp = auth_client.get("/channels")
    assert ch_resp.status_code == 200
    assert f'<meta name="csrf-token" content="{csrf}">' in ch_resp.text
    assert f'<input type="hidden" name="csrf_token" value="{csrf}">' in ch_resp.text

    dl_resp = auth_client.get("/dead-letter")
    assert dl_resp.status_code == 200
    assert f'<meta name="csrf-token" content="{csrf}">' in dl_resp.text

    # 2. Browser POST without CSRF token fails with 403
    post_no_csrf = auth_client.post(f"/queue/{clip.id}/approve")
    assert post_no_csrf.status_code == 403
    assert "CSRF" in post_no_csrf.json()["detail"]

    # 3. Browser form POST with CSRF token succeeds and redirects (HTTP 303 -> /queue)
    post_with_csrf = auth_client.post(
        f"/queue/{clip.id}/approve",
        data={"csrf_token": csrf},
        headers={"Sec-Fetch-Dest": "document"},
        follow_redirects=False,
    )
    assert post_with_csrf.status_code == 303
    assert post_with_csrf.headers["location"].startswith("/queue")
    test_db.refresh(clip)
    assert clip.status == ClipStatus.APPROVED

    # 4. Browser form POST to channel toggle-watch redirects (HTTP 303 -> /channels)
    watch_resp = auth_client.post(
        f"/channels/{channel.id}/toggle-watch",
        data={"csrf_token": csrf},
        headers={"Sec-Fetch-Dest": "document"},
        follow_redirects=False,
    )
    assert watch_resp.status_code == 303
    assert watch_resp.headers["location"] == "/channels"
    test_db.refresh(channel)
    assert channel.watch_enabled is False

    # 5. Browser form POST to channel toggle-review redirects (HTTP 303 -> /channels)
    review_resp = auth_client.post(
        f"/channels/{channel.id}/toggle-review",
        data={"csrf_token": csrf},
        headers={"Sec-Fetch-Dest": "document"},
        follow_redirects=False,
    )
    assert review_resp.status_code == 303
    assert review_resp.headers["location"] == "/channels"
    test_db.refresh(channel)
    assert channel.human_review_required is False


def test_admin_users_html_rbac_and_crud(auth_client, test_db):
    # 1. Operator is rejected from /admin/users with 403
    op = User(
        username="op_user_2",
        password_hash=hash_password("oppassword1234"),
        role=UserRole.OPERATOR,
        is_active=True,
    )
    test_db.add(op)
    test_db.commit()

    op_token = create_session_token(op.id)
    auth_client.cookies.set(SESSION_COOKIE_NAME, op_token)
    op_resp = auth_client.get("/admin/users")
    assert op_resp.status_code == 403

    # 2. Admin can view /admin/users HTML page
    admin = User(
        username="admin_user_2",
        password_hash=hash_password("adminpassword1234"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    test_db.add(admin)
    test_db.commit()

    admin_token = create_session_token(admin.id)
    auth_client.cookies.set(SESSION_COOKIE_NAME, admin_token)
    admin_resp = auth_client.get("/admin/users")
    assert admin_resp.status_code == 200
    assert "System Users" in admin_resp.text
    assert "Create New User" in admin_resp.text

    # 3. Admin can create a new user via HTML form
    admin_csrf = generate_csrf_token(admin_token)
    create_resp = auth_client.post(
        "/admin/users",
        data={
            "username": "new_viewer",
            "password": "viewerpassword123",
            "role": "viewer",
            "csrf_token": admin_csrf,
        },
        follow_redirects=False,
    )
    assert create_resp.status_code == 303
    assert "/admin/users" in create_resp.headers["location"]

    created_user = test_db.query(User).filter_by(username="new_viewer").first()
    assert created_user is not None
    assert created_user.role == UserRole.VIEWER

    # 4. Admin cannot delete self
    self_del = auth_client.post(
        f"/admin/users/{admin.id}/delete",
        data={"csrf_token": admin_csrf},
        follow_redirects=False,
    )
    assert self_del.status_code == 303
    assert "Cannot+delete+your+own+admin+account" in self_del.headers["location"]

    # 5. Admin can delete another user
    del_resp = auth_client.post(
        f"/admin/users/{created_user.id}/delete",
        data={"csrf_token": admin_csrf},
        follow_redirects=False,
    )
    assert del_resp.status_code == 303
    assert test_db.query(User).filter_by(id=created_user.id).first() is None


def test_no_cache_headers_on_html_responses(auth_client, test_db):
    resp = auth_client.get("/login")
    assert resp.status_code == 200
    assert "no-store" in resp.headers.get("cache-control", "")
    assert "no-cache" in resp.headers.get("pragma", "")


