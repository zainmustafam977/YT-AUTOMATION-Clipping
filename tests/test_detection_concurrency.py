import pytest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from shorts_engine.db.models import Base, Channel, SourceVideo, SourceVideoStatus
from shorts_engine.services.detection.detector import DetectionService


@pytest.fixture
def db_setup():
    # Use SQLite with StaticPool so multiple sessions share the in-memory database
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    channel = Channel(
        id="chan_1",
        youtube_channel_id="UC_test_123",
        name="Test Channel",
        watch_enabled=True,
        human_review_required=True,
    )
    session.add(channel)
    session.commit()

    yield session, Session, engine
    session.close()


def test_concurrent_detection_catches_integrity_error_and_returns_existing(db_setup):
    """
    If another process inserts the SourceVideo between check and commit,
    IntegrityError must be caught, session rolled back, and the existing record returned.
    """
    session, Session, engine = db_setup
    service = DetectionService(session)

    # First video created normally
    v1 = service.on_new_video("UC_test_123", "vid_race_1")
    assert v1 is not None
    assert v1.youtube_video_id == "vid_race_1"

    def simulate_concurrent_winner_and_raise():
        # Concurrent worker inserts and commits vid_race_2
        s2 = Session()
        try:
            s2.add(SourceVideo(
                id="sv_racer_win",
                channel_id="chan_1",
                youtube_video_id="vid_race_2",
                title="Racer Video",
                status=SourceVideoStatus.DETECTED,
            ))
            s2.commit()
        finally:
            s2.close()
        raise IntegrityError("UNIQUE constraint failed: source_videos.youtube_video_id", params=[], orig=Exception())

    # We patch commit for the next call to simulate the concurrent racer winning
    with patch.object(session, "commit", side_effect=simulate_concurrent_winner_and_raise):
        result = service.on_new_video("UC_test_123", "vid_race_2")

    assert result is not None
    assert result.youtube_video_id == "vid_race_2"
    assert result.id == "sv_racer_win"


def test_idempotent_existing_video_returns_immediately(db_setup):
    """Calling on_new_video for an already registered video returns existing record."""
    session, _, _ = db_setup
    service = DetectionService(session)

    v1 = service.on_new_video("UC_test_123", "vid_existing_1")
    assert v1 is not None

    v2 = service.on_new_video("UC_test_123", "vid_existing_1")
    assert v2.id == v1.id
