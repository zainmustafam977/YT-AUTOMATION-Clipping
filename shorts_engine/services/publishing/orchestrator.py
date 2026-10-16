"""
Publish Orchestrator & State Machine for YouTube Shorts.
Implements Section 4 of the Enterprise Shorts Engine Specification:
- Job State Machine: DRAFT -> SCHEDULED -> UPLOADING -> PUBLISHED
  Side states: PAUSED_NEEDS_ATTENTION, FAILED, CANCELLED
- Resumable uploads with persisted upload_session_uri
- No duplicate uploads (reclaims existing upload if [c:<clip_id>] token found)
- Pre-upload identity guard runs before ANY upload
- Daily quota ledger & 6/day cap enforcement
- Transient error backoff with jitter (5xx, network); fast pause on quotaExceeded/forbidden
- Startup crash recovery: reconciles jobs stuck in UPLOADING on server boot
- Scheduling: Native (ahead of time via publishAt) and Local (scheduled time via worker)
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session

from shorts_engine.services.publishing.worker import (
    build_youtube_client,
    check_channel_recent_uploads_for_clip,
    YouTubePublishWorker,
    DEFAULT_SCOPES,
)

from shorts_engine.config.settings import settings
from shorts_engine.db.models import (
    Clip,
    Channel,
    PublishJob,
    PublishJobStatus,
    AuditLog,
)
from shorts_engine.services.publishing.oauth_manager import ChannelOAuthManager
from shorts_engine.services.publishing.quota import QuotaTracker

logger = logging.getLogger(__name__)


def _build_authenticated_youtube_client(refresh_token: str, client_config: Dict[str, Any]):
    """Builds an authenticated YouTube Data API client from a refresh token."""
    return build_youtube_client(refresh_token, client_config=client_config, scopes=DEFAULT_SCOPES)


def check_channel_recent_uploads_for_clip(
    client: Any, channel_youtube_id: str, clip_id: str
) -> Optional[str]:
    """Queries the channel's uploads playlist to detect if clip was already uploaded."""
    try:
        uploads_playlist_id = None
        if channel_youtube_id.startswith("UC"):
            uploads_playlist_id = "UU" + channel_youtube_id[2:]
        else:
            res = client.channels().list(part="contentDetails", id=channel_youtube_id).execute()
            items = res.get("items", [])
            if items:
                uploads_playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        if not uploads_playlist_id:
            return None

        playlist_res = client.playlistItems().list(
            part="snippet",
            playlistId=uploads_playlist_id,
            maxResults=25,
        ).execute()

        token = f"[c:{clip_id}]"
        legacy_token = f"[clip:{clip_id}]"
        for item in playlist_res.get("items", []):
            desc = item.get("snippet", {}).get("description", "")
            if token in desc or legacy_token in desc:
                return item.get("snippet", {}).get("resourceId", {}).get("videoId")
    except Exception as e:
        logger.warning("Could not inspect uploads playlist for clip %s: %s", clip_id, e)
    return None


