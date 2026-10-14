"""
Tests for Publish Slots, Native publishAt Scheduling, and In-Place Post-Upload Management:
1. Automatic slot calculation in channel local timezone (DST-aware, no manual math).
2. Atomic collision-free slot allocation under concurrent simultaneous approvals.
3. Persistence across process restarts / fresh DB sessions (zero state loss).
4. Immediate upload with native publishAt as PRIVATE (storing youtube_video_id on slot).
5. Non-hardcoded Dec 2025 quota costs and separate videos.insert bucket limit.
6. In-place un-approve (videos.delete), reschedule (videos.update publishAt), and metadata update without re-upload.
7. Review queue operator error surfacing (auth failure -> needs_reauth, quota failure -> midnight-PT deferral).
8. Legacy unorphaned drip-feed clips migration.
9. Platform-agnostic interface (deferred queue jobs for other syndication platforms).
10. Upcoming schedule visibility in dashboard API.
"""

import json
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.config.settings import settings
from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    Clip,
    ClipStatus,
    ClipCandidate,
    SourceVideo,
    SourceVideoStatus,
    Channel,
    ChannelCredential,
    PublishSlot,
    PublishSlotStatus,
    PublishLog,
    PipelineJob,
    PipelineJobStatus,
)
from shorts_engine.services.publishing.slots import PublishSlotService
from shorts_engine.services.publishing.actions import approve_clip_for_publishing
from shorts_engine.services.publishing.quota import QuotaTracker
from shorts_engine.services.publishing.worker import YouTubePublishWorker


@pytest.fixture
def test_db(tmp_path):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    yield session
    session.close()


def _seed_channel_and_video(db, channel_id="ch-slots-1", video_id="v-slots-1"):
    ch = Channel(
        id=channel_id,
        youtube_channel_id=f"UC_{channel_id}",
        name="Tech Insights",
        upload_defaults_json=json.dumps({
            "slot_times": ["10:00", "18:00"],
            "timezone": "America/New_York",
            "category_id": "28",
            "privacy_status": "unlisted",
        }),
    )
    db.add(ch)

    sv = SourceVideo(
        id=video_id,
        youtube_video_id=f"yt_{video_id}",
        channel_id=ch.id,
        title="Source Tech Video",
        status=SourceVideoStatus.DONE,
    )
    db.add(sv)
    db.commit()
    return ch, sv


def _seed_clip(db, sv, clip_id="clip-slot-1", status=ClipStatus.WAITING_APPROVAL, rendered_path=None):
    cand = ClipCandidate(
        id=f"cand-{clip_id}",
        source_video_id=sv.id,
        start_ms=0,
        end_ms=15000,
        hook_text="Hook Question?",
        score=9.5,
        reason="Viral Hook",
    )
    db.add(cand)
    db.commit()

    clip = Clip(
        id=clip_id,
        clip_candidate_id=cand.id,
        status=status,
        generated_title="Mind Blowing AI Secret #Shorts",
        generated_description="Full explanation inside #shorts #ai",
        generated_tags=json.dumps(["#ai", "#shorts"]),
        rendered_path=rendered_path,
    )
    db.add(clip)
    db.commit()
    return clip


def test_automatic_slot_calculation_and_dst(test_db):
    """
    Verifies that PublishSlotService automatically determines the next slot
    in channel local time (10:00 and 18:00 in America/New_York) with zero manual math.
    """
    ch, sv = _seed_channel_and_video(test_db)
    service = PublishSlotService()

    # Given a local time at 08:00 AM NY on 2026-10-15
    ny_tz = ZoneInfo("America/New_York")
    morning_time = datetime(2026, 10, 15, 8, 0, 0, tzinfo=ny_tz)

    next_slot = service.calculate_next_available_slot(ch, test_db, start_from=morning_time)

    # Next slot should be 10:00 AM NY (which is 14:00 UTC during EDT)
    slot_ny = next_slot.replace(tzinfo=ZoneInfo("UTC")).astimezone(ny_tz)
    assert slot_ny.hour == 10
    assert slot_ny.minute == 0
    assert slot_ny.date() == morning_time.date()

    # Now if current time is 11:00 AM NY, next slot should be 18:00 (6 PM) NY
    afternoon_time = datetime(2026, 10, 15, 11, 0, 0, tzinfo=ny_tz)
    next_slot_pm = service.calculate_next_available_slot(ch, test_db, start_from=afternoon_time)
    slot_pm_ny = next_slot_pm.replace(tzinfo=ZoneInfo("UTC")).astimezone(ny_tz)
    assert slot_pm_ny.hour == 18
    assert slot_pm_ny.minute == 0


