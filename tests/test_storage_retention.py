"""
Tests for Automated Storage Retention Policy, Active Job Protection,
PathManager Deletions, and 80% Disk Capacity Alerting.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.config.settings import settings
from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    Channel,
    SourceVideo,
    SourceVideoStatus,
    ClipCandidate,
    Clip,
    ClipStatus,
    PublishLog,
    PipelineJob,
    PipelineJobStatus,
)
from shorts_engine.services.storage.path_manager import PathManager
from shorts_engine.services.storage.retention_service import RetentionPolicyService
from shorts_engine.services.monitoring.alerter import pipeline_alerter
from shorts_engine.orchestration.stage_registry import (
    StageContext,
    StageExecutor,
    PIPELINE,
)
from shorts_engine.services.queue.durable_queue import DurableJobQueue


@pytest.fixture
def test_db():
    """Provides an isolated in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def storage_env(tmp_path, monkeypatch):
    """Sets up an isolated temporary filesystem environment for storage tests."""
    storage_dir = tmp_path / "storage"
    downloads_dir = storage_dir / "downloads"
    outputs_dir = storage_dir / "outputs"
    temp_dir = storage_dir / "temp"
    crops_dir = storage_dir / "crops"

    for d in (storage_dir, downloads_dir, outputs_dir, temp_dir, crops_dir):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(settings, "STORAGE_DIR", storage_dir)
    monkeypatch.setattr(settings, "OUTPUT_DIR", outputs_dir)
    monkeypatch.setattr(settings, "TEMP_DIR", temp_dir)
    monkeypatch.setattr(settings, "RETENTION_RAW_SOURCE_DAYS", 14)
    monkeypatch.setattr(settings, "RETENTION_INTERMEDIATES_DAYS", 3)
    monkeypatch.setattr(settings, "RETENTION_KEEP_FINAL_RENDERS", True)
    monkeypatch.setattr(settings, "DISK_ALERT_THRESHOLD_PCT", 80.0)

    return {
        "storage_dir": storage_dir,
        "downloads_dir": downloads_dir,
        "outputs_dir": outputs_dir,
        "temp_dir": temp_dir,
        "crops_dir": crops_dir,
    }


def _seed_channel_and_video(db, video_id="video-1", title="Test Video"):
    ch = Channel(
        id=f"ch-{video_id}",
        youtube_channel_id=f"UC_{video_id}",
        name="Test Channel",
    )
    db.add(ch)
    db.commit()

    sv = SourceVideo(
        id=f"sv-{video_id}",
        channel_id=ch.id,
        youtube_video_id=video_id,
        title=title,
        status=SourceVideoStatus.DONE,
    )
    db.add(sv)
    db.commit()
    return ch, sv


# ── 1. Aged Raw Sources Purge (>14 Days Post-Publication) ─────────────────────

def test_aged_raw_source_purged_when_all_clips_published(test_db, storage_env):
    """
    Verifies that raw source video and audio files are safely purged and DB row
    marked 'PURGED' when all associated clips were published >= 14 days ago.
    """
    ch, sv = _seed_channel_and_video(test_db, video_id="v-aged-1")

    # Create dummy files on disk
    raw_video = storage_env["downloads_dir"] / "test_ch" / "v-aged-1" / "raw_video.mp4"
    raw_audio = storage_env["downloads_dir"] / "test_ch" / "v-aged-1" / "raw_audio.wav"
    raw_proxy = storage_env["downloads_dir"] / "test_ch" / "v-aged-1" / "raw_video_proxy_preview.mp4"
    raw_video.parent.mkdir(parents=True, exist_ok=True)
    raw_video.write_bytes(b"VIDEO CONTENT " * 100)
    raw_audio.write_bytes(b"AUDIO CONTENT " * 50)
    raw_proxy.write_bytes(b"PROXY CONTENT " * 20)

    sv.downloaded_path = str(raw_video)
    sv.audio_path = str(raw_audio)
    test_db.commit()

    # Create 2 candidates and 2 published clips published 15 days ago
    aged_pub_date = datetime.utcnow() - timedelta(days=15)
    for i in (1, 2):
        cand = ClipCandidate(
            id=f"cand-{sv.id}-{i}",
            source_video_id=sv.id,
            start_ms=i * 10000,
            end_ms=(i + 1) * 10000,
            score=9.0,
            reason="High viral potential",
        )
        test_db.add(cand)
        test_db.commit()

        clip = Clip(
            id=f"clip-{sv.id}-{i}",
            clip_candidate_id=cand.id,
            status=ClipStatus.PUBLISHED,
        )
        test_db.add(clip)
        test_db.commit()

        p_log = PublishLog(
            clip_id=clip.id,
            youtube_video_id=f"yt-{clip.id}",
            published_at=aged_pub_date,
            title="Aged Published Clip",
            description="Published 15 days ago",
            status="SUCCESS",
        )
        test_db.add(p_log)
        test_db.commit()

    # Run raw source purge
    reports = RetentionPolicyService.purge_eligible_raw_sources(test_db)
    assert len(reports) == 1
    assert reports[0]["source_video_id"] == sv.id
    assert reports[0]["freed_bytes"] > 0

    # Verify files were deleted on disk
    assert not raw_video.exists()
    assert not raw_audio.exists()
    assert not raw_proxy.exists()

    # Verify DB row updated to 'PURGED' with paths cleared
    test_db.refresh(sv)
    assert sv.status == SourceVideoStatus.PURGED
    assert sv.downloaded_path is None
    assert sv.audio_path is None


