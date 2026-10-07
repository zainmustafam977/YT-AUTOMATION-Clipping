"""
Multi-Platform Resilient Syndication Engine.
Orchestrates parallel publishing across YouTube Shorts, TikTok, and Instagram Reels
with thread-safe execution, database audit logging, and dry-run safety.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from sqlalchemy.orm import Session, sessionmaker
from shorts_engine.db.session import SessionLocal

from shorts_engine.config.settings import settings
from shorts_engine.db.models import Clip, ClipStatus, PlatformPublishLog
from shorts_engine.services.publishing.base import BasePlatformPublisher, PlatformPublishResult
from shorts_engine.services.publishing.client import YouTubePublisher
from shorts_engine.services.publishing.tiktok import TikTokPublisher
from shorts_engine.services.publishing.instagram import InstagramReelsPublisher
from shorts_engine.services.publishing.metadata import ShortsMetadata

logger = logging.getLogger(__name__)


class YouTubePlatformAdapter(BasePlatformPublisher):
    """Adapter wrapping YouTubePublisher into the BasePlatformPublisher strategy interface."""

    def __init__(self, db_session: Session):
        self.db = db_session
        self.yt_publisher = YouTubePublisher(db_session=db_session)

    @property
    def platform_name(self) -> str:
        return "youtube"

    def health_check(self) -> Tuple[bool, str]:
        can_pub, remaining, reason = self.yt_publisher.quota_tracker.can_publish(db=self.db)
        if not can_pub:
            return False, f"YouTube quota unavailable: {reason}"
        return True, f"YouTube quota healthy ({remaining} units remaining)."

    def publish(
        self,
        video_path: Path,
        title: str,
        description: str,
        tags: Optional[List[str]] = None,
        scheduled_time: Optional[datetime] = None,
        dry_run: Optional[bool] = None,
        **kwargs: Any,
    ) -> PlatformPublishResult:
        clip: Optional[Clip] = kwargs.get("clip")
        if not clip:
            return PlatformPublishResult(
                platform=self.platform_name,
                success=False,
                error="Missing required 'clip' model instance for YouTube publication.",
                dry_run=bool(dry_run),
            )

        metadata = ShortsMetadata(
            title=title,
            description=description,
            tags=tags or [],
        )

        try:
            res = self.yt_publisher.publish_clip(
                clip=clip,
                metadata=metadata,
                dry_run=dry_run,
                encrypted_oauth_token=kwargs.get("encrypted_oauth_token"),
            )
            vid_id = res.get("youtube_video_id")
            permalink = f"https://www.youtube.com/shorts/{vid_id}" if vid_id else None
            return PlatformPublishResult(
                platform=self.platform_name,
                success=res.get("success", False),
                post_id=vid_id,
                permalink=permalink,
                metadata=res,
                dry_run=res.get("dry_run", False),
            )
        except Exception as e:
            return PlatformPublishResult(
                platform=self.platform_name,
                success=False,
                error=str(e),
                dry_run=bool(dry_run),
            )


class MultiPlatformSyndicator:
    """
    Central syndication hub managing platform publishers.
    Coordinates parallel publishing, logs audit trails, and tracks multi-platform status.
    """

    def __init__(self, db_session: Session):
        self.db = db_session
        self.publishers: Dict[str, BasePlatformPublisher] = {
            "youtube": YouTubePlatformAdapter(db_session),
            "tiktok": TikTokPublisher(),
            "instagram": InstagramReelsPublisher(),
        }

    def register_publisher(self, publisher: BasePlatformPublisher):
        """Allows dynamic registration of custom platform adapters."""
        self.publishers[publisher.platform_name] = publisher

    def get_supported_platforms(self) -> List[str]:
        return list(self.publishers.keys())

    def health_check_all(self) -> Dict[str, Tuple[bool, str]]:
        """Checks connectivity and token validity across all registered platforms."""
        return {name: pub.health_check() for name, pub in self.publishers.items()}

    def syndicate_clip(
        self,
        clip_id: str,
        platforms: Optional[List[str]] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        dry_run: Optional[bool] = None,
    ) -> Dict[str, PlatformPublishResult]:
        """
        Publishes a rendered clip across selected platforms concurrently.

        Args:
            clip_id: Database ID of the Clip.
            platforms: List of platforms ('youtube', 'tiktok', 'instagram'). Defaults to settings.ENABLED_SYNDICATION_PLATFORMS.
            title: Title override; defaults to candidate hook or source video title.
            description: Description override.
            tags: Hashtags override.
            dry_run: Override flag for simulation mode.

        Returns:
            Dict mapping platform name to PlatformPublishResult.
        """
        clip: Optional[Clip] = self.db.query(Clip).filter_by(id=clip_id).first()
        if not clip:
            raise ValueError(f"Clip not found with id: {clip_id}")

        if not clip.rendered_path or not Path(clip.rendered_path).exists():
            raise FileNotFoundError(f"Rendered video file for clip {clip_id} not found on disk.")

        video_path = Path(clip.rendered_path)
        cand = clip.candidate
        src_video = cand.source_video if cand else None

        # Build default metadata
        pub_title = title or (cand.hook_text if cand else "Viral Clip")
        pub_desc = description or (f"Check out this highlight from {src_video.title if src_video else 'our video'}! #Shorts")
        pub_tags = tags or ["shorts", "viral", "highlights"]

        target_platforms = platforms or getattr(settings, "ENABLED_SYNDICATION_PLATFORMS", ["youtube"])
        # Validate platforms
        valid_platforms = [p for p in target_platforms if p in self.publishers]
        if not valid_platforms:
            valid_platforms = ["youtube"]

        results: Dict[str, PlatformPublishResult] = {}
        bind = self.db.get_bind() if hasattr(self.db, "get_bind") else None
        session_factory = sessionmaker(bind=bind) if bind is not None else SessionLocal

        def _publish_worker(plat_name: str) -> Tuple[str, PlatformPublishResult]:
            # PLAN-P1-07: Scoped per-thread DB session to prevent cross-thread session corruption
            thread_db = session_factory()
            try:
                thread_clip = thread_db.query(Clip).filter_by(id=clip_id).first()
                pub = self.publishers[plat_name]
                if isinstance(pub, YouTubePlatformAdapter):
                    worker_pub = YouTubePlatformAdapter(db_session=thread_db)
                else:
                    worker_pub = pub

                res = worker_pub.publish(
                    video_path=video_path,
                    title=pub_title,
                    description=pub_desc,
                    tags=pub_tags,
                    dry_run=dry_run,
                    clip=thread_clip,
                    db_session=thread_db,
                )
                return plat_name, res
            finally:
                thread_db.close()


        # Parallel dispatch
        with ThreadPoolExecutor(max_workers=len(valid_platforms)) as executor:
            future_to_plat = {
                executor.submit(_publish_worker, p): p for p in valid_platforms
            }
            for future in as_completed(future_to_plat):
                plat_name = future_to_plat[future]
                try:
                    p_name, result = future.result()
                    results[p_name] = result
                except Exception as exc:
                    logger.exception("Unexpected error in syndication worker for %s: %s", plat_name, exc)
                    results[plat_name] = PlatformPublishResult(
                        platform=plat_name,
                        success=False,
                        error=str(exc),
                        dry_run=bool(dry_run),
                    )

        # Audit log into platform_publish_logs table
        for plat_name, res in results.items():
            status_str = "DRY_RUN" if res.dry_run else ("SUCCESS" if res.success else "FAILED")
            log_entry = PlatformPublishLog(
                clip_id=clip.id,
                platform=plat_name,
                status=status_str,
                platform_post_id=res.post_id,
                permalink=res.permalink,
                error_message=res.error,
                metadata_json=json.dumps(res.metadata or {}),
                published_at=res.published_at,
            )
            self.db.add(log_entry)

        # If any platform succeeded, transition clip status to PUBLISHED
        any_success = any(r.success for r in results.values())
        if any_success and clip.status != ClipStatus.PUBLISHED:
            clip.status = ClipStatus.PUBLISHED

        self.db.commit()
        return results
