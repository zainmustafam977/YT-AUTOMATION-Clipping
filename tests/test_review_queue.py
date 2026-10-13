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
    PublishSlot,
    PublishLog,
    PipelineJob,
    PipelineJobStatus,
    EventLog,
)
from shorts_engine.config.settings import settings
from shorts_engine.services.queue.durable_queue import durable_job_queue
from shorts_engine.services.worker.daemon import WorkerDaemon
from shorts_engine.services.publishing.worker import YouTubePublishWorker
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
    """
    Cluster-C regression test updated for W3-2:
    Single approval is enqueue-only (no inline synchronous publish).
    Allocates slot atomically with commit=False, enqueues publish job with commit=False,
    and commits in one single atomic database transaction.
    """
    _, db, _, _ = test_env
    channel, video = _seed_channel_and_video(db, "vid_approve_1")
    clip = _seed_clip(db, video, "clip-app-01")

    mock_slot = MagicMock()
    mock_slot.id = "slot-test-01"
    mock_slot.slot_start = datetime.utcnow() + timedelta(hours=2)

    mock_job = MagicMock()
    mock_job.id = "job-publish-01"

    with patch("shorts_engine.services.publishing.actions.publish_slot_service.allocate_slot", return_value=mock_slot) as mock_alloc, \
         patch("shorts_engine.services.publishing.actions.durable_job_queue.enqueue", return_value=mock_job) as mock_enqueue, \
         patch("shorts_engine.services.publishing.actions.emit_platform_event") as mock_telem:

        result = approve_clip_for_publishing(clip.id, db=db, actor="operator_reviewer")

        assert result["ok"] is True
        assert result["slot_id"] == "slot-test-01"
        assert result["job_id"] == "job-publish-01"
        assert result["enqueued"] is True
        mock_alloc.assert_called_once_with(channel_id=channel.id, clip_id=clip.id, db=db, commit=False)
        mock_enqueue.assert_called_once_with(
            job_type="publish",
            entity_id=clip.id,
            entity_type="clip",
            stage="publish",
            next_run_at=mock_slot.slot_start,
            payload={
                "clip_id": clip.id,
                "slot_id": mock_slot.id,
                "scheduled_at": mock_slot.slot_start.isoformat(),
            },
            db=db,
            commit=False,
        )
        mock_telem.assert_called_once()

        db.refresh(clip)
        assert clip.status == ClipStatus.APPROVED
        assert clip.scheduled_publish_at == mock_slot.slot_start


# ── 2. Race Safety & Double-Approve Prevention ───────────────────────────────

def test_approve_clip_race_condition_guard(test_env):
    _, db, _, _ = test_env
    channel, video = _seed_channel_and_video(db, "vid_approve_race")
    clip = _seed_clip(db, video, "clip-app-race")

    mock_slot = MagicMock()
    mock_slot.id = "slot-test-race"
    mock_slot.slot_start = datetime.utcnow() + timedelta(hours=3)

    mock_job = MagicMock()
    mock_job.id = "job-publish-race"

    with patch("shorts_engine.services.publishing.actions.publish_slot_service.allocate_slot", return_value=mock_slot), \
         patch("shorts_engine.services.publishing.actions.durable_job_queue.enqueue", return_value=mock_job), \
         patch("shorts_engine.services.publishing.actions.emit_platform_event"):

        # First call succeeds
        res1 = approve_clip_for_publishing(clip.id, db=db, actor="operator_1")
        assert res1["ok"] is True

        # Second concurrent or duplicate call must be rejected atomically
        res2 = approve_clip_for_publishing(clip.id, db=db, actor="operator_2")
        assert res2["ok"] is False
        assert res2["error_type"] == "CONFLICT"
        assert "not reviewable" in res2["error"]


# ── 3. Failure Rollback to FAILED_NEEDS_REVIEW ──────────────────────────────

