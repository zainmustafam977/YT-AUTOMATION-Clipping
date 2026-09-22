"""
Unit tests for data models, constraints, and relationships.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError
from shorts_engine.db.models import (
    Base,
    Channel,
    SourceVideo,
    SourceVideoStatus,
    Transcript,
    ClipCandidate,
    Clip,
    ClipStatus,
    PublishLog,
    SFXLibrary,
)


@pytest.fixture
def db_session():
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestingSessionLocal = sessionmaker(bind=test_engine)
    session = TestingSessionLocal()
    yield session
    session.close()


def test_channel_creation_and_video_duplicate_prevention(db_session):
    channel = Channel(
        youtube_channel_id="UC1234567890",
        name="Tech Creator",
        watch_enabled=True,
        human_review_required=True,
    )
    db_session.add(channel)
    db_session.commit()

    video1 = SourceVideo(
        channel_id=channel.id,
        youtube_video_id="vid_abc_123",
        title="Epic Video 1",
        duration_sec=600,
        status=SourceVideoStatus.DETECTED,
    )
    db_session.add(video1)
    db_session.commit()

    # Attempting to add the same video ID for the same channel must raise IntegrityError
    video_dup = SourceVideo(
        channel_id=channel.id,
        youtube_video_id="vid_abc_123",
        title="Duplicate Video",
        duration_sec=600,
    )
    db_session.add(video_dup)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_reconciled_clip_status_and_lifecycle(db_session):
    channel = Channel(youtube_channel_id="UC999", name="Test Channel")
    db_session.add(channel)
    db_session.commit()

    video = SourceVideo(
        channel_id=channel.id,
        youtube_video_id="vid_test_001",
        title="Podcast Interview",
        duration_sec=1200,
    )
    db_session.add(video)
    db_session.commit()

    candidate = ClipCandidate(
        source_video_id=video.id,
        start_ms=10000,
        end_ms=45000,
        score=0.92,
        reason="Compelling hook with punchline",
        selected=True,
    )
    db_session.add(candidate)
    db_session.commit()

    # Verify ClipStatus enum supports QUALITY_CHECK and WAITING_APPROVAL
    clip = Clip(
        clip_candidate_id=candidate.id,
        status=ClipStatus.QUALITY_CHECK,
    )
    db_session.add(clip)
    db_session.commit()
    assert clip.status == ClipStatus.QUALITY_CHECK

    # Transition to WAITING_APPROVAL
    clip.status = ClipStatus.WAITING_APPROVAL
    db_session.commit()
    assert clip.status == ClipStatus.WAITING_APPROVAL

    # Transition to APPROVED
    clip.status = ClipStatus.APPROVED
    db_session.commit()
    assert clip.status == ClipStatus.APPROVED


def test_publish_log_duplicate_prevention(db_session):
    channel = Channel(youtube_channel_id="UC_PUB", name="Publisher")
    db_session.add(channel)
    db_session.commit()

    video = SourceVideo(channel_id=channel.id, youtube_video_id="v_pub", title="V")
    db_session.add(video)
    db_session.commit()

    candidate = ClipCandidate(source_video_id=video.id, start_ms=0, end_ms=30000, score=0.8, reason="ok")
    db_session.add(candidate)
    db_session.commit()

    clip = Clip(clip_candidate_id=candidate.id, status=ClipStatus.APPROVED)
    db_session.add(clip)
    db_session.commit()

    log1 = PublishLog(
        clip_id=clip.id,
        youtube_video_id="short_yt_123",
        title="Short 1",
        description="Desc",
        status="SUCCESS",
    )
    db_session.add(log1)
    db_session.commit()

    # Attempting to log a second publish with the exact same clip and youtube_video_id must fail
    log_dup = PublishLog(
        clip_id=clip.id,
        youtube_video_id="short_yt_123",
        title="Short Dup",
        description="Desc",
    )
    db_session.add(log_dup)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
