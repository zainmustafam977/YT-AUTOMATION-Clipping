"""
RSS/Atom fallback poller for YouTube channel detection.
Polls YouTube's public Atom feed every N minutes and emits new-video events
for any video ID not yet seen, updating channel.last_seen_video_id atomically.

Uses only stdlib (urllib.request + xml.etree.ElementTree) — no feedparser dependency.
"""

import logging
import threading
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
MEDIA_NS = "http://search.yahoo.com/mrss/"


class FeedEntry(tuple):
    """Backwards-compatible 2-tuple (video_id, published_at) with rich metadata attributes."""
    def __new__(
        cls,
        video_id: str,
        published_at: Optional[datetime] = None,
        title: str = "",
        duration_sec: Optional[float] = None,
        view_count: Optional[int] = None,
        description: str = "",
        thumbnail_url: str = "",
    ):
        instance = super().__new__(cls, (video_id, published_at))
        instance.title = title
        instance.duration_sec = duration_sec
        instance.view_count = view_count
        instance.description = description
        instance.thumbnail_url = thumbnail_url or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
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
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/128.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                xml_body = resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                logger.warning("YouTube Atom feed returned 404 for channel %s (channel not found or feed disabled)", channel_id)
            else:
                logger.warning("RSS fetch HTTP error for channel %s (HTTP %s): %s", channel_id, e.code, e)
            return []
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

            desc = ""
            thumb_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
            view_count: Optional[int] = None

            media_grp = entry.find(f"{{{MEDIA_NS}}}group")
            if media_grp is not None:
                d_elem = media_grp.find(f"{{{MEDIA_NS}}}description")
                if d_elem is not None and d_elem.text:
                    desc = d_elem.text.strip()
                t_elem = media_grp.find(f"{{{MEDIA_NS}}}thumbnail")
                if t_elem is not None and t_elem.get("url"):
                    thumb_url = t_elem.get("url")
                comm_elem = media_grp.find(f"{{{MEDIA_NS}}}community")
                if comm_elem is not None:
                    stats_elem = comm_elem.find(f"{{{MEDIA_NS}}}statistics")
                    if stats_elem is not None and stats_elem.get("views"):
                        try:
                            view_count = int(stats_elem.get("views"))
                        except ValueError:
                            pass

            entries.append(FeedEntry(
                video_id,
                published_at,
                title=title,
                view_count=view_count,
                description=desc,
                thumbnail_url=thumb_url,
            ))

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
        newest_seen_in_feed = entries[0][0] if entries else None

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

        # Advance last_seen cursor to newest video in feed to prevent stuck cursor (PLAN-P2-04 / F-18)
        if newest_seen_in_feed and newest_seen_in_feed != last_seen:
            channel.last_seen_video_id = newest_seen_in_feed
            db.commit()
            if new_video_ids:
                logger.info(
                    "Channel %s: detected %d new video(s). last_seen_video_id updated to %s.",
                    channel.youtube_channel_id,
                    len(new_video_ids),
                    newest_seen_in_feed,
                )
            else:
                logger.info(
                    "Channel %s: all new videos were Shorts. last_seen_video_id advanced to %s.",
                    channel.youtube_channel_id,
                    newest_seen_in_feed,
                )
        else:
            logger.debug("Channel %s: no new videos since %s.", channel.youtube_channel_id, last_seen)

        return new_video_ids


class RSSPollLoop:
    """
    Interruptible RSS poll loop used by the CLI `watch` command.
    Calls `on_new_video_callback(channel_id, video_id)` for each new detection.
    Supports clean termination via threading.Event (PLAN-P2-05 / F-19).
    """

    def __init__(self, db_session_factory, on_new_video_callback, stop_event: Optional[threading.Event] = None):
        self.session_factory = db_session_factory
        self.on_new_video = on_new_video_callback
        self.poller = RSSPoller()
        self.stop_event = stop_event or threading.Event()

    def stop(self) -> None:
        """Signals the poll loop to terminate immediately without waiting for interval sleep."""
        self.stop_event.set()

    def run(self, interval_minutes: Optional[int] = None) -> None:
        interval = (interval_minutes or settings.RSS_POLL_INTERVAL_MINUTES) * 60
        logger.info("RSS poll loop started. Interval: %ds.", interval)

        while not self.stop_event.is_set():
            db = self.session_factory()
            try:
                channels = db.query(Channel).filter_by(watch_enabled=True).all()
                for channel in channels:
                    if self.stop_event.is_set():
                        break
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

            if self.stop_event.wait(timeout=interval):
                logger.info("RSS poll loop received stop signal. Exiting gracefully.")
                break
