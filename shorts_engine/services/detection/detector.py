"""
Unified Detection Service: entry point that bridges WebSub push events and RSS polling
into pipeline jobs. Creates SourceVideo records idempotently and enforces retry limits.
"""

import logging
from typing import Optional, Callable
from sqlalchemy.orm import Session

from shorts_engine.config.settings import settings
from shorts_engine.db.models import Channel, SourceVideo, SourceVideoStatus
from shorts_engine.services.detection.rss_poller import RSSPoller, RSSPollLoop
from shorts_engine.services.detection.websub import WebSubSubscriber, WebSubLeaseRenewer

logger = logging.getLogger(__name__)


class DetectionService:
    """
    Receives new-video signals (from WebSub push or RSS poll) and creates
    idempotent SourceVideo records ready for the PipelineRunner to process.
    """

    def __init__(self, db_session: Session):
        self.db = db_session
        self.poller = RSSPoller()
        self.subscriber = WebSubSubscriber()
        self.renewer = WebSubLeaseRenewer(self.subscriber)

    def get_or_create_channel(self, youtube_channel_id: str) -> Optional[Channel]:
        return self.db.query(Channel).filter_by(
            youtube_channel_id=youtube_channel_id,
            watch_enabled=True,
        ).first()

    def on_new_video(self, channel_youtube_id: str, video_id: str) -> Optional[SourceVideo]:
        """
        Called when a new video is detected (from either WebSub or RSS).
        Idempotently creates a SourceVideo record in DETECTED state.
        Skips if the video already exists or channel's retry budget is exhausted.
        """
        channel = self.get_or_create_channel(channel_youtube_id)
        if channel is None:
            logger.warning(
                "Received detection for unknown/disabled channel %s — ignoring.",
                channel_youtube_id,
            )
            return None

        # Idempotency: skip if already known
        existing = self.db.query(SourceVideo).filter_by(
            channel_id=channel.id,
            youtube_video_id=video_id,
        ).first()
        if existing:
            logger.debug("Video %s already in DB (status=%s) — skipping.", video_id, existing.status)
            return existing

        # Create new detection record
        video = SourceVideo(
            channel_id=channel.id,
            youtube_video_id=video_id,
            title=f"Video {video_id}",  # Title resolved during ingestion
            status=SourceVideoStatus.DETECTED,
            retry_count=0,
        )
        self.db.add(video)

        # Update channel's last_seen pointer
        channel.last_seen_video_id = video_id
        self.db.commit()

        logger.info(
            "Detected new video %s on channel %s — queued for processing.",
            video_id, channel_youtube_id,
        )
        return video

    def subscribe_channel(self, youtube_channel_id: str) -> bool:
        """Registers a WebSub subscription for a channel."""
        return self.subscriber.subscribe(youtube_channel_id)

    def check_lease_renewals(self) -> list:
        """Checks and renews WebSub leases approaching 80% of their lifetime."""
        return self.renewer.check_and_renew_all()

    def build_rss_loop(
        self,
        session_factory,
        on_new_video_callback: Optional[Callable] = None,
    ) -> RSSPollLoop:
        """
        Builds a blocking RSS poll loop.
        `on_new_video_callback(channel_id, video_id)` is called for each new detection.
        """
        callback = on_new_video_callback or (lambda ch, vid: self.on_new_video(ch, vid))
        return RSSPollLoop(session_factory, callback)