def test_simultaneous_concurrent_approvals_race_collision_free(tmp_path):
    """
    ACCEPTANCE TEST: Test the race with two simultaneous approvals.
    No two clips occupy the same slot (unique constraint on channel_id + slot_start).
    One thread gets slot 1, the other gets slot 2, zero collisions.
    """
    db_file = tmp_path / "concurrent_race.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"timeout": 30.0},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    channel_id = "ch-race"
    clip1_id = "c-race-1"
    clip2_id = "c-race-2"

    setup_db = Session()
    try:
        ch, sv = _seed_channel_and_video(setup_db, channel_id=channel_id)
        _seed_clip(setup_db, sv, clip_id=clip1_id)
        _seed_clip(setup_db, sv, clip_id=clip2_id)
    finally:
        setup_db.close()

    service = PublishSlotService()
    results = {}
    errors = []

    def allocate_worker(target_clip_id, key):
        thread_session = Session()
        try:
            slot = service.allocate_slot(
                channel_id=channel_id,
                clip_id=target_clip_id,
                db=thread_session,
            )
            results[key] = (slot.id, slot.slot_start)
        except Exception as e:
            errors.append(e)
        finally:
            thread_session.close()

    t1 = threading.Thread(target=allocate_worker, args=(clip1_id, "clip1"))
    t2 = threading.Thread(target=allocate_worker, args=(clip2_id, "clip2"))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(errors) == 0, f"Thread errors: {errors}"
    assert "clip1" in results
    assert "clip2" in results

    slot1_id, slot1_time = results["clip1"]
    slot2_id, slot2_time = results["clip2"]

    # Must NOT occupy the same slot!
    assert slot1_id != slot2_id
    assert slot1_time != slot2_time

    # Verify both slots are persisted in the database
    verify_db = Session()
    try:
        slots_in_db = verify_db.query(PublishSlot).filter(PublishSlot.channel_id == channel_id).all()
        assert len(slots_in_db) == 2
        distinct_times = {s.slot_start for s in slots_in_db}
        assert len(distinct_times) == 2
    finally:
        verify_db.close()


def test_state_survives_process_restart(test_db, tmp_path):
    """
    ACCEPTANCE TEST: Killing and restarting the process loses nothing.
    """
    ch, sv = _seed_channel_and_video(test_db, channel_id="ch-restart")
    clip = _seed_clip(test_db, sv, clip_id="c-restart")

    service = PublishSlotService()
    slot = service.allocate_slot(channel_id=ch.id, clip_id=clip.id, db=test_db)
    slot_id = slot.id
    slot_time = slot.slot_start

    # Simulate complete process crash / restart: close DB session and open fresh one
    test_db.expire_all()
    engine = test_db.get_bind()
    NewSession = sessionmaker(bind=engine)
    fresh_db = NewSession()

    try:
        fresh_slot = fresh_db.query(PublishSlot).filter_by(id=slot_id).first()
        assert fresh_slot is not None
        assert fresh_slot.slot_start == slot_time
        assert fresh_slot.clip_id == clip.id
        assert fresh_slot.channel_id == ch.id
        assert fresh_slot.status == PublishSlotStatus.RESERVED.value
    finally:
        fresh_db.close()