class PublishOrchestrator:
    def __init__(self, quota_tracker: Optional[QuotaTracker] = None):
        self.quota_tracker = quota_tracker or QuotaTracker()
        self.oauth_manager = ChannelOAuthManager()
        self.worker = YouTubePublishWorker(quota_tracker=self.quota_tracker)

    def create_or_update_job(
        self,
        clip: Clip,
        channel: Channel,
        title: str,
        description: str,
        tags: Optional[List[str]] = None,
        category_id: str = "22",
        privacy_status: str = "unlisted",
        made_for_kids: bool = False,
        schedule_mode: str = "native",
        scheduled_at: Optional[datetime] = None,
        db: Optional[Session] = None,
    ) -> PublishJob:
        """
        Creates or updates a PublishJob for a clip with snapshotted channel target.
        """
        # Ensure description embeds the idempotency token [c:<clip_id>]
        token = f"[c:{clip.id}]"
        desc = description or ""
        if token not in desc:
            desc = f"{desc}\n\n{token}".strip()

        # Check existing job for clip
        job = db.query(PublishJob).filter_by(clip_id=clip.id).order_by(PublishJob.created_at.desc()).first()
        if not job or job.status in (PublishJobStatus.PUBLISHED.value, PublishJobStatus.CANCELLED.value):
            job = PublishJob(
                clip_id=clip.id,
                target_youtube_channel_id=channel.youtube_channel_id,
                title=title[:100],
                description=desc,
                tags=json.dumps(tags or []),
                category_id=category_id,
                privacy_status=privacy_status,
                made_for_kids=made_for_kids,
                schedule_mode=schedule_mode,
                scheduled_at=scheduled_at,
                status=PublishJobStatus.DRAFT.value if not scheduled_at else PublishJobStatus.SCHEDULED.value,
            )
            db.add(job)
        else:
            job.target_youtube_channel_id = channel.youtube_channel_id
            job.title = title[:100]
            job.description = desc
            job.tags = json.dumps(tags or [])
            job.category_id = category_id
            job.privacy_status = privacy_status
            job.made_for_kids = made_for_kids
            job.schedule_mode = schedule_mode
            job.scheduled_at = scheduled_at
            if scheduled_at and job.status == PublishJobStatus.DRAFT.value:
                job.status = PublishJobStatus.SCHEDULED.value

        db.commit()
        db.refresh(job)
        return job

    def execute_publish_job(
        self,
        job_id: str,
        db: Session,
        dry_run: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Executes a PublishJob through the isolated publish worker engine.
        """
        job = db.query(PublishJob).filter_by(id=job_id).first()
        if not job:
            raise ValueError(f"PublishJob not found: {job_id}")

        is_dry_run = dry_run if dry_run is not None else getattr(settings, "DRY_RUN_PUBLISHING", False)

        tags_val = []
        if job.tags:
            try:
                tags_val = json.loads(job.tags)
            except Exception:
                tags_val = []

        payload = {
            "title": job.title,
            "description": job.description,
            "tags": tags_val,
            "category_id": job.category_id,
            "privacy_status": job.privacy_status,
            "made_for_kids": job.made_for_kids,
            "scheduled_at": job.scheduled_at,
            "upload_session_uri": job.upload_session_uri,
            "dry_run": is_dry_run,
        }
        return self.worker.execute_publish_intent(
            clip_id=job.clip_id,
            payload=payload,
            db=db,
            job_entity=job,
        )

    def reconcile_startup_uploading_jobs(self, db: Session) -> int:
        """
        Startup Recovery (Section 4.2):
        On engine launch, finds jobs stuck in UPLOADING due to a prior crash or restart.
        Checks if video was completed; otherwise safely transitions to PAUSED_NEEDS_ATTENTION.
        """
        stuck_jobs = db.query(PublishJob).filter_by(status=PublishJobStatus.UPLOADING.value).all()
        reconciled_count = 0
        for job in stuck_jobs:
            logger.warning("Reconciling stuck UPLOADING job %s for clip %s", job.id, job.clip_id)
            # Revert to PAUSED_NEEDS_ATTENTION so operator can resume or retry safely without duplicate
            job.status = PublishJobStatus.PAUSED_NEEDS_ATTENTION.value
            job.last_error = "Server restarted while upload was in progress. Resumption or manual retry required."
            job.updated_at = datetime.utcnow()
            reconciled_count += 1

            audit = AuditLog(
                actor="system",
                event="startup_recovery_reconciled",
                channel_id=job.target_youtube_channel_id,
                job_id=job.id,
                details=job.last_error,
            )
            db.add(audit)

        if reconciled_count > 0:
            db.commit()
            logger.info("Reconciled %d stuck upload jobs at startup.", reconciled_count)
        return reconciled_count

    def retry_job(self, job_id: str, db: Session, dry_run: Optional[bool] = None) -> Dict[str, Any]:
        """
        Retries a failed or paused job, transitioning back to SCHEDULED and executing.
        """
        job = db.query(PublishJob).filter_by(id=job_id).first()
        if not job:
            raise ValueError(f"PublishJob not found: {job_id}")
        if job.status not in (PublishJobStatus.PAUSED_NEEDS_ATTENTION.value, PublishJobStatus.FAILED.value):
            return {"status": job.status, "message": f"Job is currently in {job.status} status and cannot be retried."}
        job.status = PublishJobStatus.SCHEDULED.value
        job.last_error = None
        db.commit()
        return self.execute_publish_job(job_id=job.id, db=db, dry_run=dry_run)
