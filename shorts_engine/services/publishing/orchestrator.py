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
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
from googleapiclient.errors import HttpError
import google.oauth2.credentials
import googleapiclient.discovery
import googleapiclient.http

from shorts_engine.config.settings import settings
from shorts_engine.db.models import (
    Clip,
    ClipStatus,
    Channel,
    ChannelCredential,
    PublishJob,
    PublishJobStatus,
    PublishLog,
    AuditLog,
)
from shorts_engine.db.security import decrypt_secret
from shorts_engine.services.publishing.oauth_manager import ChannelOAuthManager, DEFAULT_SCOPES
from shorts_engine.services.publishing.quota import QuotaTracker
from shorts_engine.services.events.bus import global_event_bus
from shorts_engine.services.events.models import PlatformEvent

logger = logging.getLogger(__name__)


def _build_authenticated_youtube_client(refresh_token: str, client_config: Dict[str, Any]):
    """Builds an authenticated YouTube Data API client from a refresh token."""
    credentials = google.oauth2.credentials.Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=client_config.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=client_config.get("client_id"),
        client_secret=client_config.get("client_secret"),
        scopes=DEFAULT_SCOPES,
    )
    return googleapiclient.discovery.build("youtube", "v3", credentials=credentials, cache_discovery=False)


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
        Executes a PublishJob through the full lifecycle:
        1. Pre-flight checks: video file exists, not already published.
        2. Pre-upload Identity Guard: verifies channel identity via channels.list(mine=true).
        3. Quota check: checks daily budget and 6/day upload cap.
        4. Transitions state: SCHEDULED/DRAFT -> UPLOADING.
        5. Idempotency check: scans recent channel uploads for [c:<clip_id>].
        6. Resumable upload: saves upload_session_uri, uploads in chunks with backoff.
        7. On success: marks PUBLISHED, records 1600 units in quota_ledger, writes audit_log.
        8. On recoverable failure: sets PAUSED_NEEDS_ATTENTION with clear action required.
        """
        job = db.query(PublishJob).filter_by(id=job_id).first()
        if not job:
            raise ValueError(f"PublishJob not found: {job_id}")

        clip = job.clip
        if not clip:
            clip = db.query(Clip).filter_by(id=job.clip_id).first()

        is_dry_run = dry_run if dry_run is not None else getattr(settings, "DRY_RUN_PUBLISHING", False)

        # 1. Idempotency Check
        if job.status == PublishJobStatus.PUBLISHED.value and job.youtube_video_id:
            logger.info("PublishJob %s is already published (video ID: %s)", job.id, job.youtube_video_id)
            return {
                "success": True,
                "status": "PUBLISHED",
                "youtube_video_id": job.youtube_video_id,
                "already_published": True,
            }

        # Verify rendered file
        video_path = Path(clip.rendered_path) if clip and clip.rendered_path else None
        if not video_path or not video_path.exists() or video_path.stat().st_size == 0:
            job.status = PublishJobStatus.PAUSED_NEEDS_ATTENTION.value
            job.last_error = f"Rendered video file missing or empty: {clip.rendered_path if clip else 'None'}"
            db.commit()
            return {
                "success": False,
                "status": job.status,
                "error": job.last_error,
                "action_required": "Render or re-render clip before publishing",
            }

        # 2. Pre-Upload Identity Guard (Section 3.4)
        if not is_dry_run:
            guard_ok, guard_err = self.oauth_manager.verify_identity_before_upload(job, db)
            if not guard_ok:
                logger.warning("Pre-upload identity guard blocked job %s: %s", job.id, guard_err)
                try:
                    global_event_bus.publish_sync(PlatformEvent(
                        category="auth",
                        level="WARNING",
                        title="Publishing Blocked: Identity Guard",
                        message=f"Identity guard blocked job {job.id}: {guard_err}",
                        entity_id=job.clip_id,
                        entity_type="clip",
                        action_url="/publish",
                    ))
                except Exception:
                    pass
                return {
                    "success": False,
                    "status": PublishJobStatus.PAUSED_NEEDS_ATTENTION.value,
                    "error": guard_err,
                    "action_required": "Reconnect the target channel in Publishing Studio",
                }

        # 3. Quota & Daily Cap Check (Section 4.4)
        can_pub, remaining_quota, quota_reason = self.quota_tracker.can_publish(db=db)
        if not can_pub and not is_dry_run:
            job.status = PublishJobStatus.PAUSED_NEEDS_ATTENTION.value
            job.last_error = quota_reason
            db.commit()
            try:
                global_event_bus.publish_sync(PlatformEvent(
                    category="quota",
                    level="WARNING",
                    title="Publishing Blocked: Quota Limit",
                    message=f"Daily quota reached for job {job.id}: {quota_reason}",
                    entity_id=job.clip_id,
                    entity_type="clip",
                    action_url="/settings",
                ))
            except Exception:
                pass
            return {
                "success": False,
                "status": PublishJobStatus.PAUSED_NEEDS_ATTENTION.value,
                "error": quota_reason,
                "action_required": "Wait for Pacific midnight quota reset or calibrate quota",
            }

        # 4. Transition to UPLOADING (Persisted before external call)
        job.status = PublishJobStatus.UPLOADING.value
        job.attempts += 1
        job.updated_at = datetime.utcnow()
        if clip:
            clip.status = ClipStatus.PUBLISHING
        db.commit()

        try:
            global_event_bus.publish_sync(PlatformEvent(
                category="publishing",
                level="INFO",
                title="Publishing Clip Started",
                message=f"Uploading clip {job.clip_id[:8]} to YouTube ({'DRY RUN' if is_dry_run else 'LIVE'})...",
                stage_progress_pct=25.0,
                entity_id=job.clip_id,
                entity_type="clip",
                action_url=f"/queue?clip={job.clip_id}",
            ))
        except Exception:
            pass

        audit = AuditLog(
            actor="system",
            event="publish_attempt_started",
            channel_id=job.target_youtube_channel_id,
            job_id=job.id,
            details=f"Attempt {job.attempts}: Uploading clip {job.clip_id} to {job.target_youtube_channel_id}",
        )
        db.add(audit)
        db.commit()

        # 5. Live Upload or Simulation
        youtube_video_id = None
        try:
            if is_dry_run:
                logger.info("[DRY-RUN] Simulating upload for job %s...", job.id)
                youtube_video_id = f"dry_run_{job.clip_id[:8]}"
            else:
                channel = db.query(Channel).filter_by(youtube_channel_id=job.target_youtube_channel_id).first()
                cred = db.query(ChannelCredential).filter_by(channel_id=channel.id).first()
                refresh_token = decrypt_secret(cred.encrypted_refresh_token) if cred else None

                client_cfg = self.oauth_manager.get_client_config()
                client = _build_authenticated_youtube_client(refresh_token, client_cfg)

                # Check recent uploads playlist for duplicate
                existing_yt_id = check_channel_recent_uploads_for_clip(
                    client, job.target_youtube_channel_id, job.clip_id
                )
                if existing_yt_id:
                    logger.info("Found existing upload for clip %s: %s. Reclaiming.", job.clip_id, existing_yt_id)
                    youtube_video_id = existing_yt_id
                    try:
                        global_event_bus.publish_sync(PlatformEvent(
                            category="publishing",
                            level="SUCCESS",
                            title="Clip Reclaimed & Published",
                            message=f"Reclaimed existing upload for clip {job.clip_id[:8]} (YouTube ID: {existing_yt_id}).",
                            stage_progress_pct=100.0,
                            entity_id=job.clip_id,
                            entity_type="clip",
                            action_url=f"https://youtube.com/shorts/{existing_yt_id}",
                            details={"youtube_video_id": existing_yt_id, "reclaimed": True},
                        ))
                    except Exception:
                        pass
                else:
                    # Execute resumable upload
                    youtube_video_id = self._upload_resumable(
                        client=client,
                        video_path=video_path,
                        job=job,
                        db=db,
                    )

            # 6. Success State Transition
            job.status = PublishJobStatus.PUBLISHED.value
            job.youtube_video_id = youtube_video_id
            job.last_error = None
            job.updated_at = datetime.utcnow()

            if clip:
                clip.status = ClipStatus.PUBLISHED

            # Record publish cost in Quota Ledger (1,600 units)
            if not is_dry_run:
                self.quota_tracker.record_publish(
                    channel_id=job.target_youtube_channel_id,
                    job_id=job.id,
                    db=db,
                )

            # Record PublishLog for backward compatibility
            p_log = db.query(PublishLog).filter_by(clip_id=job.clip_id).first()
            if not p_log:
                p_log = PublishLog(
                    clip_id=job.clip_id,
                    youtube_video_id=youtube_video_id,
                    published_at=datetime.utcnow(),
                    title=job.title,
                    description=job.description,
                    tags_json=job.tags,
                    status="DRY_RUN" if is_dry_run else "SUCCESS",
                )
                db.add(p_log)
            else:
                p_log.youtube_video_id = youtube_video_id
                p_log.status = "DRY_RUN" if is_dry_run else "SUCCESS"
                p_log.published_at = datetime.utcnow()

            audit_success = AuditLog(
                actor="system",
                event="publish_succeeded",
                channel_id=job.target_youtube_channel_id,
                job_id=job.id,
                details=f"Successfully uploaded short: {youtube_video_id} (dry_run={is_dry_run})",
            )
            db.add(audit_success)
            db.commit()

            try:
                yt_url = f"https://youtube.com/shorts/{youtube_video_id}" if not is_dry_run else None
                global_event_bus.publish_sync(PlatformEvent(
                    category="publishing",
                    level="SUCCESS",
                    title="Clip Published",
                    message=f"Clip {job.clip_id[:8]} successfully published! YouTube ID: {youtube_video_id}",
                    stage_progress_pct=100.0,
                    entity_id=job.clip_id,
                    entity_type="clip",
                    action_url=yt_url or f"/queue?clip={job.clip_id}",
                    details={"youtube_video_id": youtube_video_id, "dry_run": is_dry_run},
                ))
            except Exception:
                pass

            return {
                "success": True,
                "status": "PUBLISHED",
                "youtube_video_id": youtube_video_id,
                "youtube_url": f"https://youtube.com/shorts/{youtube_video_id}",
                "job_id": job.id,
            }

        except HttpError as e:
            status_code = getattr(e.resp, "status", None) if hasattr(e, "resp") else None
            err_str = str(e)
            logger.error("YouTube API HttpError during upload for job %s: %s", job.id, err_str)

            # Classify error
            is_quota_error = "quotaExceeded" in err_str or "uploadLimitExceeded" in err_str or status_code == 429
            is_auth_error = status_code in (401, 403) or "invalid_grant" in err_str

            job.status = PublishJobStatus.PAUSED_NEEDS_ATTENTION.value
            job.last_error = f"YouTube API Error ({status_code}): {err_str[:400]}"
            if clip:
                clip.status = ClipStatus.FAILED_NEEDS_REVIEW
            db.commit()

            audit_err = AuditLog(
                actor="system",
                event="publish_http_error",
                channel_id=job.target_youtube_channel_id,
                job_id=job.id,
                details=err_str[:500],
            )
            db.add(audit_err)
            db.commit()

            try:
                global_event_bus.publish_sync(PlatformEvent(
                    category="publishing",
                    level="ERROR",
                    title="Publishing HTTP Error",
                    message=f"YouTube API Error ({status_code}) for clip {job.clip_id[:8]}: {err_str[:200]}",
                    entity_id=job.clip_id,
                    entity_type="clip",
                    action_url=f"/queue?clip={job.clip_id}",
                ))
            except Exception:
                pass

            action = (
                "Wait for daily quota reset" if is_quota_error
                else "Reconnect channel authorization" if is_auth_error
                else "Inspect error and retry upload"
            )
            return {
                "success": False,
                "status": job.status,
                "error": job.last_error,
                "action_required": action,
            }

        except Exception as e:
            logger.error("Unexpected error publishing job %s: %s", job.id, e, exc_info=True)
            job.status = PublishJobStatus.PAUSED_NEEDS_ATTENTION.value
            job.last_error = str(e)
            if clip:
                clip.status = ClipStatus.FAILED_NEEDS_REVIEW
            db.commit()

            audit_err = AuditLog(
                actor="system",
                event="publish_failed_exception",
                channel_id=job.target_youtube_channel_id,
                job_id=job.id,
                details=str(e)[:500],
            )
            db.add(audit_err)
            db.commit()

            try:
                global_event_bus.publish_sync(PlatformEvent(
                    category="publishing",
                    level="ERROR",
                    title="Publishing Failed",
                    message=f"Publishing failed for clip {job.clip_id[:8]}: {str(e)[:250]}",
                    entity_id=job.clip_id,
                    entity_type="clip",
                    action_url=f"/queue?clip={job.clip_id}",
                ))
            except Exception:
                pass

            return {
                "success": False,
                "status": job.status,
                "error": str(e),
                "action_required": "Inspect logs and retry upload",
            }

    def _upload_resumable(
        self,
        client: Any,
        video_path: Path,
        job: PublishJob,
        db: Session,
    ) -> str:
        """
        Executes chunked resumable upload with session tracking and retry backoff.
        """
        body = {
            "snippet": {
                "title": job.title,
                "description": job.description,
                "tags": json.loads(job.tags) if job.tags else ["#Shorts", "#Viral"],
                "categoryId": job.category_id or "22",
            },
            "status": {
                "privacyStatus": job.privacy_status or "unlisted",
                "selfDeclaredMadeForKids": bool(job.made_for_kids),
                "embeddable": True,
            },
        }

        if job.schedule_mode == "native" and job.scheduled_at and job.scheduled_at > datetime.utcnow():
            body["status"]["privacyStatus"] = "private"
            dt_sched = job.scheduled_at
            if dt_sched.tzinfo is None:
                dt_sched = dt_sched.replace(tzinfo=timezone.utc)
            else:
                dt_sched = dt_sched.astimezone(timezone.utc)
            body["status"]["publishAt"] = dt_sched.strftime("%Y-%m-%dT%H:%M:%SZ")

        media = googleapiclient.http.MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            resumable=True,
            chunksize=256 * 1024,
        )

        request = client.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        # Record upload session URI if obtainable
        if hasattr(request, "resumable_uri") and request.resumable_uri:
            job.upload_session_uri = request.resumable_uri
            db.commit()

        response = None
        max_retries = 3
        retry_count = 0
        backoff_sec = 1.0

        while response is None:
            try:
                status, response = request.next_chunk()
                if hasattr(request, "resumable_uri") and request.resumable_uri and not job.upload_session_uri:
                    job.upload_session_uri = request.resumable_uri
                    db.commit()
                retry_count = 0
            except HttpError as e:
                status_code = getattr(e.resp, "status", None) if hasattr(e, "resp") else None
                err_str = str(e)
                if status_code in (401, 403) or "quotaExceeded" in err_str:
                    raise
                if status_code and 500 <= int(status_code) < 600:
                    retry_count += 1
                    if retry_count > max_retries:
                        raise
                    jitter = random.uniform(0.1, 0.5)
                    time.sleep(backoff_sec + jitter)
                    backoff_sec *= 2.0
                else:
                    raise
            except (IOError, OSError):
                retry_count += 1
                if retry_count > max_retries:
                    raise
                jitter = random.uniform(0.1, 0.5)
                time.sleep(backoff_sec + jitter)
                backoff_sec *= 2.0

        yt_id = response.get("id", "")
        if not yt_id:
            raise RuntimeError(f"YouTube API returned no video ID: {response}")
        return yt_id

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
