"""
Drip-Feed Publisher Daemon:
Background worker that polls for scheduled approved clips where scheduled_publish_at <= now()
and automatically uploads them to YouTube (or simulates in DRY_RUN mode).
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from sqlalchemy.orm import Session

from shorts_engine.config.settings import settings
from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import Clip, ClipStatus
from shorts_engine.services.publishing.client import YouTubePublisher
from shorts_engine.services.publishing.metadata import ShortsMetadata

logger = logging.getLogger(__name__)


def build_clip_metadata(clip: Clip) -> ShortsMetadata:
    """Extracts or infers YouTube title, description, and hashtags for a clip."""
    cand = clip.candidate
    title = clip.generated_title or (cand.hook_text if cand else "Viral Short")
    if len(title) > 90:
        title = title[:87] + "..."
    if "#shorts" not in title.lower():
        title = f"{title} #shorts"

    base_desc = cand.hook_text if cand else title
    description = f"{base_desc}\n\n#shorts #viral #trending"
    tags = ["#shorts", "#viral", "#trending"]
    if cand and cand.source_video and cand.source_video.channel:
        tags.append(cand.source_video.channel.name.lower().replace(" ", ""))

    return ShortsMetadata(
        title=title,
        description=description,
        tags=tags,
    )


def check_and_publish_due_clips(db: Session) -> List[str]:
    """
    Synchronously finds and publishes all clips due for publication.
    Returns list of published clip IDs.
    """
    now = datetime.utcnow()
    due_clips = (
        db.query(Clip)
        .filter(
            Clip.status == ClipStatus.APPROVED,
            Clip.scheduled_publish_at.isnot(None),
            Clip.scheduled_publish_at <= now,
        )
        .order_by(Clip.scheduled_publish_at.asc())
        .all()
    )

    if not due_clips:
        return []

    logger.info("Found %d scheduled clips due for drip-feed publishing at %s UTC", len(due_clips), now.isoformat())
    published_ids: List[str] = []
    publisher = YouTubePublisher(db_session=db)

    for clip in due_clips:
        try:
            if not clip.rendered_path or not Path(clip.rendered_path).exists():
                logger.warning("Scheduled clip %s has no valid rendered file at %s. Marking FAILED.", clip.id, clip.rendered_path)
                clip.status = ClipStatus.FAILED
                db.commit()
                continue

            metadata = build_clip_metadata(clip)
            logger.info("Drip-feed publishing clip %s (Title: '%s', Scheduled: %s)...", clip.id, metadata.title, clip.scheduled_publish_at)
            
            # YouTubePublisher handles dry_run vs live upload, quota tracking, and sets clip.status = PUBLISHED
            pub_res = publisher.publish_clip(
                clip=clip,
                metadata=metadata,
                dry_run=settings.DRY_RUN_PUBLISHING,
            )

            published_ids.append(clip.id)
            yt_id = pub_res.get("youtube_video_id") if isinstance(pub_res, dict) else getattr(pub_res, "youtube_video_id", "unknown")
            logger.info("Successfully published clip %s (YouTube ID: %s, DryRun: %s)", clip.id, yt_id, settings.DRY_RUN_PUBLISHING)


        except Exception as e:
            logger.error("Failed to drip-feed publish clip %s: %s", clip.id, e, exc_info=True)
            clip.retry_count = (clip.retry_count or 0) + 1
            if clip.retry_count >= settings.MAX_RETRY_COUNT:
                clip.status = ClipStatus.FAILED
                logger.warning("Clip %s reached max retries (%d). Set status to FAILED.", clip.id, clip.retry_count)
            db.commit()

    return published_ids


class DripFeedDaemon:
    """Async background worker that periodically polls for due scheduled clips."""

    def __init__(self, poll_interval_seconds: Optional[int] = None):
        self.poll_interval = poll_interval_seconds or getattr(settings, "DRIP_FEED_POLL_INTERVAL_SECONDS", 60)
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        if not getattr(settings, "DRIP_FEED_ENABLED", True):
            logger.info("DripFeedDaemon is disabled via settings (DRIP_FEED_ENABLED=False).")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("DripFeedDaemon started with %ds poll interval.", self.poll_interval)

    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("DripFeedDaemon stopped.")

    async def _run_loop(self):
        while self._running:
            try:
                with SessionLocal() as db:
                    check_and_publish_due_clips(db)
            except Exception as e:
                logger.error("Error in DripFeedDaemon poll loop: %s", e)

            try:
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