# ── 2. Raw Sources Preserved If Within Retention Window (<14 Days) ────────────

def test_raw_source_survives_if_recently_published(test_db, storage_env):
    """
    Verifies that raw source video survives if clips were published recently (< 14 days ago).
    """
    ch, sv = _seed_channel_and_video(test_db, video_id="v-recent-1")

    raw_video = storage_env["downloads_dir"] / "recent_video.mp4"
    raw_video.write_bytes(b"VIDEO CONTENT")
    sv.downloaded_path = str(raw_video)
    test_db.commit()

    cand = ClipCandidate(
        source_video_id=sv.id,
        start_ms=0,
        end_ms=15000,
        score=8.5,
        reason="Good hook",
    )
    test_db.add(cand)
    test_db.commit()

    clip = Clip(clip_candidate_id=cand.id, status=ClipStatus.PUBLISHED)
    test_db.add(clip)
    test_db.commit()

    # Published 5 days ago (< 14 days)
    p_log = PublishLog(
        clip_id=clip.id,
        youtube_video_id="yt-recent",
        published_at=datetime.utcnow() - timedelta(days=5),
        title="Recent Clip",
        description="5 days ago",
        status="SUCCESS",
    )
    test_db.add(p_log)
    test_db.commit()

    reports = RetentionPolicyService.purge_eligible_raw_sources(test_db)
    assert len(reports) == 0

    # Files and DB row remain intact
    assert raw_video.exists()
    test_db.refresh(sv)
    assert sv.status != SourceVideoStatus.PURGED
    assert sv.downloaded_path == str(raw_video)


# ── 3. Raw Sources Preserved If Any Clip Is Not Yet Published ─────────────────

def test_raw_source_survives_if_clip_pending_approval(test_db, storage_env):
    """
    Verifies that raw source survives if one clip is published but another is still pending approval.
    """
    ch, sv = _seed_channel_and_video(test_db, video_id="v-mixed-1")

    raw_video = storage_env["downloads_dir"] / "mixed_video.mp4"
    raw_video.write_bytes(b"VIDEO CONTENT")
    sv.downloaded_path = str(raw_video)
    test_db.commit()

    # Clip 1: Published 20 days ago
    cand1 = ClipCandidate(source_video_id=sv.id, start_ms=0, end_ms=10000, score=9.0, reason="Hook 1")
    test_db.add(cand1)
    test_db.commit()
    clip1 = Clip(clip_candidate_id=cand1.id, status=ClipStatus.PUBLISHED)
    test_db.add(clip1)
    test_db.commit()
    test_db.add(PublishLog(
        clip_id=clip1.id,
        published_at=datetime.utcnow() - timedelta(days=20),
        title="Clip 1",
        description="",
    ))
    test_db.commit()

    # Clip 2: Waiting for approval (not published!)
    cand2 = ClipCandidate(source_video_id=sv.id, start_ms=10000, end_ms=20000, score=8.0, reason="Hook 2")
    test_db.add(cand2)
    test_db.commit()
    clip2 = Clip(clip_candidate_id=cand2.id, status=ClipStatus.WAITING_APPROVAL)
    test_db.add(clip2)
    test_db.commit()

    reports = RetentionPolicyService.purge_eligible_raw_sources(test_db)
    assert len(reports) == 0

    assert raw_video.exists()
    test_db.refresh(sv)
    assert sv.status != SourceVideoStatus.PURGED


# ── 4. Active Job Protection Guard (Immunity for Pending/Running Jobs) ─────────