def test_approve_clip_failure_rollback(test_env):
    """
    A3: Failure rollback in atomic approval transaction.
    If slot allocation or job enqueuing fails, the entire transaction rolls back:
    clip status remains UNCHANGED (WAITING_APPROVAL), scheduled_publish_at is None.
    """
    _, db, _, _ = test_env
    channel, video = _seed_channel_and_video(db, "vid_approve_fail")
    clip = _seed_clip(db, video, "clip-app-fail", status=ClipStatus.WAITING_APPROVAL)

    with patch("shorts_engine.services.publishing.actions.publish_slot_service.allocate_slot", side_effect=RuntimeError("Simulated allocation conflict")), \
         patch("shorts_engine.services.publishing.actions.emit_platform_event"):

        result = approve_clip_for_publishing(clip.id, db=db, actor="operator_reviewer")

        assert result["ok"] is False
        assert result["error_type"] == "SLOT_ALLOCATION_FAILED"

        db.refresh(clip)
        # Atomic rollback verification: clip status must be completely untouched
        assert clip.status == ClipStatus.WAITING_APPROVAL
        assert clip.scheduled_publish_at is None


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

    mock_job = MagicMock()
    mock_job.id = "job-bulk-test"

    with patch("shorts_engine.services.publishing.actions.publish_slot_service.allocate_slot", return_value=mock_slot) as mock_alloc, \
         patch("shorts_engine.services.publishing.actions.durable_job_queue.enqueue", return_value=mock_job) as mock_enqueue, \
         patch("shorts_engine.services.publishing.actions.emit_platform_event"):

        res = bulk_approve_clips(clip_ids, db=db, actor="bulk_operator")

        assert res["total"] == 20
        assert res["succeeded"] == 20
        assert res["failed"] == 0
        assert len(res["results"]) == 20

        # Must allocate slots and enqueue 20 durable queue background jobs
        assert mock_alloc.call_count == 20
        assert mock_enqueue.call_count == 20
        for item in res["results"]:
            assert item["ok"] is True
            assert item["slot_start"] is not None
            assert item["enqueued"] is True


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


# ── 11. W3-2 A1: Claim Predicate Filters next_run_at <= now() ─────────────────

def test_durable_queue_claim_predicate_next_run_at(test_env):
    """
    W3-2 A1 Load-Bearing Assumption Verification:
    Asserts that durable_queue.claim_next_job strictly filters:
    or_(next_run_at.is_(None), next_run_at <= now()).
    - A job with next_run_at 1 hour in the future is NOT claimed.
    - A job with next_run_at in the past IS claimed.
    """
    _, db, _, TestSession = test_env
    channel, video = _seed_channel_and_video(db, "vid_claim_pred")
    clip_fut = _seed_clip(db, video, "clip_fut_pred")
    clip_past = _seed_clip(db, video, "clip_past_pred")

    # Enqueue a future publish job (+1 hour)
    fut_time = datetime.utcnow() + timedelta(hours=1)
    job_fut = durable_job_queue.enqueue(
        job_type="publish",
        entity_id=clip_fut.id,
        stage="publish",
        next_run_at=fut_time,
        payload={"clip_id": clip_fut.id},
        db=db,
    )

    # durable_job_queue.claim_next_job must NOT claim future job
    claimed_fut = durable_job_queue.claim_next_job(
        worker_id="test_worker_1",
        allowed_stages=["publish"],
        db=db,
    )
    assert claimed_fut is None, "Future job was claimed before its next_run_at!"

    # Enqueue a past publish job (-5 minutes)
    past_time = datetime.utcnow() - timedelta(minutes=5)
    job_past = durable_job_queue.enqueue(
        job_type="publish",
        entity_id=clip_past.id,
        stage="publish",
        next_run_at=past_time,
        payload={"clip_id": clip_past.id},
        db=db,
    )

    # claim_next_job must claim the past job
    claimed_past = durable_job_queue.claim_next_job(
        worker_id="test_worker_2",
        allowed_stages=["publish"],
        db=db,
    )
    assert claimed_past is not None
    assert claimed_past.id == job_past.id


# ── 12. W3-2 A3: Atomic Approval Enqueue Failure Rollback ─────────────────────

def test_approve_clip_enqueue_failure_atomically_rolls_back(test_env):
    """
    W3-2 A3 Transaction Atomicity:
    Clip status mutation, slot allocation, and queue job enqueuing are committed
    in a single atomic transaction. If enqueue raises an error, all three operations
    roll back: clip remains WAITING_APPROVAL, 0 slots created, 0 jobs created.
    """
    _, db, _, _ = test_env
    channel, video = _seed_channel_and_video(db, "vid_atom_rollback")
    clip = _seed_clip(db, video, "clip_atom_rollback", status=ClipStatus.WAITING_APPROVAL)

    with patch("shorts_engine.services.publishing.actions.durable_job_queue.enqueue", side_effect=RuntimeError("Disk queue failure")):
        res = approve_clip_for_publishing(clip.id, db=db, actor="reviewer_atom")

    assert res["ok"] is False
    assert res["error_type"] == "SLOT_ALLOCATION_FAILED"

    db.refresh(clip)
    assert clip.status == ClipStatus.WAITING_APPROVAL
    assert clip.scheduled_publish_at is None

    # Verify zero committed slots and zero committed pipeline jobs
    slot_count = db.query(PublishSlot).filter_by(clip_id=clip.id).count()
    assert slot_count == 0
    job_count = db.query(PipelineJob).filter_by(entity_id=clip.id).count()
    assert job_count == 0


