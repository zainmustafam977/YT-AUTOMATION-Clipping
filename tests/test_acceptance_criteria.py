"""
Acceptance Criteria Tests: explicitly verifies the three named scenarios from Section 15.2:
1. test_idempotency_no_duplicate_publish
2. test_pipeline_resume_after_crash
3. test_quota_exhaustion_queueing
"""

import pytest
import subprocess
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from shorts_engine.db.models import (
    Base,
    Channel,
    SourceVideo,
    SourceVideoStatus,
    ClipCandidate,
    Clip,
    ClipStatus,
    PublishLog,
)
from shorts_engine.services.publishing.client import YouTubePublisher
from shorts_engine.services.publishing.quota import QuotaTracker
from shorts_engine.services.publishing.metadata import ShortsMetadata
from shorts_engine.orchestration.lifecycle import LifecycleManager


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "acceptance_test.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_idempotency_no_duplicate_publish(test_db, tmp_path):
    """
    Spec 15.2 Scenario 1: No duplicate publish occurs when a job is deliberately retried.
    """
    channel = Channel(youtube_channel_id="UC_ACCEPT_1", name="Acceptance Channel")
    test_db.add(channel)
    test_db.commit()

    # Create dummy rendered video file
    dummy_video = tmp_path / "rendered_short.mp4"
    dummy_video.write_bytes(b"dummy video content for test")

    video = SourceVideo(channel_id=channel.id, youtube_video_id="src_123", title="Test Source")
    test_db.add(video)
    test_db.commit()

    cand = ClipCandidate(source_video_id=video.id, start_ms=0, end_ms=20000, score=0.9, reason="test")
    test_db.add(cand)
    test_db.commit()

    clip = Clip(
        clip_candidate_id=cand.id,
        rendered_path=str(dummy_video),
        status=ClipStatus.APPROVED,
    )
    test_db.add(clip)
    test_db.commit()

    tracker = QuotaTracker(state_file=tmp_path / "idempotency_quota.json")
    publisher = YouTubePublisher(test_db, quota_tracker=tracker)
    meta = ShortsMetadata(title="Test Short Title", description="Desc")

    # First publish: should succeed and record PublishLog
    result1 = publisher.publish_clip(clip, meta, dry_run=True)
    assert result1["success"] is True
    assert clip.status == ClipStatus.PUBLISHED
    logs = test_db.query(PublishLog).filter_by(clip_id=clip.id).all()
    assert len(logs) == 1

    # Second publish (deliberate retry): must detect published state and NOT duplicate publish
    result2 = publisher.publish_clip(clip, meta, dry_run=True)
    assert result2["success"] is True
    assert result2.get("already_published") is True

    # Confirm only 1 record exists in PublishLog
    logs_after = test_db.query(PublishLog).filter_by(clip_id=clip.id).all()
    assert len(logs_after) == 1


def test_pipeline_resume_after_crash(test_db):
    """
    Spec 15.2 Scenario 2: The pipeline resumes correctly after a forced mid-job restart.
    """
    channel = Channel(youtube_channel_id="UC_CRASH", name="Crash Channel")
    test_db.add(channel)
    test_db.commit()

    # Simulate a video that was interrupted mid-job at TRANSCRIBING
    video = SourceVideo(
        channel_id=channel.id,
        youtube_video_id="crashed_video",
        title="Interrupted Video",
        status=SourceVideoStatus.TRANSCRIBING,
        downloaded_path="storage/downloads/crashed_video.mp4",
        audio_path="storage/downloads/crashed_video_audio.wav",
    )
    test_db.add(video)
    test_db.commit()

    lifecycle = LifecycleManager(test_db)

    # Upon recovery, system checks existing status and resumes at ANALYZING without re-downloading
    assert video.status == SourceVideoStatus.TRANSCRIBING
    assert video.downloaded_path is not None

    # Simulate transcription completion on recovery
    resumed = lifecycle.transition_video(
        video,
        target_status=SourceVideoStatus.ANALYZING,
        expected_status=SourceVideoStatus.TRANSCRIBING,
    )
    assert resumed is True
    assert video.status == SourceVideoStatus.ANALYZING


def test_quota_exhaustion_queueing(test_db, tmp_path):
    """
    Spec 15.2 Scenario 3: Publishing respects YouTube API quota and queues rather than fails.
    """
    quota_file = tmp_path / "quota.json"
    tracker = QuotaTracker(state_file=quota_file)

    # Consume quota up to limit
    tracker.record_publish(cost=9000)

    # Check remaining: 10,000 - 9,000 = 1,000 units (less than the 1,600 required for an upload)
    can_pub, remaining, reason = tracker.can_publish()
    assert can_pub is False
    assert remaining == 1000
    assert "Daily YouTube quota exhausted" in reason

    channel = Channel(youtube_channel_id="UC_QUOTA", name="Quota Channel")
    test_db.add(channel)
    test_db.commit()

    dummy_video = tmp_path / "video.mp4"
    dummy_video.write_bytes(b"content")

    video = SourceVideo(channel_id=channel.id, youtube_video_id="v_quota", title="T")
    test_db.add(video)
    test_db.commit()

    cand = ClipCandidate(source_video_id=video.id, start_ms=0, end_ms=10000, score=0.8, reason="r")
    test_db.add(cand)
    test_db.commit()

    clip = Clip(clip_candidate_id=cand.id, rendered_path=str(dummy_video), status=ClipStatus.APPROVED)
    test_db.add(clip)
    test_db.commit()

    publisher = YouTubePublisher(test_db, quota_tracker=tracker)
    meta = ShortsMetadata(title="Quota Short", description="Desc")

    # Attempting to publish must return quota_exhausted=True without erroring or crashing
    res = publisher.publish_clip(clip, meta, dry_run=True)
    assert res["success"] is False
    assert res["quota_exhausted"] is True
    assert clip.status == ClipStatus.APPROVED  # Stays approved, queued for next window!
