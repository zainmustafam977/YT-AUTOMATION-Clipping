"""
Drip-Feed Publisher Daemon:
Background worker that polls for scheduled approved clips where scheduled_publish_at <= now()
and automatically uploads them to YouTube (or simulates in DRY_RUN mode).
"""

import asyncio
import logging
import re
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
    """Extracts or infers YouTube title, description, and hashtags for a clip, preserving SEO and channel defaults."""
    cand = clip.candidate
    title = clip.generated_title or (cand.hook_text if cand else "Viral Short")
    if len(title) > 90:
        title = title[:87] + "..."
    if "#shorts" not in title.lower():
        title = f"{title} #shorts"

    token = f"[c:{clip.id}]"
    legacy_token = f"[clip:{clip.id}]"
    if clip.generated_description and len(clip.generated_description.strip()) > 10:
        description = clip.generated_description
        if token not in description:
            description = f"{description}\n\n{token} {legacy_token}"
        elif legacy_token not in description:
            description = f"{description} {legacy_token}"
    else:
        base_desc = cand.hook_text if cand else title
        description = f"{base_desc}\n\n#shorts #viral #trending\n\n{token} {legacy_token}"

    tags = None
    if clip.generated_tags:
        try:
            import json
            parsed = json.loads(clip.generated_tags)
            if isinstance(parsed, list) and parsed:
                tags = [str(t) for t in parsed]
        except Exception:
            tags = None

    channel = None
    if cand and cand.source_video and cand.source_video.channel:
        channel = cand.source_video.channel

    channel_defaults = getattr(channel, "upload_defaults", {}) if channel else {}
    if not tags:
        tags = ["#shorts", "#viral", "#trending"]
        if channel:
            tags.append(channel.name.lower().replace(" ", ""))
        if channel_defaults.get("default_tags"):
            raw_def_tags = channel_defaults["default_tags"]
            if isinstance(raw_def_tags, list):
                def_tags = [str(t).strip() for t in raw_def_tags if str(t).strip()]
            else:
                def_tags = [t.strip() for t in str(raw_def_tags).split(",") if t.strip()]
            tags.extend(def_tags)

    privacy = channel_defaults.get("privacy_status", "unlisted")
    category_id = channel_defaults.get("category_id", "22")
    made_for_kids = bool(channel_defaults.get("made_for_kids", False))
    language = channel_defaults.get("default_language", "en")
    license_type = channel_defaults.get("license", "youtube")

    return ShortsMetadata(
        title=title,
        description=description,
        tags=tags,
        privacy_status=privacy,
        category_id=category_id,
        made_for_kids=made_for_kids,
        default_language=language,
        license=license_type,
    )


def extract_clip_id_from_description(description: str) -> Optional[str]:
    """Parses [c:<clip_id>] token from a YouTube description string."""
    if not description:
        return None
    match = re.search(r'\[c:([^\]]+)\]', description)
    return match.group(1) if match else None


def check_duplicate_upload(clip_id: str, channel_recent_descriptions: List[str]) -> bool:
    """Returns True if any description in the list contains [c:<clip_id>]."""
    token = f"[c:{clip_id}]"
    for desc in channel_recent_descriptions:
        if desc and token in desc:
            return True
    return False


def check_and_publish_due_clips(db: Session) -> List[str]:
    """
    Synchronously finds and publishes all clips due for publication.
    Enforces the 15-minute ambiguity rule for stale PUBLISHING clips.
    Returns list of published clip IDs.
    """
    now = datetime.utcnow()

    # Ambiguity Rule: Reclaim / flag stale PUBLISHING clips (PLAN-P0-05)
    stale_publishing_clips = (
        db.query(Clip)
        .filter(Clip.status == ClipStatus.PUBLISHING)
        .all()
    )
    for stale_clip in stale_publishing_clips:
        updated = stale_clip.updated_at or stale_clip.created_at or now
        elapsed_sec = (now - updated).total_seconds()
        # Treat any upload initiated or updated within the last 15 minutes as ambiguous
        if elapsed_sec <= 900:
            logger.info(
                "Clip %s has been in PUBLISHING status for %.1fs (<= 15 min). "
                "Leaving in PUBLISHING per ambiguity window.",
                stale_clip.id, elapsed_sec,
            )
            continue
        else:
            logger.warning(
                "Clip %s has been in PUBLISHING status for %.1fs (> 15 min). "
                "Marking FAILED_NEEDS_REVIEW for operator intervention.",
                stale_clip.id, elapsed_sec,
            )
            stale_clip.status = ClipStatus.FAILED_NEEDS_REVIEW
            db.commit()

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
