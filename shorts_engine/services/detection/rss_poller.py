"""
RSS/Atom fallback poller for YouTube channel detection.
Polls YouTube's public Atom feed every N minutes and emits new-video events
for any video ID not yet seen, updating channel.last_seen_video_id atomically.

Uses only stdlib (urllib.request + xml.etree.ElementTree) — no feedparser dependency.
"""

import logging
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session

from shorts_engine.config.settings import settings
from shorts_engine.db.models import Channel
from shorts_engine.services.detection.filter import is_existing_short

logger = logging.getLogger(__name__)

YOUTUBE_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
ATOM_NS = "http://www.w3.org/2005/Atom"
YT_NS = "http://www.youtube.com/xml/schemas/2015"


class FeedEntry(tuple):
    """Backwards-compatible 2-tuple (video_id, published_at) with title attribute."""
    def __new__(cls, video_id: str, published_at: datetime, title: str = ""):
        instance = super().__new__(cls, (video_id, published_at))
        instance.title = title
        return instance


class RSSPoller:
    """Fetches and parses YouTube's Atom feed for a channel, returning new video IDs."""

    def __init__(self, timeout_sec: int = 10):
        self.timeout_sec = timeout_sec

    def fetch_feed(self, channel_id: str) -> List[Tuple[str, datetime]]:
        """
        Fetches the channel's Atom feed and returns a list of (video_id, published_at) tuples,
        sorted newest-first.
        """
        if not channel_id or not channel_id.startswith("UC") or len(channel_id) != 24:
            logger.debug("Skipping RSS fetch for non-YouTube channel ID format: %s", channel_id)
            return []

        url = YOUTUBE_FEED_URL.format(channel_id=channel_id)
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "ShortsEngine/1.0 (RSS Fallback Poller)"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                xml_body = resp.read()
        except Exception as e:
            logger.error("RSS fetch failed for channel %s: %s", channel_id, e)
            return []

        try:
            root = ET.fromstring(xml_body)
        except ET.ParseError as e:
            logger.error("RSS XML parse error for channel %s: %s", channel_id, e)
            return []

        entries: List[Tuple[str, datetime]] = []
        for entry in root.findall(f"{{{ATOM_NS}}}entry"):
            vid_elem = entry.find(f"{{{YT_NS}}}videoId")
            pub_elem = entry.find(f"{{{ATOM_NS}}}published")
            title_elem = entry.find(f"{{{ATOM_NS}}}title")
            if vid_elem is None or pub_elem is None:
                continue
            video_id = vid_elem.text.strip()
            title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""
            try:
                # YouTube publishes ISO 8601 with Z suffix
                pub_text = pub_elem.text.strip().replace("Z", "+00:00")
                published_at = datetime.fromisoformat(pub_text)
            except ValueError:
                published_at = datetime.now(tz=timezone.utc)
            entries.append(FeedEntry(video_id, published_at, title=title))

        # Sort newest-first
        entries.sort(key=lambda x: x[1], reverse=True)
        return entries

    def poll_channel(self, channel: Channel, db: Session) -> List[str]:
        """
        Polls the channel's Atom feed and returns a list of newly-detected video IDs
        (i.e., IDs that are newer than channel.last_seen_video_id).
        Updates channel.last_seen_video_id to the newest video ID found.
        """
        entries = self.fetch_feed(channel.youtube_channel_id)
        if not entries:
            return []

        new_video_ids: List[str] = []
        last_seen = channel.last_seen_video_id

        for item in entries:
            video_id = item[0]
            title = getattr(item, "title", "")

            if video_id == last_seen:
                # Reached the last known video; everything before is already processed.
                break

            # Filter out videos that are already YouTube Shorts (#Shorts in title)
            is_short, reason = is_existing_short(title)
            if is_short:
                logger.info(
                    "RSS Poller skipping video %s: already a YouTube Short (%s)",
                    video_id,
                    reason,
                )
                continue

            new_video_ids.append(video_id)

        if new_video_ids:
            # Update last_seen to the most recent video
            channel.last_seen_video_id = new_video_ids[0]
            db.commit()
            logger.info(
                "Channel %s: detected %d new video(s). last_seen_video_id updated to %s.",
                channel.youtube_channel_id,
                len(new_video_ids),
                new_video_ids[0],
            )
        else:
            logger.debug("Channel %s: no new videos since %s.", channel.youtube_channel_id, last_seen)

        return new_video_ids


class RSSPollLoop:
    """
    Blocking RSS poll loop used by the CLI `watch` command.
    Calls `on_new_video_callback(channel_id, video_id)` for each new detection.
    """

    def __init__(self, db_session_factory, on_new_video_callback):
        self.session_factory = db_session_factory
        self.on_new_video = on_new_video_callback
        self.poller = RSSPoller()

    def run(self, interval_minutes: Optional[int] = None) -> None:
        interval = (interval_minutes or settings.RSS_POLL_INTERVAL_MINUTES) * 60
        logger.info("RSS poll loop started. Interval: %ds.", interval)

        while True:
            db = self.session_factory()
            try:
                channels = db.query(Channel).filter_by(watch_enabled=True).all()
                for channel in channels:
                    try:
                        new_ids = self.poller.poll_channel(channel, db)
                        for video_id in new_ids:
                            self.on_new_video(channel.youtube_channel_id, video_id)
                    except Exception as e:
                        logger.error(
                            "Error polling channel %s: %s", channel.youtube_channel_id, e
                        )
            finally:
                db.close()

            time.sleep(interval)
