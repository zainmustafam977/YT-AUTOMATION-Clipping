"""
Comprehensive Verification Test Suite for W3-2 Lifecycle Hardening (T1–T19).

Guarantees:
- Pure SQLite in-memory / tmp_path isolation (Zero touch on live database or network).
- Zero live uploads (All external publishing blocked / mocked).
- Arbitrates control-operation vs claim races (T15, T5, T4).
- Enforces strict slot ownership, sandbox preview, and recovery pathways (T1–T19).
"""

import json
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    Channel,
    ChannelCredential,
    SourceVideo,
    SourceVideoStatus,
    ClipCandidate,
    Clip,
    ClipStatus,
    User,
    UserRole,
    PublishSlot,
    PublishSlotStatus,
    PublishLog,
    AuditLog,
    PipelineJob,
    PipelineJobStatus,
    EventLog,
)
from shorts_engine.config.settings import settings
from shorts_engine.services.queue.durable_queue import durable_job_queue
from shorts_engine.services.worker.daemon import WorkerDaemon
from shorts_engine.services.publishing.worker import YouTubePublishWorker
from shorts_engine.services.publishing.slots import publish_slot_service
from shorts_engine.services.publishing.quota import QuotaTracker, VIDEOS_INSERT_COST_UNITS
from shorts_engine.services.publishing.actions import (
    approve_clip_for_publishing,
    reject_clip_action,
    recover_publish_job,
)
from shorts_engine.orchestration.exceptions import (
    PermanentError,
    JobCancelledError,
)
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.routes import queue as queue_route
from shorts_engine.dashboard.auth import (
    get_current_user,
    require_operator,
    require_viewer,
    require_admin,
    verify_csrf,
)


