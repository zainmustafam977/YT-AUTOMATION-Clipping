import threading
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.models import Base, Clip, ClipCandidate, SourceVideo, Channel, ClipStatus
from shorts_engine.services.publishing.manager import MultiPlatformSyndicator, PlatformPublishResult


@pytest.fixture
def db_setup():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    channel = Channel(id="c1", youtube_channel_id="UC_1", name="Chan 1", watch_enabled=True, human_review_required=False)
    sv = SourceVideo(id="sv1", channel_id="c1", youtube_video_id="yt1", title="Title", duration_sec=60, status="DONE")
    cand = ClipCandidate(id="cand1", source_video_id="sv1", start_ms=0, end_ms=10000, score=0.9, reason="Test", selected=True)
    session.add(channel)
    session.add(sv)
    session.add(cand)
    session.commit()

    yield session, Session, engine
    session.close()


def test_syndicator_threads_do_not_share_session(db_setup, tmp_path):
    """
    Syndication worker threads must instantiate and use distinct scoped database sessions,
    never sharing a single parent session across threads (PLAN-P1-07 / F-29).
    """
    session, Session, engine = db_setup

    dummy_render = tmp_path / "clip.mp4"
    dummy_render.write_bytes(b"render")

    clip = Clip(
        id="clip_synd_test",
        clip_candidate_id="cand1",
        rendered_path=str(dummy_render),
        status=ClipStatus.APPROVED,
    )
    session.add(clip)
    session.commit()

    manager = MultiPlatformSyndicator(db_session=session)

    thread_sessions = {}
    thread_lock = threading.Lock()

    def mock_publish(video_path, title, description, tags, dry_run, **kwargs):
        tid = threading.get_ident()
        # Retrieve the session used in this worker thread (passed via kwargs or thread-local)
        used_session = kwargs.get("db_session")
        with thread_lock:
            thread_sessions[tid] = used_session
        return PlatformPublishResult(platform="mock", success=True, post_id="pid", dry_run=True)

    # Mock two platforms to trigger multi-threaded dispatch
    mock_pub1 = MagicMock()
    mock_pub1.publish.side_effect = mock_publish
    mock_pub2 = MagicMock()
    mock_pub2.publish.side_effect = mock_publish

    manager.publishers = {
        "platform_a": mock_pub1,
        "platform_b": mock_pub2,
    }

    with patch("shorts_engine.services.publishing.manager.SessionLocal", Session):
        manager.syndicate_clip(
            clip_id=clip.id,
            platforms=["platform_a", "platform_b"],
            dry_run=True,
        )

    # 1. Must have dispatched across distinct worker threads
    assert len(thread_sessions) == 2, f"Expected 2 distinct threads, got: {len(thread_sessions)}"

    # 2. In unhardened code, kwargs does NOT provide an isolated thread session (or passes shared session.db)
    # The sessions must not be None and must NOT be the parent session
    session_instances = list(thread_sessions.values())
    for s in session_instances:
        assert s is not None, "Worker thread was not provided an isolated database session"
        assert s is not session, "Worker thread shared the parent database session! Must be distinct scoped session."

    # 3. The sessions used by thread 1 and thread 2 must be different session instances
    assert session_instances[0] is not session_instances[1], "Both worker threads shared the exact same database session instance!"
