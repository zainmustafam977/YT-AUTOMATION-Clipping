import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.db.models import Base, Channel
from shorts_engine.services.detection.rss_poller import RSSPoller, RSSPollLoop, FeedEntry


def test_rss_poller_advances_cursor_when_all_videos_are_shorts():
    """RSSPoller advances channel.last_seen_video_id even when all detected items are Shorts (PLAN-P2-04 / F-18)."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    channel = Channel(
        id="c1",
        name="Shorts Only Channel",
        youtube_channel_id="UC_shorts_only",
        watch_enabled=True,
        last_seen_video_id="old_vid_1",
    )
    db.add(channel)
    db.commit()

    poller = RSSPoller()

    # Two newest videos are both Shorts (#Shorts in title)
    fake_entries = [
        FeedEntry("short_vid_99", datetime.now(timezone.utc), title="Crazy Viral Trick #Shorts"),
        FeedEntry("short_vid_98", datetime.now(timezone.utc), title="Another Short Video #shorts"),
        FeedEntry("old_vid_1", datetime.now(timezone.utc), title="Old Long Video"),
    ]

    with patch.object(poller, "fetch_feed", return_value=fake_entries):
        new_ids = poller.poll_channel(channel, db)

        # None detected for long-form pipeline
        assert new_ids == []

        # But cursor MUST advance to the newest item in feed to prevent stuck poller loop
        db.refresh(channel)
        assert channel.last_seen_video_id == "short_vid_99", (
            f"Expected cursor to advance to 'short_vid_99', got: {channel.last_seen_video_id}"
        )


def test_rss_poll_loop_terminates_immediately_on_stop_event():
    """RSSPollLoop exits cleanly and immediately when stop() is called (PLAN-P2-05 / F-19)."""
    Session = MagicMock()
    stop_event = threading.Event()
    loop = RSSPollLoop(Session, on_new_video_callback=MagicMock(), stop_event=stop_event)

    # Signal stop before running
    loop.stop()
    assert loop.stop_event.is_set()

    # Run with large interval; must return immediately without blocking
    t_start = datetime.now()
    loop.run(interval_minutes=60)
    duration = (datetime.now() - t_start).total_seconds()
    assert duration < 2.0, "Poll loop must terminate immediately upon stop signal"
