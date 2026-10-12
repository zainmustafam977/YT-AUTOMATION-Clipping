"""
Comprehensive tests for human review queue improvements:
- Isolated approval service (approve_clip_for_publishing, bulk_approve_clips)
- Race-safety / status-guard arbitration (double-approve prevention)
- Failure rollback to FAILED_NEEDS_REVIEW and slot release
- Bulk approve allocates slots and enqueues background jobs (never publishes inline)
- Per-clip structured outcomes in bulk actions
- Rejection reason codes, notes, and dual-write
- Global queue depth and oldest item age metrics
- Source video filtering
- HTTP Range requests for video seeking (206 Partial Content)
- Mocked boundaries so tests never touch external network
"""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    Channel,
    SourceVideo,
    SourceVideoStatus,
    ClipCandidate,
    Clip,
    ClipStatus,
    User,
    UserRole,
)
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.routes import status as status_route
from shorts_engine.dashboard.routes import queue as queue_route
from shorts_engine.dashboard.auth import (
    get_current_user,
    require_operator,
    require_viewer,
    require_admin,
    verify_csrf,
)
from shorts_engine.services.publishing.actions import (
    approve_clip_for_publishing,
    bulk_approve_clips,
    reject_clip_action,
    bulk_reject_clips,
)


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
        id="test-op-review",
        username="operator_reviewer",
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
    yield client, db, tmp_path, TestSession
    db.close()


def _seed_channel_and_video(db, vid="test_rq_vid", title="Test RQ Video"):
    ch = db.query(Channel).filter_by(id="rq-chan-01").first()
    if not ch:
        ch = Channel(id="rq-chan-01", name="Review Channel", youtube_channel_id="UC_RQ_TEST")
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


def _seed_clip(db, video, clip_id, status=ClipStatus.WAITING_APPROVAL, queued_delta=None):
    cand = ClipCandidate(
        id=f"cand-{clip_id}",
        source_video_id=video.id,
        start_ms=0,
        end_ms=30000,
        score=8.0,
        reason="Good hook",
    )
    db.add(cand)
    db.commit()

    now = datetime.utcnow()
    queued_at = (now - queued_delta) if queued_delta else now

    clip = Clip(
        id=clip_id,
        clip_candidate_id=cand.id,
        status=status,
        generated_title=f"Title for {clip_id}",
        queued_at=queued_at,
        created_at=queued_at,
    )
    db.add(clip)
    db.commit()
    return clip


# ── 1. Isolated Single Approval & Inline Publish ─────────────────────────────

def test_approve_clip_single_inline_success(test_env):
    _, db, _, _ = test_env
    channel, video = _seed_channel_and_video(db, "vid_approve_1")
    clip = _seed_clip(db, video, "clip-app-01")

    mock_slot = MagicMock()
    mock_slot.id = "slot-test-01"
    mock_slot.slot_start = datetime.utcnow() + timedelta(hours=2)

    with patch("shorts_engine.services.publishing.actions.publish_slot_service.allocate_slot", return_value=mock_slot) as mock_alloc, \
         patch("shorts_engine.services.publishing.actions.publish_slot_service.publish_slot_now", return_value={"success": True, "youtube_video_id": "yt_123"}) as mock_pub, \
         patch("shorts_engine.services.publishing.actions.emit_platform_event") as mock_telem:

        result = approve_clip_for_publishing(clip.id, db=db, actor="operator_reviewer", publish_inline=True)

        assert result["ok"] is True
        assert result["slot_id"] == "slot-test-01"
        assert result["youtube_video_id"] == "yt_123"
        mock_alloc.assert_called_once_with(channel_id=channel.id, clip_id=clip.id, db=db)
        mock_pub.assert_called_once_with(slot=mock_slot, clip=clip, db=db)
        mock_telem.assert_called_once()

        db.refresh(clip)
        assert clip.status == ClipStatus.APPROVED


# ── 2. Race Safety & Double-Approve Prevention ───────────────────────────────