def test_raw_source_protected_by_active_pipeline_job(test_db, storage_env):
    """
    Verifies that even if an asset is 100 days old and all clips published,
    if a pending or running PipelineJob references it, it is NEVER deleted.
    """
    ch, sv = _seed_channel_and_video(test_db, video_id="v-protected-1")

    raw_video = storage_env["downloads_dir"] / "protected_video.mp4"
    raw_video.write_bytes(b"PROTECTED CONTENT")
    sv.downloaded_path = str(raw_video)
    test_db.commit()

    cand = ClipCandidate(source_video_id=sv.id, start_ms=0, end_ms=10000, score=9.2, reason="Viral hook")
    test_db.add(cand)
    test_db.commit()
    clip = Clip(clip_candidate_id=cand.id, status=ClipStatus.PUBLISHED)
    test_db.add(clip)
    test_db.commit()
    test_db.add(PublishLog(
        clip_id=clip.id,
        published_at=datetime.utcnow() - timedelta(days=60),
        title="Old Clip",
        description="",
    ))
    test_db.commit()

    # Seed an active running job referencing this source video
    active_job = PipelineJob(
        id="job-active-transcode",
        job_type="PROCESS_VIDEO",
        entity_type="source_video",
        entity_id=sv.id,
        status=PipelineJobStatus.RUNNING,
        stage="reframe",
    )
    test_db.add(active_job)
    test_db.commit()

    # Protection check
    protected = RetentionPolicyService.get_active_protected_entity_ids(test_db)
    assert sv.id in protected

    reports = RetentionPolicyService.purge_eligible_raw_sources(test_db)
    assert len(reports) == 0

    assert raw_video.exists()
    test_db.refresh(sv)
    assert sv.status != SourceVideoStatus.PURGED
    assert sv.downloaded_path == str(raw_video)


# ── 5. Intermediate Assets Purge (>3 Days Post-Publication) ───────────────────

def test_intermediates_purged_while_final_render_preserved(test_db, storage_env):
    """
    Verifies that intermediate crop plans (.json) and subtitles (.ass) are deleted
    after the clip is published for >= 3 days, while final renders are preserved.
    """
    ch, sv = _seed_channel_and_video(test_db, video_id="v-inter-1")

    cand = ClipCandidate(
        source_video_id=sv.id,
        start_ms=0,
        end_ms=15000,
        score=9.5,
        reason="Super hook",
    )
    test_db.add(cand)
    test_db.commit()

    # Final rendered clip file
    final_render = storage_env["outputs_dir"] / "test_ch" / "v-inter-1" / "clips" / "final_clip.mp4"
    final_render.parent.mkdir(parents=True, exist_ok=True)
    final_render.write_bytes(b"FINAL RENDER 1080x1920 MP4")

    clip = Clip(
        id="clip-inter-1",
        clip_candidate_id=cand.id,
        rendered_path=str(final_render),
        status=ClipStatus.PUBLISHED,
    )
    test_db.add(clip)
    test_db.commit()

    # Published 5 days ago (> 3 days)
    test_db.add(PublishLog(
        clip_id=clip.id,
        published_at=datetime.utcnow() - timedelta(days=5),
        title="Published Clip",
        description="",
    ))
    test_db.commit()

    # Intermediate crop plan JSON
    crop_plan = storage_env["crops_dir"] / f"{clip.id}_crop_plan.json"
    crop_plan.write_text(json.dumps({"box": [0, 0, 100, 100]}))

    # Intermediate subtitle .ass file
    sub_file = PathManager.get_subtitle_path("v-inter-1", 0, 15000)
    sub_file.write_text("[Script Info]\nTitle: Test Subtitles")

    assert crop_plan.exists()
    assert sub_file.exists()
    assert final_render.exists()

    # Purge intermediates
    reports = RetentionPolicyService.purge_eligible_intermediates(test_db)
    assert len(reports) == 1
    assert reports[0]["clip_id"] == clip.id
    assert reports[0]["freed_bytes"] > 0

    # Intermediates were purged
    assert not crop_plan.exists()
    assert not sub_file.exists()

    # CRITICAL: Final render MUST be preserved!
    assert final_render.exists()
    test_db.refresh(clip)
    assert clip.rendered_path == str(final_render)


# ── 6. Intermediates Preserved If Recently Published (<3 Days) ────────────────