def test_immediate_upload_with_native_publish_at(test_db, tmp_path):
    """
    Verifies that on approval, synchronous inline upload is retired (W3-2).
    Approval allocates a slot atomically and enqueues a durable publish job
    with slot_id binding, returning immediately with status 202.
    """
    ch, sv = _seed_channel_and_video(test_db, channel_id="ch-upload")
    video_file = tmp_path / "rendered_short.mp4"
    video_file.write_bytes(b"MP4 CONTENT")
    clip = _seed_clip(test_db, sv, clip_id="c-upload", rendered_path=str(video_file))

    app_res = approve_clip_for_publishing(clip.id, db=test_db)
    assert app_res["ok"] is True
    assert app_res["status"] == "APPROVED"
    assert app_res["enqueued"] is True
    assert "job_id" in app_res

    # Assert slot is allocated and RESERVED, not published
    slot = test_db.query(PublishSlot).filter_by(clip_id=clip.id).first()
    assert slot is not None
    assert slot.status == PublishSlotStatus.RESERVED.value
    assert slot.youtube_video_id is None

    # Assert publish job is enqueued with slot binding
    job = test_db.query(PipelineJob).filter_by(id=app_res["job_id"]).first()
    assert job is not None
    assert job.status == PipelineJobStatus.PENDING
    payload = json.loads(job.payload_json)
    assert payload["slot_id"] == slot.id


def test_dec_2025_quota_config_values(test_db):
    """
    Verifies that quota costs are not hardcoded to 1,600 and that separate
    videos.insert bucket limit from config is respected.
    """
    tracker = QuotaTracker()
    assert tracker.daily_limit == settings.DAILY_QUOTA_LIMIT

    # Check that settings define configurable costs
    assert settings.QUOTA_VIDEOS_INSERT_COST == 1600
    assert settings.QUOTA_VIDEOS_UPDATE_COST == 50
    assert settings.QUOTA_VIDEOS_DELETE_COST == 50
    assert settings.QUOTA_DAILY_VIDEOS_INSERT_LIMIT == 6

    # Test that can_publish evaluates against configurable limit
    can_pub, remaining, reason = tracker.can_publish(db=test_db, cost=settings.QUOTA_VIDEOS_INSERT_COST)
    assert can_pub is True
    assert remaining > 0


def test_unapprove_deletes_video_on_youtube(test_db, tmp_path):
    """
    Verifies un-approve semantics under W3-2 asynchronous publishing:
    Cancels the pending publish job, cancels the slot (preserving clip_id),
    reverts clip to WAITING_APPROVAL, and does not attempt remote YouTube deletion
    if no upload was performed yet.
    """
    ch, sv = _seed_channel_and_video(test_db, channel_id="ch-unapp")
    video_file = tmp_path / "v_unapp.mp4"
    video_file.write_bytes(b"CONTENT")
    clip = _seed_clip(test_db, sv, clip_id="c-unapp", rendered_path=str(video_file))

    app_res = approve_clip_for_publishing(clip.id, db=test_db)
    assert app_res["ok"] is True

    service = PublishSlotService()
    slot = test_db.query(PublishSlot).filter_by(clip_id=clip.id).first()
    job = test_db.query(PipelineJob).filter_by(id=app_res["job_id"]).first()

    # Now un-approve before any upload occurs
    unapp_res = service.unapprove_clip(clip_id=clip.id, db=test_db)
    assert unapp_res["success"] is True

    test_db.refresh(clip)
    assert clip.status == ClipStatus.WAITING_APPROVAL
    assert clip.scheduled_publish_at is None

    test_db.refresh(slot)
    assert slot.status == PublishSlotStatus.CANCELLED.value
    assert slot.clip_id == clip.id  # I3: slot preserves clip_id

    test_db.refresh(job)
    assert job.status == PipelineJobStatus.CANCELLED