def test_approve_clip_race_condition_guard(test_env):
    _, db, _, _ = test_env
    channel, video = _seed_channel_and_video(db, "vid_approve_race")
    clip = _seed_clip(db, video, "clip-app-race")

    mock_slot = MagicMock()
    mock_slot.id = "slot-test-race"
    mock_slot.slot_start = datetime.utcnow() + timedelta(hours=3)

    with patch("shorts_engine.services.publishing.actions.publish_slot_service.allocate_slot", return_value=mock_slot), \
         patch("shorts_engine.services.publishing.actions.publish_slot_service.publish_slot_now", return_value={"success": True, "youtube_video_id": "yt_race"}), \
         patch("shorts_engine.services.publishing.actions.emit_platform_event"):

        # First call succeeds
        res1 = approve_clip_for_publishing(clip.id, db=db, actor="operator_1", publish_inline=True)
        assert res1["ok"] is True

        # Second concurrent or duplicate call must be rejected atomically
        res2 = approve_clip_for_publishing(clip.id, db=db, actor="operator_2", publish_inline=True)
        assert res2["ok"] is False
        assert res2["error_type"] == "CONFLICT"
        assert "not reviewable" in res2["error"]


# ── 3. Failure Rollback to FAILED_NEEDS_REVIEW ──────────────────────────────

def test_approve_clip_failure_rollback(test_env):
    _, db, _, _ = test_env
    channel, video = _seed_channel_and_video(db, "vid_approve_fail")
    clip = _seed_clip(db, video, "clip-app-fail")

    mock_slot = MagicMock()
    mock_slot.id = "slot-fail-01"
    mock_slot.slot_start = datetime.utcnow() + timedelta(hours=1)

    with patch("shorts_engine.services.publishing.actions.publish_slot_service.allocate_slot", return_value=mock_slot), \
         patch("shorts_engine.services.publishing.actions.publish_slot_service.publish_slot_now", return_value={"success": False, "reason": "Simulated quota cap exceeded", "error_type": "PUBLISH_FAILED"}), \
         patch("shorts_engine.services.publishing.actions.emit_platform_event"):

        result = approve_clip_for_publishing(clip.id, db=db, actor="operator_reviewer", publish_inline=True)

        assert result["ok"] is False
        assert result["error_type"] == "PUBLISH_FAILED"

        db.refresh(clip)
        assert clip.status == ClipStatus.FAILED_NEEDS_REVIEW


# ── 4. Bulk Approve: 20 Clips Without Synchronous Inline Publishing ──────────

def test_bulk_approve_allocates_slots_and_enqueues_background_jobs(test_env):
    _, db, _, _ = test_env
    channel, video = _seed_channel_and_video(db, "vid_bulk_20")

    clip_ids = []
    for i in range(20):
        c = _seed_clip(db, video, f"clip-bulk-20-{i:02d}")
        clip_ids.append(c.id)

    mock_slot = MagicMock()
    mock_slot.id = "slot-bulk-test"
    mock_slot.slot_start = datetime.utcnow() + timedelta(hours=4)

    with patch("shorts_engine.services.publishing.actions.publish_slot_service.allocate_slot", return_value=mock_slot) as mock_alloc, \
         patch("shorts_engine.services.publishing.actions.publish_slot_service.publish_slot_now") as mock_inline_pub, \
         patch("shorts_engine.services.publishing.actions.durable_job_queue.enqueue") as mock_enqueue, \
         patch("shorts_engine.services.publishing.actions.emit_platform_event"):

        res = bulk_approve_clips(clip_ids, db=db, actor="bulk_operator")

        assert res["total"] == 20
        assert res["succeeded"] == 20
        assert res["failed"] == 0
        assert len(res["results"]) == 20

        # CRITICAL: Bulk approval must NEVER publish inline!
        assert mock_inline_pub.call_count == 0

        # Must allocate slots and enqueue 20 durable queue background jobs
        assert mock_alloc.call_count == 20
        assert mock_enqueue.call_count == 20
        for item in res["results"]:
            assert item["ok"] is True
            assert item["slot_start"] is not None