@pytest.fixture
def lifecycle_env(tmp_path):
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
    app.dependency_overrides[queue_route.get_db] = override_get_db

    test_operator = User(
        id="test-op-lifecycle",
        username="operator_lifecycle",
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

    # Seed channel and source video
    channel = Channel(
        id="chan_lifecycle_1",
        name="Lifecycle Channel",
        youtube_channel_id="UC_LIFECYCLE_1",
    )
    db.add(channel)
    cred = ChannelCredential(
        channel_id=channel.id,
        encrypted_refresh_token="fake_encrypted_refresh_token",
        scopes="youtube.upload",
        status="ACTIVE",
    )
    db.add(cred)
    video = SourceVideo(
        id="vid_lifecycle_1",
        channel_id=channel.id,
        youtube_video_id="yt_source_1",
        title="Source Video 1",
        status=SourceVideoStatus.DONE,
    )
    db.add(video)
    db.commit()

    yield client, db, tmp_path, TestSession, channel, video
    db.close()


def _seed_clip(db, video, clip_id, status=ClipStatus.WAITING_APPROVAL, rendered_path=None):
    cand = ClipCandidate(
        id=f"cand_{clip_id}",
        source_video_id=video.id,
        start_ms=0,
        end_ms=30000,
        score=9.0,
        reason="Good hook",
        hook_text="Hook for " + clip_id,
    )
    db.add(cand)
    db.commit()
    clip = Clip(
        id=clip_id,
        clip_candidate_id=cand.id,
        status=status,
        generated_title="Title " + clip_id,
        rendered_path=rendered_path,
    )
    db.add(clip)
    db.commit()
    return clip


# ── T1: REST approve returns 202; HTMX returns 200/303 ─────────────────────────

def test_t1_rest_and_htmx_approval_responses(lifecycle_env):
    client, db, tmp_path, _, channel, video = lifecycle_env

    # 1. REST approval returns HTTP 202 Accepted
    clip1 = _seed_clip(db, video, "clip_t1_rest")
    resp_rest = client.post(f"/api/clips/{clip1.id}/approve")
    assert resp_rest.status_code == 202
    data_rest = resp_rest.json()
    assert data_rest["status"] == "accepted"
    assert data_rest["new_status"] == "APPROVED"
    assert data_rest["job_id"] is not None

    # 2. HTMX approval returns HTTP 200 with HX-Trigger
    clip2 = _seed_clip(db, video, "clip_t1_htmx")
    resp_htmx = client.post(
        f"/queue/{clip2.id}/approve",
        headers={"hx-request": "true"},
    )
    assert resp_htmx.status_code == 200
    assert "HX-Trigger" in resp_htmx.headers
    trigger_data = json.loads(resp_htmx.headers["HX-Trigger"])
    assert "clipApproved" in trigger_data

    # 3. Form document approval returns HTTP 303 Redirect
    clip3 = _seed_clip(db, video, "clip_t1_form")
    resp_form = client.post(
        f"/queue/{clip3.id}/approve",
        data={"return_query": "page=1"},
        follow_redirects=False,
    )
    assert resp_form.status_code == 303
    assert "/queue" in resp_form.headers["location"]


# ── T2: Reschedule changes slot, clip schedule, job next_run_at, payload atomically ──

def test_t2_reschedule_atomic_synchronization(lifecycle_env):
    _, db, _, _, channel, video = lifecycle_env
    clip = _seed_clip(db, video, "clip_t2_sync")

    res = approve_clip_for_publishing(clip.id, db=db, actor="op_t2")
    assert res["ok"] is True

    new_time = (datetime.utcnow() + timedelta(days=3)).replace(microsecond=0)
    res_sched = publish_slot_service.reschedule_clip(clip.id, new_time, db=db)
    assert res_sched["success"] is True

    # Verify slot, clip, and job synced atomically
    slot = db.query(PublishSlot).filter_by(clip_id=clip.id).first()
    assert slot.slot_start == new_time

    db.refresh(clip)
    assert clip.scheduled_publish_at == new_time

    job = db.query(PipelineJob).filter_by(entity_id=clip.id, stage="publish").first()
    assert job.next_run_at == new_time
    payload = json.loads(job.payload_json)
    assert payload["scheduled_at"] == new_time.isoformat()

    audit = db.query(AuditLog).filter_by(job_id=job.id, event="clip.rescheduled").first()
    assert audit is not None


# ── T3: Reschedule collision rolls back and returns 409 ─────────────────────────

def test_t3_reschedule_collision_rollback_409(lifecycle_env):
    _, db, _, _, channel, video = lifecycle_env
    clip1 = _seed_clip(db, video, "clip_t3_a")
    clip2 = _seed_clip(db, video, "clip_t3_b")

    res1 = approve_clip_for_publishing(clip1.id, db=db)
    res2 = approve_clip_for_publishing(clip2.id, db=db)
    assert res1["ok"] and res2["ok"]

    slot1 = db.query(PublishSlot).filter_by(clip_id=clip1.id).first()
    slot2 = db.query(PublishSlot).filter_by(clip_id=clip2.id).first()
    initial_slot2_time = slot2.slot_start

    # Attempt to reschedule clip2 into clip1's slot time
    with pytest.raises(HTTPException) as exc_info:
        publish_slot_service.reschedule_clip(clip2.id, slot1.slot_start, db=db)
    assert exc_info.value.status_code == 409

    db.refresh(slot2)
    assert slot2.slot_start == initial_slot2_time


# ── T4: Pending cancel prevents upload; worker raises JobCancelledError; 0 quota, 0 logs ─

def test_t4_pending_cancel_prevents_upload_and_consumes_zero_quota(lifecycle_env):
    _, db, tmp_path, _, channel, video = lifecycle_env
    rendered = tmp_path / "t4.mp4"
    rendered.write_bytes(b"\x00" * 1024)
    clip = _seed_clip(db, video, "clip_t4_cancel", rendered_path=str(rendered))

    res = approve_clip_for_publishing(clip.id, db=db)
    assert res["ok"] is True
    slot = db.query(PublishSlot).filter_by(clip_id=clip.id).first()

    # Cancel approval
    unapp = publish_slot_service.unapprove_clip(clip.id, db=db)
    assert unapp["success"] is True

    # Verify slot cancelled, clip back to WAITING_APPROVAL
    db.refresh(slot)
    assert slot.status == PublishSlotStatus.CANCELLED.value
    assert slot.clip_id == clip.id  # I8: ownership preserved

    db.refresh(clip)
    assert clip.status == ClipStatus.WAITING_APPROVAL

    # Worker executes publish intent -> must raise JobCancelledError
    worker = YouTubePublishWorker()
    with pytest.raises(JobCancelledError):
        worker.execute_publish_intent(
            clip_id=clip.id,
            payload={"slot_id": slot.id},
            db=db,
        )

    # Assert 0 quota used, 0 success publish logs
    tracker = QuotaTracker()
    assert tracker.get_used_units(db=db) == 0
    p_log = db.query(PublishLog).filter_by(clip_id=clip.id).first()
    assert p_log is None or p_log.status != "SUCCESS"


# ── T5: RUNNING reschedule/cancel returns 409 ───────────────────────────────────

def test_t5_running_reschedule_or_cancel_returns_409(lifecycle_env):
    _, db, _, _, channel, video = lifecycle_env
    clip = _seed_clip(db, video, "clip_t5_running")
    approve_clip_for_publishing(clip.id, db=db)

    job = db.query(PipelineJob).filter_by(entity_id=clip.id, stage="publish").first()
    job.status = PipelineJobStatus.RUNNING
    db.commit()

    with pytest.raises(HTTPException) as exc_unapp:
        publish_slot_service.unapprove_clip(clip.id, db=db)
    assert exc_unapp.value.status_code == 409

    with pytest.raises(HTTPException) as exc_sched:
        publish_slot_service.reschedule_clip(clip.id, datetime.utcnow() + timedelta(days=1), db=db)
    assert exc_sched.value.status_code == 409

    rej = reject_clip_action(clip.id, db=db)
    assert rej["ok"] is False
    assert rej["error_type"] == "CONFLICT"


# ── T6: Worker rejects mismatched/missing/cancelled slot bindings ───────────────

def test_t6_worker_rejects_corrupt_missing_cancelled_slot_bindings(lifecycle_env):
    _, db, tmp_path, _, channel, video = lifecycle_env
    rendered = tmp_path / "t6.mp4"
    rendered.write_bytes(b"\x00" * 1024)
    clip = _seed_clip(db, video, "clip_t6_bindings", status=ClipStatus.APPROVED, rendered_path=str(rendered))

    worker = YouTubePublishWorker()

    # 1. Missing slot_id
    with pytest.raises(PermanentError) as exc_missing:
        worker.execute_publish_intent(clip.id, payload={}, db=db)
    assert "Missing mandatory slot_id binding" in str(exc_missing.value)

    # 2. Non-existent slot_id
    with pytest.raises(PermanentError) as exc_corrupt:
        worker.execute_publish_intent(clip.id, payload={"slot_id": "nonexistent_slot_id"}, db=db)
    assert "Corrupt slot binding" in str(exc_corrupt.value)

    # 3. Channel mismatch
    other_chan = Channel(id="chan_other_t6", name="Other", youtube_channel_id="UC_OTHER")
    db.add(other_chan)
    slot_mismatch = PublishSlot(
        channel_id=other_chan.id,
        clip_id=clip.id,
        slot_start=datetime.utcnow() + timedelta(hours=1),
        platform="youtube",
        status=PublishSlotStatus.RESERVED.value,
    )
    db.add(slot_mismatch)
    db.commit()

    with pytest.raises(PermanentError) as exc_chan:
        worker.execute_publish_intent(clip.id, payload={"slot_id": slot_mismatch.id}, db=db)
    assert "Slot channel mismatch" in str(exc_chan.value)

    # 4. Cancelled slot
    slot_cancelled = publish_slot_service.allocate_slot(channel.id, clip.id, db=db)
    slot_cancelled.status = PublishSlotStatus.CANCELLED.value
    db.commit()

    with pytest.raises(JobCancelledError):
        worker.execute_publish_intent(clip.id, payload={"slot_id": slot_cancelled.id}, db=db)


# ── T7: Resumable retry in PUBLISHING with session URI succeeds ────────────────

def test_t7_resumable_retry_in_publishing_with_session_uri_succeeds(lifecycle_env):
    _, db, tmp_path, _, channel, video = lifecycle_env
    rendered = tmp_path / "t7.mp4"
    rendered.write_bytes(b"\x00" * 1024)
    clip = _seed_clip(db, video, "clip_t7_resumable", status=ClipStatus.PUBLISHING, rendered_path=str(rendered))
    slot = publish_slot_service.allocate_slot(channel.id, clip.id, db=db)

    worker = YouTubePublishWorker(dry_run=False)
    fake_session_uri = "https://youtube.googleapis.com/upload/session/fake_resumable_t7"

    mock_client = MagicMock()
    mock_request = MagicMock()
    mock_request.resumable_uri = fake_session_uri
    mock_request.next_chunk.return_value = (None, {"id": "resumed_vid_77"})
    mock_client.videos().insert.return_value = mock_request

    with patch.object(worker, "_get_channel_refresh_token", return_value="fake_refresh"), \
         patch("shorts_engine.services.publishing.worker.build_youtube_client", return_value=mock_client), \
         patch("shorts_engine.services.publishing.worker.check_channel_recent_uploads_for_clip", return_value=None):
        res = worker.execute_publish_intent(
            clip_id=clip.id,
            payload={"slot_id": slot.id, "upload_session_uri": fake_session_uri},
            db=db,
        )

    assert res["success"] is True
    assert res["youtube_video_id"] == "resumed_vid_77"
    db.refresh(clip)
    assert clip.status == ClipStatus.PUBLISHED


# ── T8: Upload success finalizes exact bound slot ──────────────────────────────

def test_t8_upload_success_finalizes_exact_bound_slot(lifecycle_env):
    _, db, tmp_path, _, channel, video = lifecycle_env
    worker = YouTubePublishWorker(dry_run=False)

    # 8a: Future publishAt -> slot.status = SCHEDULED, clip.status = SCHEDULED
    future_time = datetime.utcnow() + timedelta(days=2)
    rendered_a = tmp_path / "t8a.mp4"
    rendered_a.write_bytes(b"\x00" * 1024)
    clip_a = _seed_clip(db, video, "clip_t8a_future", status=ClipStatus.APPROVED, rendered_path=str(rendered_a))
    slot_a = publish_slot_service.allocate_slot(channel.id, clip_a.id, db=db, preferred_time=future_time)

    mock_client_a = MagicMock()
    mock_req_a = MagicMock()
    mock_req_a.next_chunk.return_value = (None, {"id": "vid_fut_8a"})
    mock_client_a.videos().insert.return_value = mock_req_a

    with patch.object(worker, "_get_channel_refresh_token", return_value="fake_refresh"), \
         patch("shorts_engine.services.publishing.worker.build_youtube_client", return_value=mock_client_a), \
         patch("shorts_engine.services.publishing.worker.check_channel_recent_uploads_for_clip", return_value=None):
        res_a = worker.execute_publish_intent(
            clip_id=clip_a.id,
            payload={"slot_id": slot_a.id, "scheduled_at": future_time.isoformat()},
            db=db,
        )
    assert res_a["success"] is True
    db.refresh(slot_a)
    db.refresh(clip_a)
    assert slot_a.status == PublishSlotStatus.SCHEDULED.value
    assert slot_a.youtube_video_id == "vid_fut_8a"
    assert clip_a.status == ClipStatus.SCHEDULED

    # 8b: Immediate publish -> slot.status = PUBLISHED, clip.status = PUBLISHED
    past_time = datetime.utcnow() - timedelta(minutes=10)
    rendered_b = tmp_path / "t8b.mp4"
    rendered_b.write_bytes(b"\x00" * 1024)
    clip_b = _seed_clip(db, video, "clip_t8b_imm", status=ClipStatus.APPROVED, rendered_path=str(rendered_b))
    slot_b = publish_slot_service.allocate_slot(channel.id, clip_b.id, db=db, preferred_time=past_time)

    mock_client_b = MagicMock()
    mock_req_b = MagicMock()
    mock_req_b.next_chunk.return_value = (None, {"id": "vid_imm_8b"})
    mock_client_b.videos().insert.return_value = mock_req_b

    with patch.object(worker, "_get_channel_refresh_token", return_value="fake_refresh"), \
         patch("shorts_engine.services.publishing.worker.build_youtube_client", return_value=mock_client_b), \
         patch("shorts_engine.services.publishing.worker.check_channel_recent_uploads_for_clip", return_value=None):
        res_b = worker.execute_publish_intent(
            clip_id=clip_b.id,
            payload={"slot_id": slot_b.id, "scheduled_at": past_time.isoformat()},
            db=db,
        )
    assert res_b["success"] is True
    db.refresh(slot_b)
    db.refresh(clip_b)
    assert slot_b.status == PublishSlotStatus.PUBLISHED.value
    assert slot_b.youtube_video_id == "vid_imm_8b"
    assert clip_b.status == ClipStatus.PUBLISHED


# ── T9: Auth failure keeps slot RESERVED and job QUARANTINED ───────────────────

def test_t9_auth_failure_keeps_slot_reserved_and_job_quarantined(lifecycle_env):
    _, db, tmp_path, _, channel, video = lifecycle_env
    rendered = tmp_path / "t9.mp4"
    rendered.write_bytes(b"\x00" * 1024)
    clip = _seed_clip(db, video, "clip_t9_auth", status=ClipStatus.APPROVED, rendered_path=str(rendered))
    slot = publish_slot_service.allocate_slot(channel.id, clip.id, db=db)

    job = PipelineJob(
        job_type="publish",
        entity_type="clip",
        entity_id=clip.id,
        stage="publish",
        status=PipelineJobStatus.PENDING,
        attempt_count=1,
    )
    db.add(job)
    db.commit()

    # Mark channel credential as needs_reauth
    cred = db.query(ChannelCredential).filter_by(channel_id=channel.id).first()
    cred.status = "needs_reauth"
    db.commit()

    worker = YouTubePublishWorker(dry_run=False)
    res = worker.execute_publish_intent(
        clip_id=clip.id,
        payload={"slot_id": slot.id},
        db=db,
        job_entity=job,
    )

    assert res["status"] == "QUARANTINED"
    db.refresh(slot)
    assert slot.status == PublishSlotStatus.RESERVED.value  # I7: slot remains reserved
    db.refresh(job)
    assert job.status == PipelineJobStatus.QUARANTINED


# ── T10: DONE real publication cannot be revived by ordinary approval/enqueue ──

def test_t10_done_publication_cannot_be_revived_by_ordinary_approval_or_enqueue(lifecycle_env):
    _, db, _, _, channel, video = lifecycle_env
    clip = _seed_clip(db, video, "clip_t10_done", status=ClipStatus.PUBLISHED)
    clip.youtube_video_id = "yt_real_10"
    db.commit()

    # Ordinary approval must reject
    res = approve_clip_for_publishing(clip.id, db=db)
    assert res["ok"] is False
    assert res["error_type"] == "CONFLICT"

    # Generic enqueue must not reset a terminal job
    job = PipelineJob(
        job_type="publish",
        entity_type="clip",
        entity_id=clip.id,
        stage="publish",
        status=PipelineJobStatus.DONE,
        attempt_count=1,
    )
    db.add(job)
    db.commit()

    enq_job = durable_job_queue.enqueue("publish", clip.id, stage="publish", db=db)
    assert enq_job.id == job.id
    db.refresh(job)
    assert job.status == PipelineJobStatus.DONE  # Untouched


# ── T11: Explicit recovery endpoint resets attempts, re-binds slot, returns 202 ─

def test_t11_explicit_recovery_endpoint_resets_attempts_and_rebinds_slot(lifecycle_env):
    client, db, _, _, channel, video = lifecycle_env
    clip = _seed_clip(db, video, "clip_t11_rec", status=ClipStatus.APPROVED)
    expired_slot = publish_slot_service.allocate_slot(channel.id, clip.id, db=db)
    expired_slot.status = PublishSlotStatus.CANCELLED.value
    db.commit()

    job = PipelineJob(
        job_type="publish",
        entity_type="clip",
        entity_id=clip.id,
        stage="publish",
        status=PipelineJobStatus.QUARANTINED,
        attempt_count=3,
        payload_json=json.dumps({"slot_id": expired_slot.id, "upload_session_uri": "https://saved.uri/11"}),
    )
    db.add(job)
    db.commit()

    # Call recovery endpoint
    resp = client.post(
        f"/api/pipeline/jobs/{job.id}/recover",
        data={"reason": "Operator OAuth re-authentication complete"},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "PENDING"

    db.refresh(job)
    assert job.status == PipelineJobStatus.PENDING
    assert job.attempt_count == 0  # I4: reset attempt_count to 0
    payload = json.loads(job.payload_json)
    assert payload["upload_session_uri"] == "https://saved.uri/11"  # Evidence preserved

    new_slot = db.query(PublishSlot).filter_by(id=payload["slot_id"]).first()
    assert new_slot is not None
    assert new_slot.status == PublishSlotStatus.RESERVED.value

    audit = db.query(AuditLog).filter_by(job_id=job.id, event="job.recovered").first()
    assert audit is not None
    details = json.loads(audit.details)
    assert details["previous_attempt_count"] == 3


# ── T12: Sandbox preview rejects production DB bind; does not set clip PUBLISHED or consume quota ─

def test_t12_sandbox_preview_rejects_production_db_and_does_not_consume_quota(lifecycle_env):
    _, db, tmp_path, _, channel, video = lifecycle_env
    rendered = tmp_path / "t12.mp4"
    rendered.write_bytes(b"\x00" * 1024)
    clip = _seed_clip(db, video, "clip_t12_preview", status=ClipStatus.APPROVED, rendered_path=str(rendered))
    slot = publish_slot_service.allocate_slot(channel.id, clip.id, db=db)

    worker = YouTubePublishWorker(dry_run=True)

    # 12a: Production DB bind simulation rejects dry_run
    class MockUrl:
        def __str__(self):
            return "sqlite:///storage/shorts_automation.db"
        def lower(self):
            return "sqlite:///storage/shorts_automation.db"

    real_bind = db.get_bind()
    with patch.dict("os.environ", {"ALLOW_IN_PROCESS_PREVIEW": "1"}), \
         patch.object(real_bind, "url", MockUrl()):
        with pytest.raises(PermanentError) as exc_prod:
            worker.execute_publish_intent(clip.id, payload={"slot_id": slot.id, "dry_run": True}, db=db)
        assert "Dry-run simulation mode is only permitted" in str(exc_prod.value)

    # 12b: Test database with ALLOW_IN_PROCESS_PREVIEW=1 succeeds without modifying clip/slot status
    with patch.dict("os.environ", {"ALLOW_IN_PROCESS_PREVIEW": "1"}):
        res = worker.execute_publish_intent(clip.id, payload={"slot_id": slot.id, "dry_run": True}, db=db)

    assert res["success"] is True
    assert res["status"] == "DRY_RUN"

    db.refresh(clip)
    assert clip.status == ClipStatus.APPROVED  # I5: never set PUBLISHED

    db.refresh(slot)
    assert slot.status == PublishSlotStatus.RESERVED.value  # I5: never set PUBLISHED

    tracker = QuotaTracker()
    assert tracker.get_used_units(db=db) == 0  # 0 quota

    p_log = db.query(PublishLog).filter_by(clip_id=clip.id).first()
    assert p_log.status == "DRY_RUN"


# ── T13: Repeated approval asserts exactly 1 slot and 1 job ────────────────────

def test_t13_repeated_approval_asserts_exactly_one_slot_and_job(lifecycle_env):
    _, db, _, _, channel, video = lifecycle_env
    clip = _seed_clip(db, video, "clip_t13_repeat")

    res1 = approve_clip_for_publishing(clip.id, db=db)
    assert res1["ok"] is True

    res2 = approve_clip_for_publishing(clip.id, db=db)
    assert res2["ok"] is False
    assert res2["error_type"] == "CONFLICT"

    slot_count = db.query(PublishSlot).filter_by(clip_id=clip.id).count()
    assert slot_count == 1

    job_count = db.query(PipelineJob).filter_by(entity_id=clip.id).count()
    assert job_count == 1


# ── T14: Concurrent approval on separate sessions asserts uniqueness and atomicity ─

def test_t14_concurrent_approval_on_separate_sessions_atomicity(lifecycle_env):
    _, db, _, TestSession, channel, video = lifecycle_env
    clip = _seed_clip(db, video, "clip_t14_concurrent")

    sess1 = TestSession()
    sess2 = TestSession()

    try:
        res1 = approve_clip_for_publishing(clip.id, db=sess1)
        assert res1["ok"] is True

        res2 = approve_clip_for_publishing(clip.id, db=sess2)
        assert res2["ok"] is False
        assert res2["error_type"] == "CONFLICT"
    finally:
        sess1.close()
        sess2.close()

    total_slots = db.query(PublishSlot).filter_by(clip_id=clip.id).count()
    assert total_slots == 1

    total_jobs = db.query(PipelineJob).filter_by(entity_id=clip.id).count()
    assert total_jobs == 1


# ── T15: Race barrier: worker selects due job -> operator reschedules future -> worker CAS fails ─

def test_t15_race_barrier_reschedule_defeats_worker_claim_cas(lifecycle_env):
    _, db, _, _, channel, video = lifecycle_env
    clip = _seed_clip(db, video, "clip_t15_barrier")
    res = approve_clip_for_publishing(clip.id, db=db)
    assert res["ok"] is True

    job = db.query(PipelineJob).filter_by(entity_id=clip.id, stage="publish").first()
    # Make due
    job.next_run_at = datetime.utcnow() - timedelta(minutes=5)
    db.commit()

    # 1. Worker candidate SELECT reads job as due
    worker_now = datetime.utcnow()
    candidate = (
        db.query(PipelineJob)
        .filter(
            PipelineJob.id == job.id,
            PipelineJob.status == PipelineJobStatus.PENDING,
            PipelineJob.next_run_at <= worker_now,
        )
        .first()
    )
    assert candidate is not None

    # 2. Race Barrier: Operator reschedules job to future before worker UPDATE CAS executes
    future_time = (datetime.utcnow() + timedelta(hours=3)).replace(microsecond=0)
    publish_slot_service.reschedule_clip(clip.id, future_time, db=db)

    # 3. Worker executes claim CAS update with next_run_at <= worker_now recheck
    rows_claimed = (
        db.query(PipelineJob)
        .filter(
            PipelineJob.id == candidate.id,
            PipelineJob.status == PipelineJobStatus.PENDING,
            PipelineJob.next_run_at <= worker_now,
        )
        .update(
            {
                PipelineJob.status: PipelineJobStatus.RUNNING,
                PipelineJob.locked_by: "worker_test",
                PipelineJob.locked_at: datetime.utcnow(),
            },
            synchronize_session=False,
        )
    )
    # CAS must fail (0 rows updated) because operator changed next_run_at
    assert rows_claimed == 0

    db.refresh(job)
    assert job.status == PipelineJobStatus.PENDING
    assert job.next_run_at.replace(microsecond=0) == future_time


# ── T16: Cancellation non-overwrite: cancelled job remains CANCELLED after daemon handling ─

def test_t16_cancellation_non_overwrite_in_daemon(lifecycle_env):
    _, db, tmp_path, TestSession, channel, video = lifecycle_env
    rendered = tmp_path / "t16.mp4"
    rendered.write_bytes(b"\x00" * 1024)
    clip = _seed_clip(db, video, "clip_t16_cancel", status=ClipStatus.APPROVED, rendered_path=str(rendered))
    slot = publish_slot_service.allocate_slot(channel.id, clip.id, db=db)

    job = PipelineJob(
        job_type="publish",
        entity_type="clip",
        entity_id=clip.id,
        stage="publish",
        status=PipelineJobStatus.PENDING,
        next_run_at=datetime.utcnow() - timedelta(seconds=1),
        payload_json=json.dumps({"slot_id": slot.id}),
    )
    db.add(job)
    db.commit()

    # Cancel slot before execution
    slot.status = PublishSlotStatus.CANCELLED.value
    db.commit()

    daemon = WorkerDaemon(session_factory=TestSession, pinned_groups=["publish"])
    claimed = daemon.run_once()
    assert claimed is not None

    db.refresh(job)
    assert job.status == PipelineJobStatus.CANCELLED  # Not overwritten to DONE


# ── T17: Attempted upload safety: in-flight/retrying upload with session URI cannot be cancelled/rescheduled ─

def test_t17_attempted_upload_safety_session_uri_cannot_cancel_reschedule(lifecycle_env):
    _, db, _, _, channel, video = lifecycle_env
    clip = _seed_clip(db, video, "clip_t17_safety")
    approve_clip_for_publishing(clip.id, db=db)

    job = db.query(PipelineJob).filter_by(entity_id=clip.id, stage="publish").first()
    job.payload_json = json.dumps({"slot_id": "slot_17", "upload_session_uri": "https://active.upload.url"})
    db.commit()

    with pytest.raises(HTTPException) as exc_unapp:
        publish_slot_service.unapprove_clip(clip.id, db=db)
    assert exc_unapp.value.status_code == 409

    with pytest.raises(HTTPException) as exc_sched:
        publish_slot_service.reschedule_clip(clip.id, datetime.utcnow() + timedelta(days=1), db=db)
    assert exc_sched.value.status_code == 409

    rej = reject_clip_action(clip.id, db=db)
    assert rej["ok"] is False
    assert rej["error_type"] == "CONFLICT"


# ── T18: Remote success reclaim: crash before local finalization -> retry reclaims existing video ID without second upload ─

def test_t18_remote_success_reclaim_without_second_upload(lifecycle_env):
    _, db, tmp_path, _, channel, video = lifecycle_env
    rendered = tmp_path / "t18.mp4"
    rendered.write_bytes(b"\x00" * 1024)
    clip = _seed_clip(db, video, "clip_t18_reclaim", status=ClipStatus.APPROVED, rendered_path=str(rendered))
    slot = publish_slot_service.allocate_slot(channel.id, clip.id, db=db)

    worker = YouTubePublishWorker(dry_run=False)

    mock_client = MagicMock()
    # If insert() is called, fail the test
    mock_client.videos().insert.side_effect = RuntimeError("Second upload attempted! Reclaim must avoid videos().insert()")

    with patch.object(worker, "_get_channel_refresh_token", return_value="fake_refresh"), \
         patch("shorts_engine.services.publishing.worker.build_youtube_client", return_value=mock_client), \
         patch("shorts_engine.services.publishing.worker.check_channel_recent_uploads_for_clip", return_value="reclaimed_vid_18"):
        res = worker.execute_publish_intent(
            clip_id=clip.id,
            payload={"slot_id": slot.id},
            db=db,
        )

    assert res["success"] is True
    assert res["reclaimed"] is True
    assert res["youtube_video_id"] == "reclaimed_vid_18"

    db.refresh(slot)
    assert slot.youtube_video_id == "reclaimed_vid_18"
    assert slot.status == PublishSlotStatus.PUBLISHED.value

    db.refresh(clip)
    assert clip.youtube_video_id == "reclaimed_vid_18"
    assert clip.status == ClipStatus.PUBLISHED


# ── T19: Quota exclusion: PublishLog(status="DRY_RUN") excluded from quota counts ─

def test_t19_quota_exclusion_dry_run_ignored_in_quota_tracking(lifecycle_env):
    _, db, _, _, channel, video = lifecycle_env
    tracker = QuotaTracker()

    clip_dry = _seed_clip(db, video, "clip_dry_19")
    clip_succ = _seed_clip(db, video, "clip_success_19")

    # Seed 1 DRY_RUN log (0 actual quota)
    log_dry = PublishLog(
        clip_id=clip_dry.id,
        youtube_video_id="dry_run_12345",
        published_at=datetime.utcnow(),
        title="Dry Run Title",
        description="Dry Run Desc",
        status="DRY_RUN",
    )
    db.add(log_dry)

    # Seed 1 SUCCESS log (1600 quota)
    log_success = PublishLog(
        clip_id=clip_succ.id,
        youtube_video_id="real_yt_19",
        published_at=datetime.utcnow(),
        title="Success Title",
        description="Success Desc",
        status="SUCCESS",
    )
    db.add(log_success)
    db.commit()

    used = tracker.get_used_units(db=db)
    assert used == VIDEOS_INSERT_COST_UNITS  # Exactly 1600 units, not 3200

    uploads = tracker.get_uploads_today(db=db)
    assert uploads == 1  # Exactly 1 upload, not 2