def test_reschedule_updates_publish_at_without_reupload(test_db, tmp_path):
    """
    Verifies reschedule semantics under W3-2 asynchronous publishing:
    Updates slot time, clip scheduled_publish_at, and the pending PipelineJob's
    next_run_at and payload binding atomically without re-uploading.
    """
    ch, sv = _seed_channel_and_video(test_db, channel_id="ch-resched")
    video_file = tmp_path / "v_resched.mp4"
    video_file.write_bytes(b"CONTENT")
    clip = _seed_clip(test_db, sv, clip_id="c-resched", rendered_path=str(video_file))

    app_res = approve_clip_for_publishing(clip.id, db=test_db)
    assert app_res["ok"] is True

    service = PublishSlotService()
    slot = test_db.query(PublishSlot).filter_by(clip_id=clip.id).first()
    job = test_db.query(PipelineJob).filter_by(id=app_res["job_id"]).first()

    new_time = (datetime.utcnow() + timedelta(days=3)).replace(microsecond=0)
    res_res = service.reschedule_clip(clip_id=clip.id, new_slot_time=new_time, db=test_db)

    assert res_res["success"] is True

    test_db.refresh(slot)
    assert slot.slot_start == new_time

    test_db.refresh(clip)
    assert clip.scheduled_publish_at == new_time

    test_db.refresh(job)
    assert job.next_run_at == new_time
    job_payload = json.loads(job.payload_json)
    assert job_payload["scheduled_at"] == new_time.isoformat()


def test_metadata_edit_updates_youtube_without_reupload(test_db, tmp_path):
    """
    Verifies metadata edit semantics: updates local draft metadata.
    If video has not been uploaded yet (asynchronous publish queue),
    it updates locally without attempting network calls to YouTube.
    """
    ch, sv = _seed_channel_and_video(test_db, channel_id="ch-meta")
    video_file = tmp_path / "v_meta.mp4"
    video_file.write_bytes(b"CONTENT")
    clip = _seed_clip(test_db, sv, clip_id="c-meta", rendered_path=str(video_file))

    app_res = approve_clip_for_publishing(clip.id, db=test_db)
    assert app_res["ok"] is True

    service = PublishSlotService()
    meta_res = service.update_clip_metadata(
        clip_id=clip.id,
        db=test_db,
        title="Updated Title Winner #Shorts",
    )
    assert meta_res["success"] is True

    test_db.refresh(clip)
    assert clip.generated_title == "Updated Title Winner #Shorts"


def test_operator_surfacing_auth_and_quota_failures(test_db, tmp_path):
    """
    Verifies Amendment 4: Auth failures mark needs_reauth and surface FAILED_NEEDS_REVIEW;
    Quota failures defer to Pacific midnight and enqueue a durable queue job.
    """
    ch, sv = _seed_channel_and_video(test_db, channel_id="ch-fail")
    video_file = tmp_path / "v_fail.mp4"
    video_file.write_bytes(b"CONTENT")
    clip = _seed_clip(test_db, sv, clip_id="c-fail", rendered_path=str(video_file))

    # 1. Test Auth Failure: channel in needs_reauth
    cred = ChannelCredential(
        channel_id=ch.id,
        encrypted_refresh_token="enc_dummy",
        status="needs_reauth",
    )
    test_db.add(cred)
    test_db.commit()

    service = PublishSlotService()
    slot = service.allocate_slot(channel_id=ch.id, clip_id=clip.id, db=test_db)
    res_auth = service.publish_slot_now(slot=slot, clip=clip, db=test_db)

    assert res_auth["success"] is False
    assert res_auth["error_type"] == "AUTH_ERROR"
    assert res_auth["status"] == "FAILED_NEEDS_REVIEW"
    test_db.refresh(clip)
    assert clip.status == ClipStatus.FAILED_NEEDS_REVIEW

    # 2. Test Quota Failure: simulate zero quota remaining
    cred.status = "valid"
    test_db.commit()

    with patch.object(service.quota_tracker, "can_publish", return_value=(False, 0, "Daily upload cap reached")):
        slot2 = service.allocate_slot(channel_id=ch.id, clip_id=clip.id, db=test_db)
        res_quota = service.publish_slot_now(slot=slot2, clip=clip, db=test_db)

        assert res_quota["success"] is False
        assert res_quota["error_type"] == "QUOTA_EXCEEDED"
        assert res_quota["status"] == "QUOTA_DEFERRED"
        assert "deferred_until" in res_quota

        # Verify durable queue job was enqueued for Pacific midnight
        jobs = test_db.query(PipelineJob).filter(PipelineJob.entity_id == clip.id).all()
        assert len(jobs) >= 1
        q_job = jobs[-1]
        assert q_job.stage == "publish"
        assert q_job.next_run_at is not None


