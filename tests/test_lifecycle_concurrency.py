import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from shorts_engine.db.models import Base, SourceVideo, SourceVideoStatus, Clip, ClipStatus, ClipCandidate
from shorts_engine.orchestration.lifecycle import LifecycleManager


@pytest.fixture
def db_engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def test_concurrent_transition_only_one_succeeds(db_engine):
    """Verify that when two managers attempt to transition from the same expected status, only one succeeds."""
    SessionLocal = sessionmaker(bind=db_engine)
    
    # Session 1 creates video in DOWNLOADING
    session1 = SessionLocal()
    video1 = SourceVideo(
        id="vid-test-01",
        youtube_video_id="test01",
        title="Test Video",
        channel_id="chan-01",
        status=SourceVideoStatus.DOWNLOADING,
    )
    session1.add(video1)
    session1.commit()

    # Session 2 loads the same video
    session2 = SessionLocal()
    video2 = session2.query(SourceVideo).filter(SourceVideo.id == "vid-test-01").one()

    manager1 = LifecycleManager(session1)
    manager2 = LifecycleManager(session2)

    # Manager 1 transitions from DOWNLOADING -> TRANSCRIBING
    res1 = manager1.transition_video(
        video1,
        target_status=SourceVideoStatus.TRANSCRIBING,
        expected_status=SourceVideoStatus.DOWNLOADING,
    )
    assert res1 is True
    assert video1.status == SourceVideoStatus.TRANSCRIBING

    # Manager 2 attempts same transition from DOWNLOADING -> TRANSCRIBING
    # Since DB is already TRANSCRIBING, manager2 must fail!
    res2 = manager2.transition_video(
        video2,
        target_status=SourceVideoStatus.TRANSCRIBING,
        expected_status=SourceVideoStatus.DOWNLOADING,
    )
    assert res2 is False
    assert video2.status == SourceVideoStatus.TRANSCRIBING

    session1.close()
    session2.close()


def test_transition_clip_concurrency_only_one_succeeds(db_engine):
    """Verify that concurrent clip transition only succeeds once."""
    SessionLocal = sessionmaker(bind=db_engine)
    
    session1 = SessionLocal()
    video = SourceVideo(
        id="vid-clip-test",
        youtube_video_id="clipvid01",
        title="Test Clip Video",
        channel_id="chan-01",
        status=SourceVideoStatus.DONE,
    )
    session1.add(video)
    session1.commit()

    cand1 = ClipCandidate(
        id="cand-test-01",
        source_video_id="vid-clip-test",
        start_ms=0,
        end_ms=15000,
        score=0.9,
        reason="high viral hook",
        selected=True,
    )
    session1.add(cand1)
    session1.commit()

    clip1 = Clip(
        id="clip-test-01",
        clip_candidate_id="cand-test-01",
        status=ClipStatus.WAITING_APPROVAL,
    )
    session1.add(clip1)
    session1.commit()

    session2 = SessionLocal()
    clip2 = session2.query(Clip).filter(Clip.id == "clip-test-01").one()

    manager1 = LifecycleManager(session1)
    manager2 = LifecycleManager(session2)

    # Manager 1 transitions WAITING_APPROVAL -> APPROVED
    res1 = manager1.transition_clip(
        clip1,
        target_status=ClipStatus.APPROVED,
        expected_status=ClipStatus.WAITING_APPROVAL,
    )
    assert res1 is True
    assert clip1.status == ClipStatus.APPROVED

    # Manager 2 attempts WAITING_APPROVAL -> REJECTED
    res2 = manager2.transition_clip(
        clip2,
        target_status=ClipStatus.REJECTED,
        expected_status=ClipStatus.WAITING_APPROVAL,
    )
    assert res2 is False
    assert clip2.status == ClipStatus.APPROVED

    session1.close()
    session2.close()
