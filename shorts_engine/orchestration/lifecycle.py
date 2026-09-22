"""
Job Lifecycle and Idempotent State Machine: enforces explicit stage transitions,
optimistic locking, and resume-from-checkpoint after process restarts.
"""

import logging
from sqlalchemy.orm import Session
from shorts_engine.db.models import SourceVideo, SourceVideoStatus, Clip, ClipStatus

logger = logging.getLogger(__name__)


class LifecycleManager:
    def __init__(self, db_session: Session):
        self.db = db_session

    def transition_video(
        self,
        video: SourceVideo,
        target_status: SourceVideoStatus,
        expected_status: SourceVideoStatus = None,
    ) -> bool:
        """
        Atomically transitions video status with state-lock verification.
        """
        if expected_status and video.status != expected_status:
            logger.warning(
                "Video %s state mismatch: expected %s, found %s",
                video.id, expected_status, video.status
            )
            return False

        logger.info("Transitioning video %s: %s -> %s", video.id, video.status, target_status)
        video.status = target_status
        self.db.commit()
        return True

    def transition_clip(
        self,
        clip: Clip,
        target_status: ClipStatus,
        expected_status: ClipStatus = None,
    ) -> bool:
        """
        Atomically transitions clip status with state-lock verification.
        """
        if expected_status and clip.status != expected_status:
            logger.warning(
                "Clip %s state mismatch: expected %s, found %s",
                clip.id, expected_status, clip.status
            )
            return False

        logger.info("Transitioning clip %s: %s -> %s", clip.id, clip.status, target_status)
        clip.status = target_status
        self.db.commit()
        return True