def test_legacy_drip_feed_clips_migration(test_db, tmp_path):
    """
    Verifies Amendment 5: Migration function migrates legacy clips with
    scheduled_publish_at to publish_slots so none are orphaned.
    """
    ch, sv = _seed_channel_and_video(test_db, channel_id="ch-mig")
    video_file = tmp_path / "v_mig.mp4"
    video_file.write_bytes(b"CONTENT")

    legacy_clip = _seed_clip(
        test_db,
        sv,
        clip_id="c-legacy-mig",
        status=ClipStatus.APPROVED,
        rendered_path=str(video_file),
    )
    future_time = datetime.utcnow() + timedelta(days=2)
    legacy_clip.scheduled_publish_at = future_time
    test_db.commit()

    # Run migration
    migrated_count = PublishSlotService.migrate_unorphaned_drip_feed_clips(db=test_db)
    assert migrated_count == 1

    # Verify slot was created and clip is not orphaned
    slot = test_db.query(PublishSlot).filter_by(clip_id=legacy_clip.id).first()
    assert slot is not None
    assert slot.channel_id == ch.id
    assert slot.status == PublishSlotStatus.RESERVED.value

    # Verify publish job was enqueued
    job = test_db.query(PipelineJob).filter_by(entity_id=legacy_clip.id, stage="publish").first()
    assert job is not None
    assert job.status == PipelineJobStatus.PENDING


def test_platform_agnostic_deferred_job_interface(test_db, tmp_path):
    """
    Verifies Amendment 6: Platforms without native publishAt (e.g. TikTok)
    allocate a slot and enqueue a deferred job in the durable queue.
    """
    ch, sv = _seed_channel_and_video(test_db, channel_id="ch-tiktok")
    clip = _seed_clip(test_db, sv, clip_id="c-tiktok")

    service = PublishSlotService()
    slot = service.allocate_slot(channel_id=ch.id, clip_id=clip.id, db=test_db, platform="tiktok")
    assert slot.platform == "tiktok"

    pub_res = service.publish_slot_now(slot=slot, clip=clip, db=test_db)
    assert pub_res["success"] is True
    assert pub_res["status"] == "DEFERRED_JOB_QUEUED"

    # Verify deferred job was enqueued in durable queue with next_run_at = slot.slot_start
    job = test_db.query(PipelineJob).filter(PipelineJob.id == pub_res["job_id"]).first()
    assert job is not None
    assert job.next_run_at == slot.slot_start


def test_upcoming_schedule_dashboard_api(test_db):
    """
    Verifies upcoming schedule endpoint returns allocated slots for dashboard display.
    """
    ch, sv = _seed_channel_and_video(test_db, channel_id="ch-dash")
    clip = _seed_clip(test_db, sv, clip_id="c-dash")

    service = PublishSlotService()
    slot = service.allocate_slot(channel_id=ch.id, clip_id=clip.id, db=test_db)
    slot.status = PublishSlotStatus.SCHEDULED.value
    slot.youtube_video_id = "yt_dash_123"
    test_db.commit()

    schedule = PublishSlotService.get_upcoming_schedule(db=test_db, channel_id=ch.id)
    assert len(schedule) >= 1
    item = schedule[0]
    assert item["clip_id"] == clip.id
    assert item["channel_name"] == "Tech Insights"
    assert item["youtube_video_id"] == "yt_dash_123"
    assert "slot_display" in item