def test_intermediates_survive_if_recently_published(test_db, storage_env):
    """
    Verifies that intermediate files survive if published < 3 days ago.
    """
    ch, sv = _seed_channel_and_video(test_db, video_id="v-inter-recent")
    cand = ClipCandidate(source_video_id=sv.id, start_ms=0, end_ms=12000, score=8.0, reason="Hook")
    test_db.add(cand)
    test_db.commit()

    clip = Clip(id="clip-recent-inter", clip_candidate_id=cand.id, status=ClipStatus.PUBLISHED)
    test_db.add(clip)
    test_db.commit()

    # Published 1 day ago (< 3 days)
    test_db.add(PublishLog(
        clip_id=clip.id,
        published_at=datetime.utcnow() - timedelta(days=1),
        title="Recent",
        description="",
    ))
    test_db.commit()

    crop_plan = storage_env["crops_dir"] / f"{clip.id}_crop_plan.json"
    crop_plan.write_text(json.dumps({"test": 1}))

    reports = RetentionPolicyService.purge_eligible_intermediates(test_db)
    assert len(reports) == 0
    assert crop_plan.exists()


# ── 7. Disk Capacity Alerting (>=80% Threshold) ────────────────────────────────

def test_disk_capacity_alert_fires_at_80_percent(test_db):
    """
    Verifies that when disk capacity meets or exceeds 80%, an alert is dispatched
    via pipeline_alerter.
    """
    alerts_fired = []

    def mock_alert_disk_space(percent_used, free_gb, total_gb, path, wait=False):
        alerts_fired.append({
            "percent_used": percent_used,
            "free_gb": free_gb,
            "total_gb": total_gb,
            "path": path,
        })
        return True

    # Mock shutil.disk_usage to return 85% used (170 GB used of 200 GB)
    total_bytes = 200 * (1024 ** 3)
    used_bytes = 170 * (1024 ** 3)
    free_bytes = 30 * (1024 ** 3)

    with patch("shutil.disk_usage", return_value=(total_bytes, used_bytes, free_bytes)):
        with patch.object(pipeline_alerter, "alert_disk_space", side_effect=mock_alert_disk_space):
            metric = RetentionPolicyService.check_disk_and_alert(test_db)

            assert metric["percent_used"] == 85.0
            assert metric["is_critical"] is True
            assert len(alerts_fired) == 1
            assert alerts_fired[0]["percent_used"] == 85.0


# ── 8. Daily Queue Task Execution & Self-Rescheduling ─────────────────────────

def test_daily_cleanup_queue_stage_execution(test_db, storage_env):
    """
    Verifies that cleanup executes as a registered stage within StageExecutor,
    purges aged assets, updates DB rows, and enqueues tomorrow's daily_cleanup job.
    """
    ch, sv = _seed_channel_and_video(test_db, video_id="v-queue-clean")

    raw_video = storage_env["downloads_dir"] / "v_clean.mp4"
    raw_video.write_bytes(b"CLEAN CONTENT")
    sv.downloaded_path = str(raw_video)
    test_db.commit()

    cand = ClipCandidate(source_video_id=sv.id, start_ms=0, end_ms=10000, score=9.0, reason="Clean hook")
    test_db.add(cand)
    test_db.commit()
    clip = Clip(clip_candidate_id=cand.id, status=ClipStatus.PUBLISHED)
    test_db.add(clip)
    test_db.commit()
    test_db.add(PublishLog(
        clip_id=clip.id,
        published_at=datetime.utcnow() - timedelta(days=20),
        title="Old",
        description="",
    ))
    test_db.commit()

    # Create cleanup job in queue
    queue = DurableJobQueue()
    cleanup_job = queue.enqueue(
        job_type="daily_cleanup",
        entity_id="system_maintenance",
        entity_type="system",
        stage="daily_cleanup",
        db=test_db,
    )

    ctx = StageContext(
        db_session=test_db,
        job_id=cleanup_job.id,
        payload={"action": "retention_cleanup"},
    )

    executor = StageExecutor(pipeline=PIPELINE)
    res = executor.execute_stage("daily_cleanup", ctx, job=cleanup_job)

    assert res["raw_sources_purged"] == 1
    assert not raw_video.exists()

    test_db.refresh(sv)
    assert sv.status == SourceVideoStatus.PURGED
    assert sv.downloaded_path is None

    # Verify self-scheduled next daily cleanup job exists
    next_jobs = (
        test_db.query(PipelineJob)
        .filter(
            PipelineJob.stage == "daily_cleanup",
            PipelineJob.status == PipelineJobStatus.PENDING,
        )
        .all()
    )
    assert len(next_jobs) >= 1
    scheduled_job = next_jobs[-1]
    assert scheduled_job.next_run_at is not None
    assert scheduled_job.next_run_at > datetime.utcnow()