# ── 13. W3-2 A5: Full Leg Approval -> Enqueue -> Claim -> Dry-Run Publish ──────

def test_e2e_approval_to_worker_claim_publish_leg(test_env):
    """
    W3-2 A5 Integration Test:
    Full pipeline leg from operator approval -> atomic slot & job enqueue ->
    WorkerDaemon claim & dry_run publish -> DONE + PublishLog + Events.
    """
    _, db, tmp_path, TestSession = test_env
    channel, video = _seed_channel_and_video(db, "vid_e2e_full_leg")
    clip = _seed_clip(db, video, "clip_e2e_full_leg", status=ClipStatus.WAITING_APPROVAL)

    # Seed physical rendered file on disk (required by publish worker)
    rendered_file = tmp_path / "rendered_clip_leg.mp4"
    rendered_file.write_bytes(b"\x00" * 2048)
    clip.rendered_path = str(rendered_file)
    db.commit()

    # Step 1: Operator approves clip (enqueue-only, no upload in-request)
    res = approve_clip_for_publishing(clip.id, db=db, actor="test_operator")
    assert res["ok"] is True
    assert res["enqueued"] is True
    assert res["slot_id"] is not None

    db.refresh(clip)
    assert clip.status == ClipStatus.APPROVED
    assert clip.scheduled_publish_at is not None

    # Step 2: Retrieve enqueued publish job and make next_run_at eligible for claim
    job = db.query(PipelineJob).filter_by(entity_id=clip.id, stage="publish").first()
    assert job is not None
    assert job.status == PipelineJobStatus.PENDING

    # Fast-forward next_run_at to simulate arrival of the scheduled publish slot
    job.next_run_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()

    # Step 3: Worker daemon claims and processes the job in dry_run mode
    with patch.object(settings, "DRY_RUN_PUBLISHING", True):
        daemon = WorkerDaemon(session_factory=TestSession, pinned_groups=["publish"])
        claimed_job = daemon.run_once()

    assert claimed_job is not None
    assert claimed_job.id == job.id

    # Step 4: Verify post-execution states: job DONE, clip PUBLISHED, PublishLog, events
    db.refresh(job)
    assert job.status == PipelineJobStatus.DONE

    db.refresh(clip)
    assert clip.status == ClipStatus.PUBLISHED

    p_log = db.query(PublishLog).filter_by(clip_id=clip.id).first()
    assert p_log is not None
    assert p_log.status == "DRY_RUN"
    assert p_log.youtube_video_id.startswith("dry_run_")

    events = db.query(EventLog).filter(EventLog.entity_id == clip.id).all()
    event_names = [e.event_name for e in events]
    assert "clip.approved" in event_names
    assert "clip.published" in event_names


# ── 14. W3-2 A2: Worker Handles Past Slot & Emits job.late_publish Event ───────

def test_worker_execute_publish_past_slot_emits_late_publish_event(test_env):
    """
    W3-2 A2 Scheduling Semantics:
    When worker execution starts later than slot_start, the worker publishes
    immediately without error and emits a job.late_publish event for observability.
    """
    _, db, tmp_path, _ = test_env
    channel, video = _seed_channel_and_video(db, "vid_late_pub_event")
    clip = _seed_clip(db, video, "clip_late_pub_event", status=ClipStatus.APPROVED)

    rendered_file = tmp_path / "rendered_late.mp4"
    rendered_file.write_bytes(b"\x00" * 1024)
    clip.rendered_path = str(rendered_file)
    db.commit()

    past_time = datetime.utcnow() - timedelta(minutes=15)
    worker = YouTubePublishWorker(dry_run=True)

    result = worker.execute_publish_intent(
        clip_id=clip.id,
        payload={"scheduled_at": past_time.isoformat(), "dry_run": True},
        db=db,
    )

    assert result["success"] is True
    assert result["status"] == "PUBLISHED"

    # Verify job.late_publish event emitted
    late_ev = db.query(EventLog).filter_by(entity_id=clip.id, event_name="job.late_publish").first()
    assert late_ev is not None
    payload = late_ev.payload
    assert "Publish execution started later than slot_start" in payload.get("message", "")
    assert payload.get("delay_seconds", 0) > 0
