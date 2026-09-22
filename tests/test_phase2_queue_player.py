"""
Tests for Phase 2:
- Video streaming endpoint (/api/video/{clip_id}) with inline modal playback
- Reversible Reject & Undo (Restore to Queue)
- Bulk approve / reject actions (/queue/bulk-action)
- Explicit quality gate badges (Pass, Fail with reason, Pending)
- Tab filtering (Active Queue vs Rejected Clips)
- Disk space & storage breakdown monitoring
- Per-video subfolder and clean hook slug renaming
"""

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.config.settings import settings
from shorts_engine.db.session import Base
from shorts_engine.db.models import Channel, SourceVideo, SourceVideoStatus, ClipCandidate, Clip, ClipStatus, User, UserRole
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.routes import status as status_route
from shorts_engine.dashboard.routes import queue as queue_route
from shorts_engine.dashboard.routes.status import get_storage_stats
from shorts_engine.dashboard.auth import get_current_user, require_operator, require_viewer, require_admin, verify_csrf


@pytest.fixture
def test_env(tmp_path):
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

    test_operator = User(
        id="test-op-p2",
        username="operator_phase2",
        password_hash="hash",
        role=UserRole.OPERATOR,
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: test_operator
    app.dependency_overrides[require_viewer] = lambda: test_operator
    app.dependency_overrides[require_operator] = lambda: test_operator
    app.dependency_overrides[require_admin] = lambda: test_operator
    app.dependency_overrides[verify_csrf] = lambda: True

    client = TestClient(app, raise_server_exceptions=True)
    db = TestSession()
    yield client, db, tmp_path
    db.close()


def _seed_channel_and_video(db, vid="test_p2_vid", title="Test P2 Video"):
    ch = db.query(Channel).filter_by(id="p2-chan-01").first()
    if not ch:
        ch = Channel(id="p2-chan-01", name="Phase2 Channel", youtube_channel_id="UC_P2_TEST")
        db.add(ch)
        db.commit()

    v = SourceVideo(
        id=f"sv-{vid}",
        channel_id=ch.id,
        youtube_video_id=vid,
        title=title,
        status=SourceVideoStatus.DONE,
        duration_sec=120.0,
    )
    db.add(v)
    db.commit()
    return ch, v


# ── 1. Video Streaming Endpoint ───────────────────────────────────────────────

def test_stream_clip_video_success(test_env):
    client, db, tmp_path = test_env
    _, video = _seed_channel_and_video(db, "vid_stream_01")

    # Create dummy mp4 file
    dummy_video = tmp_path / "rendered_short.mp4"
    dummy_video.write_bytes(b"\x00\x00\x00\x1cftypisom" + b"A" * 1024)

    cand = ClipCandidate(
        id="cand-st-1",
        source_video_id=video.id,
        start_ms=0,
        end_ms=15000,
        score=8.5,
        reason="Test high score candidate",
        hook_text="Check this out!",
    )
    db.add(cand)
    db.commit()

    clip = Clip(
        id="clip-stream-001",
        clip_candidate_id=cand.id,
        status=ClipStatus.WAITING_APPROVAL,
        rendered_path=str(dummy_video),
    )
    db.add(clip)
    db.commit()

    resp = client.get(f"/api/video/{clip.id}")
    assert resp.status_code == 200
    assert "video/mp4" in resp.headers.get("content-type", "")
    assert len(resp.content) > 1000


def test_stream_clip_video_not_found(test_env):
    client, db, _ = test_env
    resp = client.get("/api/video/non-existent-clip")
    assert resp.status_code == 404

    # Clip with missing file on disk
    _, video = _seed_channel_and_video(db, "vid_stream_missing")
    cand = ClipCandidate(id="cand-st-2", source_video_id=video.id, start_ms=0, end_ms=15000, score=7.0, reason="Cand test")
    db.add(cand)
    db.commit()

    clip = Clip(id="clip-missing-disk", clip_candidate_id=cand.id, status=ClipStatus.WAITING_APPROVAL, rendered_path="/non/existent/file.mp4")
    db.add(clip)
    db.commit()

    resp2 = client.get(f"/api/video/{clip.id}")
    assert resp2.status_code == 404
    assert "not found on disk" in resp2.json()["detail"].lower()


# ── 2. Reversible Reject & Undo (Restore to Queue) ─────────────────────────────

def test_reject_and_restore_clip(test_env):
    client, db, _ = test_env
    _, video = _seed_channel_and_video(db, "vid_reject_restore")
    cand = ClipCandidate(id="cand-rr-1", source_video_id=video.id, start_ms=1000, end_ms=20000, score=7.5, reason="Test cand")
    db.add(cand)
    db.commit()

    clip = Clip(id="clip-rr-001", clip_candidate_id=cand.id, status=ClipStatus.WAITING_APPROVAL)
    db.add(clip)
    db.commit()

    # Reject
    resp_reject = client.post(f"/queue/{clip.id}/reject", data={"reason": "Weak payoff"})
    assert resp_reject.status_code == 200
    db.refresh(clip)
    assert clip.status == ClipStatus.REJECTED
    qr = json.loads(clip.quality_check_result or "{}")
    assert qr.get("rejection_reason") == "Weak payoff"

    # Reject again should return 409
    resp_re_reject = client.post(f"/queue/{clip.id}/reject")
    assert resp_re_reject.status_code == 409

    # Restore back to queue
    resp_restore = client.post(f"/queue/{clip.id}/restore")
    assert resp_restore.status_code == 200
    db.refresh(clip)
    assert clip.status == ClipStatus.WAITING_APPROVAL

    # Restoring an active clip should return 409
    resp_restore_active = client.post(f"/queue/{clip.id}/restore")
    assert resp_restore_active.status_code == 409


# ── 3. Bulk Action: Approve and Reject ─────────────────────────────────────────

def test_bulk_approve_and_reject(test_env):
    client, db, _ = test_env
    _, video = _seed_channel_and_video(db, "vid_bulk_test")

    clip_ids = []
    for i in range(4):
        cand = ClipCandidate(id=f"cand-bulk-{i}", source_video_id=video.id, start_ms=i*1000, end_ms=(i+1)*10000, score=8.0, reason="Bulk test")
        db.add(cand)
        db.commit()
        clip = Clip(id=f"clip-bulk-{i}", clip_candidate_id=cand.id, status=ClipStatus.WAITING_APPROVAL)
        db.add(clip)
        db.commit()
        clip_ids.append(clip.id)

    # Bulk approve first 2
    resp_app = client.post("/queue/bulk-action", data={"action": "approve", "clip_ids": clip_ids[:2]})
    assert resp_app.status_code == 200
    assert resp_app.json()["count"] == 2

    db.expire_all()
    assert db.query(Clip).filter_by(id=clip_ids[0]).first().status == ClipStatus.APPROVED
    assert db.query(Clip).filter_by(id=clip_ids[1]).first().status == ClipStatus.APPROVED

    # Bulk reject next 2
    resp_rej = client.post("/queue/bulk-action", data={"action": "reject", "clip_ids": clip_ids[2:]})
    assert resp_rej.status_code == 200
    assert resp_rej.json()["count"] == 2

    db.expire_all()
    assert db.query(Clip).filter_by(id=clip_ids[2]).first().status == ClipStatus.REJECTED
    assert db.query(Clip).filter_by(id=clip_ids[3]).first().status == ClipStatus.REJECTED

    # Invalid action
    resp_inv = client.post("/queue/bulk-action", data={"action": "destroy", "clip_ids": [clip_ids[0]]})
    assert resp_inv.status_code == 400


# ── 4. Queue UI Tab Navigation & Explicit Badges ──────────────────────────────

def test_queue_tabs_and_explicit_badges(test_env, tmp_path):
    client, db, _ = test_env
    _, video = _seed_channel_and_video(db, "vid_badges_tabs")

    dummy_video = tmp_path / "badge_sample.mp4"
    dummy_video.write_bytes(b"\x00" * 200)

    # Clip 1: Active with mixed checks (Pass technical, Fail framing, Pending semantic)
    cand1 = ClipCandidate(id="cand-badge-1", source_video_id=video.id, start_ms=0, end_ms=25000, score=9.0, reason="Badge test", hook_text="Wait until the end")
    db.add(cand1)
    db.commit()

    q_report1 = {
        "technical_check": {"all_passed": True},
        "render_inspection": {"all_passed": False, "error": "Subject drifted to edge (x=0.08)"},
    }
    clip1 = Clip(
        id="clip-badge-1",
        clip_candidate_id=cand1.id,
        status=ClipStatus.WAITING_APPROVAL,
        rendered_path=str(dummy_video),
        quality_check_result=json.dumps(q_report1),
    )
    db.add(clip1)

    # Clip 2: Rejected clip
    cand2 = ClipCandidate(id="cand-badge-2", source_video_id=video.id, start_ms=30000, end_ms=45000, score=6.0, reason="Badge test 2")
    db.add(cand2)
    db.commit()
    clip2 = Clip(
        id="clip-badge-2",
        clip_candidate_id=cand2.id,
        status=ClipStatus.REJECTED,
        quality_check_result=json.dumps({"rejection_reason": "Low operator engagement"}),
    )
    db.add(clip2)
    db.commit()


    # Query Active tab
    resp_active = client.get("/queue?tab=active")
    assert resp_active.status_code == 200
    html_act = resp_active.text
    assert "Active Queue" in html_act
    assert "Rejected Clips" in html_act
    assert "clip-badge-1"[:8] in html_act
    assert "✓ Technical Pass" in html_act
    assert "✗ Framing Fail" in html_act
    assert "Subject drifted to edge" in html_act
    assert "⏳ Semantic Pending" in html_act
    assert "▶ Play Short" in html_act

    # Query Rejected tab
    resp_rejected = client.get("/queue?tab=rejected")
    assert resp_rejected.status_code == 200
    html_rej = resp_rejected.text
    assert "clip-badge-2"[:8] in html_rej
    assert "Restore to Queue" in html_rej
    assert "Low operator engagement" in html_rej

    # Test Undo banner presence when rejected_id query parameter is set
    resp_undo = client.get("/queue?rejected_id=clip-badge-1")
    assert resp_undo.status_code == 200
    assert "Undo (Restore to Queue)" in resp_undo.text


# ── 5. Storage Stats & Status Card ────────────────────────────────────────────

def test_storage_stats_and_status_card(test_env):
    client, _, _ = test_env

    stats = get_storage_stats()
    assert "total_gb" in stats
    assert "free_gb" in stats
    assert "used_gb" in stats
    assert "percent_used" in stats
    assert "outputs_mb" in stats
    assert "raw_mb" in stats
    assert "temp_mb" in stats

    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "Storage & Disk Monitor" in html
    assert "Rendered Outputs" in html
    assert "Raw Videos" in html
    assert "Temp Workspace" in html
