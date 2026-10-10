"""
Tests for the Detection Service: RSS poller, WebSub HMAC verification,
and new-video idempotency.
"""

import hashlib
import hmac
import xml.etree.ElementTree as ET
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.db.session import Base
from shorts_engine.db.models import Channel
from shorts_engine.services.detection.rss_poller import RSSPoller
from shorts_engine.services.detection.websub import WebSubNotificationParser


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_atom_feed(entries: list) -> bytes:
    """Build a minimal YouTube Atom feed XML for testing."""
    ATOM = "http://www.w3.org/2005/Atom"
    YT = "http://www.youtube.com/xml/schemas/2015"
    root = ET.Element(f"{{{ATOM}}}feed")
    for channel_id, video_id in entries:
        entry = ET.SubElement(root, f"{{{ATOM}}}entry")
        vid_el = ET.SubElement(entry, f"{{{YT}}}videoId")
        vid_el.text = video_id
        ch_el = ET.SubElement(entry, f"{{{YT}}}channelId")
        ch_el.text = channel_id
        pub_el = ET.SubElement(entry, f"{{{ATOM}}}published")
        pub_el.text = "2026-09-10T18:00:00Z"
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _make_in_memory_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_rss_poller_parses_new_video():
    """RSS poller should detect a video ID that's newer than last_seen_video_id."""
    db = _make_in_memory_db()
    channel = Channel(
        youtube_channel_id="UC_testchannel",
        name="Test Channel",
        watch_enabled=True,
        human_review_required=True,
        last_seen_video_id="OLD_VIDEO_ID",
    )
    db.add(channel)
    db.commit()

    atom_feed = _make_atom_feed([("UC_testchannel", "NEW_VIDEO_001")])
    poller = RSSPoller()

    with patch.object(poller, "fetch_feed", return_value=[("NEW_VIDEO_001", __import__("datetime").datetime.now())]):
        new_ids = poller.poll_channel(channel, db)

    assert "NEW_VIDEO_001" in new_ids
    assert channel.last_seen_video_id == "NEW_VIDEO_001"


def test_rss_poller_skips_known_video():
    """RSS poller should emit nothing if all videos are already seen."""
    db = _make_in_memory_db()
    channel = Channel(
        youtube_channel_id="UC_testchannel",
        name="Test Channel",
        watch_enabled=True,
        human_review_required=True,
        last_seen_video_id="ALREADY_SEEN",
    )
    db.add(channel)
    db.commit()

    poller = RSSPoller()
    with patch.object(poller, "fetch_feed", return_value=[("ALREADY_SEEN", __import__("datetime").datetime.now())]):
        new_ids = poller.poll_channel(channel, db)

    assert new_ids == []
    # last_seen_video_id must not change
    assert channel.last_seen_video_id == "ALREADY_SEEN"


def test_websub_hmac_validation_passes():
    """Correct HMAC-SHA1 signature should be accepted."""
    secret = "test_websub_secret_123"
    body = b"<feed>test notification body</feed>"
    sig = "sha1=" + hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()

    with patch("shorts_engine.services.detection.websub.settings") as mock_settings:
        mock_settings.WEBSUB_SECRET = secret
        parser = WebSubNotificationParser(secret=secret)
        assert parser.verify_signature(body, sig) is True


def test_websub_hmac_validation_rejects_tampered():
    """A tampered body (mismatched HMAC) should be rejected."""
    secret = "test_websub_secret_123"
    original_body = b"<feed>original body</feed>"
    tampered_body = b"<feed>TAMPERED body</feed>"
    # Signature computed over original body
    sig = "sha1=" + hmac.new(secret.encode(), original_body, hashlib.sha1).hexdigest()

    parser = WebSubNotificationParser(secret=secret)
    # Verify against tampered body — must fail
    assert parser.verify_signature(tampered_body, sig) is False
