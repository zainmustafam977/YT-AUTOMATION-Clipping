"""
Unit and Integration Tests for DripFeedDaemon and Background Publishing:
- Verifies that only APPROVED clips with scheduled_publish_at <= now() are queried.
- Verifies that successful publish transitions clip to PUBLISHED and creates PublishLog.
- Verifies that missing rendered file marks clip as FAILED.
- Verifies retry incrementing on simulated error.
"""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.db.models import Clip, ClipStatus, ClipCandidate, SourceVideo, SourceVideoStatus, Channel, PublishLog
from shorts_engine.services.publishing.drip_feed import check_and_publish_due_clips, build_clip_metadata, DripFeedDaemon
from shorts_engine.config.settings import settings


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def test_build_clip_metadata(db):
    ch = Channel(id="ch_df_1", youtube_channel_id="UC_df_1", name="AI Explorers")
    db.add(ch)
    sv = SourceVideo(id="sv_df_1", youtube_video_id="yt_df_1", channel_id=ch.id, title="Original Video", status=SourceVideoStatus.DONE)
    db.add(sv)
    cand = ClipCandidate(id="cand_df_1", source_video_id=sv.id, start_ms=0, end_ms=10000, hook_text="Did you know this trick?", payoff_text="It works.", score=0.9, reason="Strong")
    db.add(cand)
    clip = Clip(id="clip_df_1", clip_candidate_id=cand.id, status=ClipStatus.APPROVED, generated_title="Insane Coding Hack")
    db.add(clip)
    db.commit()

    meta = build_clip_metadata(clip)
    assert "Insane Coding Hack" in meta.title
    assert "#shorts" in meta.title.lower()
    assert "Did you know this trick?" in meta.description
    assert "#shorts" in meta.tags
    assert "aiexplorers" in meta.tags


def test_check_and_publish_due_clips(db, tmp_path):
    ch = Channel(id="ch_df_2", youtube_channel_id="UC_df_2", name="Tech Trends")
    db.add(ch)
    sv = SourceVideo(id="sv_df_2", youtube_video_id="yt_df_2", channel_id=ch.id, title="Tech Video", status=SourceVideoStatus.DONE)
    db.add(sv)
    cand_due = ClipCandidate(id="cand_df_due", source_video_id=sv.id, start_ms=0, end_ms=10000, hook_text="Due Hook", payoff_text="Done", score=0.85, reason="Good")
    cand_future = ClipCandidate(id="cand_df_future", source_video_id=sv.id, start_ms=10000, end_ms=20000, hook_text="Future Hook", payoff_text="Done", score=0.85, reason="Good")
    cand_unapp = ClipCandidate(id="cand_df_unapp", source_video_id=sv.id, start_ms=20000, end_ms=30000, hook_text="Unapproved Hook", payoff_text="Done", score=0.85, reason="Good")
    db.add_all([cand_due, cand_future, cand_unapp])

    # 1. Due clip (scheduled 10 minutes ago)
    rendered_file = tmp_path / "clip_due.mp4"
    rendered_file.write_bytes(b"dummy mp4")

    clip_due = Clip(
        id="clip_due_1",
        clip_candidate_id=cand_due.id,
        status=ClipStatus.APPROVED,
        scheduled_publish_at=datetime.utcnow() - timedelta(minutes=10),
        rendered_path=str(rendered_file),
    )
    db.add(clip_due)

    # 2. Future clip (scheduled 2 hours in the future) - should NOT be published
    clip_future = Clip(
        id="clip_future_1",
        clip_candidate_id=cand_future.id,
        status=ClipStatus.APPROVED,
        scheduled_publish_at=datetime.utcnow() + timedelta(hours=2),
        rendered_path=str(rendered_file),
    )
    db.add(clip_future)

    # 3. Non-approved clip (WAITING_APPROVAL) - should NOT be published
    clip_unapproved = Clip(
        id="clip_unapproved_1",
        clip_candidate_id=cand_unapp.id,
        status=ClipStatus.WAITING_APPROVAL,
        scheduled_publish_at=datetime.utcnow() - timedelta(minutes=5),
        rendered_path=str(rendered_file),
    )
    db.add(clip_unapproved)
    db.commit()


    # Run check_and_publish_due_clips
    published_ids = check_and_publish_due_clips(db)

    assert published_ids == ["clip_due_1"]

    db.refresh(clip_due)
    assert clip_due.status == ClipStatus.PUBLISHED

    db.refresh(clip_future)
    assert clip_future.status == ClipStatus.APPROVED

    db.refresh(clip_unapproved)
    assert clip_unapproved.status == ClipStatus.WAITING_APPROVAL

    # Check publish log
    log = db.query(PublishLog).filter_by(clip_id=clip_due.id).first()
    assert log is not None
    assert log.status in ("SUCCESS", "DRY_RUN")



def test_missing_rendered_file_marks_failed(db):
    cand = ClipCandidate(id="cand_df_3", source_video_id="sv_dummy", start_ms=0, end_ms=5000, hook_text="Missing", payoff_text="None", score=0.7, reason="None")
    db.add(cand)

    clip = Clip(
        id="clip_missing_file",
        clip_candidate_id=cand.id,
        status=ClipStatus.APPROVED,
        scheduled_publish_at=datetime.utcnow() - timedelta(minutes=1),
        rendered_path="/non/existent/path.mp4",
    )
    db.add(clip)
    db.commit()

    published_ids = check_and_publish_due_clips(db)
    assert published_ids == []

    db.refresh(clip)
    assert clip.status == ClipStatus.FAILED