# ── 5. Bulk Approve: Partial Failure Resilience ──────────────────────────────

def test_bulk_approve_partial_failure_does_not_abort_batch(test_env):
    _, db, _, _ = test_env
    channel, video = _seed_channel_and_video(db, "vid_bulk_partial")

    clips = []
    for i in range(5):
        st = ClipStatus.WAITING_APPROVAL if i != 2 else ClipStatus.REJECTED
        c = _seed_clip(db, video, f"clip-bulk-part-{i}", status=st)
        clips.append(c)

    clip_ids = [c.id for c in clips]

    mock_slot = MagicMock()
    mock_slot.id = "slot-part"
    mock_slot.slot_start = datetime.utcnow() + timedelta(hours=5)

    with patch("shorts_engine.services.publishing.actions.publish_slot_service.allocate_slot", return_value=mock_slot), \
         patch("shorts_engine.services.publishing.actions.durable_job_queue.enqueue"), \
         patch("shorts_engine.services.publishing.actions.emit_platform_event"):

        res = bulk_approve_clips(clip_ids, db=db, actor="bulk_op")

        assert res["total"] == 5
        assert res["succeeded"] == 4
        assert res["failed"] == 1

        # Check outcomes per clip
        results_map = {r["clip_id"]: r for r in res["results"]}
        assert results_map["clip-bulk-part-0"]["ok"] is True
        assert results_map["clip-bulk-part-1"]["ok"] is True
        assert results_map["clip-bulk-part-2"]["ok"] is False
        assert results_map["clip-bulk-part-2"]["error_type"] == "CONFLICT"
        assert results_map["clip-bulk-part-3"]["ok"] is True
        assert results_map["clip-bulk-part-4"]["ok"] is True


# ── 6. Rejection Reason Codes, Notes & Dual-Write ─────────────────────────────

def test_reject_clip_reason_code_note_and_dual_write(test_env):
    _, db, _, _ = test_env
    _, video = _seed_channel_and_video(db, "vid_reject_dual")
    clip = _seed_clip(db, video, "clip-rej-01")

    res = reject_clip_action(
        clip_id=clip.id,
        db=db,
        actor="reviewer_op",
        reason_code="WEAK_HOOK",
        note="Hook intro is boring and quiet",
    )

    assert res["ok"] is True
    db.refresh(clip)
    assert clip.status == ClipStatus.REJECTED
    assert clip.rejection_reason_code == "WEAK_HOOK"
    assert clip.rejection_note == "Hook intro is boring and quiet"
    assert clip.rejected_at is not None

    # Dual-write check
    qr = json.loads(clip.quality_check_result or "{}")
    assert qr.get("rejection_reason") == "Hook intro is boring and quiet"
    assert qr.get("rejection_reason_code") == "WEAK_HOOK"
    assert qr.get("rejection_note") == "Hook intro is boring and quiet"


def test_bulk_reject_clips(test_env):
    _, db, _, _ = test_env
    _, video = _seed_channel_and_video(db, "vid_bulk_rej")
    c1 = _seed_clip(db, video, "clip-br-1")
    c2 = _seed_clip(db, video, "clip-br-2")

    res = bulk_reject_clips(
        clip_ids=[c1.id, c2.id],
        db=db,
        actor="reviewer_bulk",
        reason_code="AUDIO_GLITCH",
        note="Loud pop at 0:05",
    )

    assert res["total"] == 2
    assert res["succeeded"] == 2
    assert res["failed"] == 0

    db.refresh(c1)
    db.refresh(c2)
    assert c1.status == ClipStatus.REJECTED
    assert c1.rejection_reason_code == "AUDIO_GLITCH"
    assert c2.status == ClipStatus.REJECTED
    assert c2.rejection_reason_code == "AUDIO_GLITCH"


