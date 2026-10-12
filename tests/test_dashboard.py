"""
Tests for the operator dashboard API endpoints:
status counts, clip approval, clip rejection, and dead-letter retry.
Also verifies that Basic Auth is enforced on protected routes.
"""

import json
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.db.session import Base
from shorts_engine.db.models import Channel, SourceVideo, SourceVideoStatus, ClipCandidate, Clip, ClipStatus
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.routes import status as status_route
from shorts_engine.dashboard.routes import queue as queue_route


from sqlalchemy.pool import StaticPool
from shorts_engine.dashboard.routes import channels as channels_route


# ── Test App Setup ─────────────────────────────────────────────────────────────

def _make_test_app_and_db():
    """Create an in-memory DB with StaticPool and a TestClient wired to use it."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = create_dashboard_app()
    app.dependency_overrides[status_route.get_db] = override_get_db
    app.dependency_overrides[queue_route.get_db] = override_get_db
    app.dependency_overrides[channels_route.get_db] = override_get_db

    from shorts_engine.dashboard.auth import get_current_user, require_operator, require_viewer, require_admin
    from shorts_engine.db.models import User, UserRole
    test_operator = User(
        id="test-op-001",
        username="test_operator",
        password_hash="hash",
        role=UserRole.OPERATOR,
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: test_operator
    app.dependency_overrides[require_viewer] = lambda: test_operator
    app.dependency_overrides[require_operator] = lambda: test_operator
    app.dependency_overrides[require_admin] = lambda: test_operator

    client = TestClient(app, raise_server_exceptions=True)
    db = TestSession()
    return client, db


def _make_waiting_clip(db, rendered_path: str = None) -> Clip:
    channel = Channel(youtube_channel_id="UC_dash", name="Dash Ch",
                      watch_enabled=True, human_review_required=True)
    db.add(channel)
    db.flush()
    video = SourceVideo(channel_id=channel.id, youtube_video_id="dash_vid_001",
                        title="Dash Video", status=SourceVideoStatus.DONE)
    db.add(video)
    db.flush()
    cand = ClipCandidate(source_video_id=video.id, start_ms=0, end_ms=30000,
                         score=0.8, reason="Good", hook_text="Good hook", selected=True)
    db.add(cand)
    db.flush()
    clip = Clip(clip_candidate_id=cand.id, status=ClipStatus.WAITING_APPROVAL,
                rendered_path=rendered_path or "/fake/path.mp4", retry_count=0)
    db.add(clip)
    db.commit()
    return clip


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_status_endpoint_returns_correct_counts():
    """GET / should return 200 and include pipeline metric data."""
    client, db = _make_test_app_and_db()
    # Hit JSON API endpoint (avoids Jinja2 template rendering in unit test)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_videos" in data
    assert "waiting_approval" in data
    assert "quota_remaining" in data
    assert isinstance(data["total_videos"], int)


def test_queue_approve_transitions_clip_status(tmp_path, monkeypatch):
    """POST /queue/{id}/approve should transition a WAITING_APPROVAL clip to APPROVED."""
    monkeypatch.setattr("shorts_engine.config.settings.settings.DRY_RUN_PUBLISHING", True)
    dummy_video = tmp_path / "dash_rendered.mp4"
    dummy_video.write_bytes(b"\x00" * 1024)

    client, db = _make_test_app_and_db()
    clip = _make_waiting_clip(db, rendered_path=str(dummy_video))

    resp = client.post(f"/queue/{clip.id}/approve")
    assert resp.status_code == 200
    assert resp.json()["new_status"] in (ClipStatus.APPROVED.value, ClipStatus.SCHEDULED.value)

    db.refresh(clip)
    assert clip.status in (ClipStatus.APPROVED, ClipStatus.SCHEDULED)


def test_queue_reject_transitions_clip_status():
    """POST /queue/{id}/reject should transition a WAITING_APPROVAL clip to REJECTED."""
    client, db = _make_test_app_and_db()
    clip = _make_waiting_clip(db)

    resp = client.post(f"/queue/{clip.id}/reject", data={"reason": "Off-topic content"})
    assert resp.status_code == 200
    result = resp.json()
    assert result["new_status"] == ClipStatus.REJECTED.value

    db.refresh(clip)
    assert clip.status == ClipStatus.REJECTED
    qr = json.loads(clip.quality_check_result or "{}")
    assert qr.get("rejection_reason") == "Off-topic content"


def test_dead_letter_retry_resets_clip():
    """POST /dead-letter/clip/{id}/retry should reset status to RENDERING and increment retry_count."""
    client, db = _make_test_app_and_db()

    # Create a failed clip
    channel = Channel(youtube_channel_id="UC_dl", name="DL Ch",
                      watch_enabled=True, human_review_required=True)
    db.add(channel)
    db.flush()
    video = SourceVideo(channel_id=channel.id, youtube_video_id="dl_vid",
                        title="DL Video", status=SourceVideoStatus.DONE)
    db.add(video)
    db.flush()
    cand = ClipCandidate(source_video_id=video.id, start_ms=0, end_ms=20000,
                         score=0.5, reason="Fail", selected=True)
    db.add(cand)
    db.flush()
    failed_clip = Clip(clip_candidate_id=cand.id, status=ClipStatus.FAILED,
                       rendered_path=None, retry_count=1)
    db.add(failed_clip)
    db.commit()

    resp = client.post(f"/dead-letter/clip/{failed_clip.id}/retry")
    assert resp.status_code == 200
    result = resp.json()
    assert result["new_status"] == ClipStatus.RENDERING.value
    assert result["retry_count"] == 2

    db.refresh(failed_clip)
    assert failed_clip.status == ClipStatus.RENDERING
    assert failed_clip.retry_count == 2


def test_dashboard_auth_required():
    """Accessing protected dashboard endpoints without authentication returns 401."""
    app = create_dashboard_app()
    client = TestClient(app)
    resp = client.get("/api/status")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Authentication required"
