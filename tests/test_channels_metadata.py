"""
Tests for channel video metadata enrichment, relative formatting, duration formatting,
view formatting, and FeedEntry backwards-compatible tuple behavior.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from shorts_engine.dashboard.routes.channels import (
    _format_duration,
    _format_views,
    _format_relative_time,
)
from shorts_engine.services.detection.rss_poller import FeedEntry, RSSPoller
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.routes import channels as channels_route
from shorts_engine.dashboard.auth import require_operator, require_viewer, verify_csrf
from shorts_engine.db.models import Channel, User, UserRole
from shorts_engine.db.session import Base


# ---------------------------------------------------------------------------
# Formatting Unit Tests
# ---------------------------------------------------------------------------

def test_format_duration():
    assert _format_duration(None) is None
    assert _format_duration("invalid") is None
    assert _format_duration(0) == "0:00"
    assert _format_duration(45) == "0:45"
    assert _format_duration(75) == "1:15"
    assert _format_duration(599) == "9:59"
    assert _format_duration(3600) == "1:00:00"
    assert _format_duration(3665) == "1:01:05"
    assert _format_duration(7200 + 120 + 3) == "2:02:03"


def test_format_views():
    assert _format_views(None) is None
    assert _format_views("invalid") is None
    assert _format_views(0) == "0 views"
    assert _format_views(850) == "850 views"
    assert _format_views(1200) == "1.2K views"
    assert _format_views(45000) == "45.0K views"
    assert _format_views(1500000) == "1.5M views"
    assert _format_views(2500000000) == "2.5B views"


def test_format_relative_time():
    assert _format_relative_time(None) == ""

    now = datetime.now(timezone.utc)
    assert _format_relative_time(now - timedelta(seconds=20)) == "20s ago"
    assert _format_relative_time(now - timedelta(minutes=5)) == "5m ago"
    assert _format_relative_time(now - timedelta(hours=3)) == "3h ago"
    assert _format_relative_time(now - timedelta(days=1)) == "yesterday"
    assert _format_relative_time(now - timedelta(days=4)) == "4d ago"
    assert _format_relative_time(now - timedelta(days=14)) == "2w ago"
    assert _format_relative_time(now - timedelta(days=65)) == "2mo ago"
    assert _format_relative_time(now - timedelta(days=400)) == "1y ago"

    # Test timezone naive fallback
    naive_dt = datetime.utcnow() - timedelta(minutes=10)
    res = _format_relative_time(naive_dt)
    assert "m ago" in res or "s ago" in res


# ---------------------------------------------------------------------------
# FeedEntry Backwards Compatibility & Rich Fields
# ---------------------------------------------------------------------------

def test_feed_entry_tuple_compatibility():
    pub = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    entry = FeedEntry(
        "dQw4w9WgXcQ",
        pub,
        title="Never Gonna Give You Up",
        duration_sec=212,
        view_count=1400000000,
        description="Official Music Video",
        thumbnail_url="https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
    )

    # Tuple indexing
    assert entry[0] == "dQw4w9WgXcQ"
    assert entry[1] == pub

    # Tuple unpacking
    vid, p = entry
    assert vid == "dQw4w9WgXcQ"
    assert p == pub

    # Extended attribute access
    assert entry.title == "Never Gonna Give You Up"
    assert entry.duration_sec == 212
    assert entry.view_count == 1400000000
    assert entry.description == "Official Music Video"
    assert entry.thumbnail_url == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"


def test_fetch_video_upload_date():
    from shorts_engine.dashboard.routes.channels import _fetch_video_upload_date

    sample_html = """
    <html>
      <head>
        <meta itemprop="uploadDate" content="2026-10-01T14:00:38-07:00">
        <meta itemprop="datePublished" content="2026-10-01T14:00:38-07:00">
      </head>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = sample_html

    with patch("httpx.Client.get", return_value=mock_resp):
        dt = _fetch_video_upload_date("test_vid_1")

    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 10
    assert dt.day == 1
    assert dt.hour == 21
    assert dt.minute == 0


# ---------------------------------------------------------------------------
# RSS Media Parsing
# ---------------------------------------------------------------------------

def test_rss_poller_media_parsing():
    sample_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/">
        <entry>
            <yt:videoId>testVideo123</yt:videoId>
            <title>Awesome Test Video</title>
            <published>2026-09-30T14:00:00+00:00</published>
            <media:group>
                <media:title>Awesome Test Video</media:title>
                <media:description>This is a test description</media:description>
                <media:thumbnail url="https://i.ytimg.com/vi/testVideo123/hqdefault.jpg" width="480" height="360"/>
                <media:community>
                    <media:statistics views="54321"/>
                </media:community>
            </media:group>
        </entry>
    </feed>
    """

    poller = RSSPoller()
    mock_resp = MagicMock()
    mock_resp.read.return_value = sample_xml
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    with patch("urllib.request.urlopen", return_value=mock_resp):
        entries = poller.fetch_feed("UC1234567890123456789012")

    assert len(entries) == 1
    item = entries[0]
    assert item[0] == "testVideo123"
    assert item.title == "Awesome Test Video"
    assert item.view_count == 54321
    assert item.thumbnail_url == "https://i.ytimg.com/vi/testVideo123/hqdefault.jpg"
    assert item.description == "This is a test description"


# ---------------------------------------------------------------------------
# API Channel Videos Endpoint Integration Test
# ---------------------------------------------------------------------------

@pytest.fixture
def client_and_db():
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
    app.dependency_overrides[channels_route.get_db] = override_get_db

    fake_user = User(username="test_admin", role=UserRole.ADMIN, is_active=True)
    app.dependency_overrides[require_operator] = lambda: fake_user
    app.dependency_overrides[require_viewer] = lambda: fake_user
    app.dependency_overrides[verify_csrf] = lambda: True

    client = TestClient(app)
    db = TestSession()

    return client, db


def test_api_channel_videos_endpoint(client_and_db):
    client, db = client_and_db

    ch = Channel(
        id="chan_meta_test",
        name="Meta Test Channel",
        youtube_channel_id="UC_meta_test_chan_1",
        watch_enabled=True,
        human_review_required=False,
    )
    db.add(ch)
    db.commit()

    test_pub = datetime.now(timezone.utc) - timedelta(hours=2)
    fake_entries = [
        FeedEntry(
            "vid_meta_99",
            test_pub,
            title="Metadata Sample Video",
            duration_sec=145,
            view_count=82000,
            description="Testing metadata API",
            thumbnail_url="https://i.ytimg.com/vi/vid_meta_99/hqdefault.jpg",
        )
    ]

    with patch.object(RSSPoller, "fetch_feed", return_value=fake_entries):
        resp = client.get(f"/api/channels/{ch.id}/videos")

    assert resp.status_code == 200
    data = resp.json()
    assert data["channel_id"] == ch.id
    assert data["count"] == 1
    video = data["videos"][0]
    assert video["video_id"] == "vid_meta_99"
    assert video["title"] == "Metadata Sample Video"
    assert video["duration_sec"] == 145
    assert video["duration_formatted"] == "2:25"
    assert video["view_count"] == 82000
    assert video["views_formatted"] == "82.0K views"
    assert video["published_relative"] == "2h ago"
    assert video["youtube_url"] == "https://www.youtube.com/watch?v=vid_meta_99"
    assert video["is_ingested"] is False
    assert video["status_label"] == "NOT INGESTED"