# ── 7. Global Queue Depth & Oldest Item Age Metrics ──────────────────────────

def test_queue_metrics_and_oldest_item_age(test_env):
    client, db, _, _ = test_env
    _, video = _seed_channel_and_video(db, "vid_metrics")

    # Seed clips with distinct ages
    _seed_clip(db, video, "clip-m-1", queued_delta=timedelta(minutes=15))
    _seed_clip(db, video, "clip-m-2", queued_delta=timedelta(hours=3, minutes=30))
    _seed_clip(db, video, "clip-m-3", queued_delta=timedelta(days=2, hours=4))

    resp = client.get("/queue?tab=active")
    assert resp.status_code == 200
    html = resp.text

    # Badges must show global depth and oldest item age
    assert "Depth: 3" in html
    assert "2d 4h" in html

    # With a query filter that matches nothing in view, global depth remains 3
    resp_filtered = client.get("/queue?tab=active&q=nonexistent_query_xyz")
    assert resp_filtered.status_code == 200
    html_f = resp_filtered.text
    assert "Depth: 3" in html_f
    assert "2d 4h" in html_f


# ── 8. Source Video Filtering ────────────────────────────────────────────────

def test_source_video_filter(test_env):
    client, db, _, _ = test_env
    _, v1 = _seed_channel_and_video(db, "vid_filt_1", title="First Source Video")
    _, v2 = _seed_channel_and_video(db, "vid_filt_2", title="Second Source Video")

    c1 = _seed_clip(db, v1, "clip-sv-1")
    c2 = _seed_clip(db, v2, "clip-sv-2")

    # Filter by v1
    resp1 = client.get(f"/queue?source_video_id={v1.id}")
    assert resp1.status_code == 200
    assert c1.id in resp1.text
    assert c2.id not in resp1.text

    # Filter by v2
    resp2 = client.get(f"/queue?source_video_id={v2.id}")
    assert resp2.status_code == 200
    assert c2.id in resp2.text
    assert c1.id not in resp2.text


# ── 9. HTTP Range Requests on /api/video/{clip_id} ───────────────────────────

def test_video_streaming_range_request(test_env):
    client, db, tmp_path, _ = test_env
    _, video = _seed_channel_and_video(db, "vid_range_01")

    # Create dummy 5,000-byte video
    dummy_video = tmp_path / "streamable_final.mp4"
    dummy_video.write_bytes(b"\x00\x00\x00\x1cftypisom" + b"X" * 4980)

    clip = _seed_clip(db, video, "clip-range-01")
    clip.rendered_path = str(dummy_video)
    db.commit()

    # Issue Range request for bytes 100-500
    resp = client.get(f"/api/video/{clip.id}", headers={"Range": "bytes=100-500"})
    assert resp.status_code == 206
    assert resp.headers.get("Content-Range", "").startswith("bytes 100-500/")
    assert len(resp.content) == 401


# ── 10. Bulk Action Route JSON Payload Handling ──────────────────────────────

def test_queue_bulk_action_route_json(test_env):
    client, db, _, _ = test_env
    _, video = _seed_channel_and_video(db, "vid_route_bulk")
    c1 = _seed_clip(db, video, "clip-rb-1")
    c2 = _seed_clip(db, video, "clip-rb-2")

    mock_slot = MagicMock()
    mock_slot.id = "slot-route-bulk"
    mock_slot.slot_start = datetime.utcnow() + timedelta(hours=6)

    with patch("shorts_engine.services.publishing.actions.publish_slot_service.allocate_slot", return_value=mock_slot), \
         patch("shorts_engine.services.publishing.actions.durable_job_queue.enqueue"), \
         patch("shorts_engine.services.publishing.actions.emit_platform_event"):

        # JSON bulk approve
        resp = client.post(
            "/queue/bulk-action",
            data={"action": "approve", "clip_ids": [c1.id, c2.id]},
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["total"] == 2
        assert data["succeeded"] == 2
        assert len(data["results"]) == 2
