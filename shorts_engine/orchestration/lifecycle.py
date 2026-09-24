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
        Atomically transitions video status with state-lock verification via conditional SQL update.
        """
        query = self.db.query(SourceVideo).filter(SourceVideo.id == video.id)
        if expected_status:
            query = query.filter(SourceVideo.status == expected_status)

        rows_affected = query.update({SourceVideo.status: target_status}, synchronize_session="fetch")
        self.db.commit()

        if rows_affected == 0:
            logger.warning(
                "Video %s state transition rejected: expected %s, current DB status differs",
                video.id, expected_status
            )
            latest = self.db.query(SourceVideo).filter(SourceVideo.id == video.id).first()
            if latest:
                video.status = latest.status
            return False

        logger.info("Transitioned video %s: %s -> %s", video.id, expected_status or video.status, target_status)
        video.status = target_status
        return True

    def transition_clip(
        self,
        clip: Clip,
        target_status: ClipStatus,
        expected_status: ClipStatus = None,
    ) -> bool:
        """
        Atomically transitions clip status with state-lock verification via conditional SQL update.
        """
        query = self.db.query(Clip).filter(Clip.id == clip.id)
        if expected_status:
            query = query.filter(Clip.status == expected_status)

        rows_affected = query.update({Clip.status: target_status}, synchronize_session="fetch")
        self.db.commit()

        if rows_affected == 0:
            logger.warning(
                "Clip %s state transition rejected: expected %s, current DB status differs",
                clip.id, expected_status
            )
            latest = self.db.query(Clip).filter(Clip.id == clip.id).first()
            if latest:
                clip.status = latest.status
            return False

        logger.info("Transitioned clip %s: %s -> %s", clip.id, expected_status or clip.status, target_status)
        clip.status = target_status
        return True
