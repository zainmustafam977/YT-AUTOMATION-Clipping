"""
YouTube Publish Worker & OAuth Isolation Module.
THIS MODULE IS THE SOLE MODULE AUTHORIZED TO TOUCH GOOGLE OAUTH TOKENS
AND THE YOUTUBE DATA API (v3).

Consumes intents from the durable queue:
1. 'publish': Resumable chunked video upload, idempotency verification, MadeForKids enforcement.
2. 'metadata_update': Video title update (videos.update = 50 units) & thumbnail update (thumbnails.set).
3. 'daily_health': Channel health check via channels.list (1 unit).

Enforces:
- Pacific-Time (America/Los_Angeles) aware Quota Ledger tracking.
- HTTP 403 quotaExceeded: defers job past Pacific midnight with jitter, emits alert event.
- OAuth invalid_grant: marks channel as 'needs_reauth', halts retries.
- Ambiguous failure handling: embeds [c:<clip_id>] tag, checks channel recent uploads before retry.
- Explicit selfDeclaredMadeForKids from configuration.
- Dry-run simulation mode without quota consumption.

W3-2 SCHEDULING SEMANTICS DECISION:
This module consumes publish intents dispatched from the durable queue at slot time.
Approval is enqueue-only (upload-at-slot-time) rather than in-request upload.
Rationale:
1. Quota Pacing: Daily YouTube API quotas are preserved and paced across days.
2. Slot Distribution: Approval queues jobs with `next_run_at=slot_start`.
3. If slot_start is already in the past at worker execution time, the worker publishes
   immediately without error and emits a `job.late_publish` event for observability.
"""

import os
import json
import random
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from sqlalchemy.orm import Session

# ==============================================================================
# EXCLUSIVE GOOGLE OAUTH & YOUTUBE CLIENT LIBRARY IMPORTS
# (No other module in shorts_engine may import these libraries)
# ==============================================================================
from googleapiclient.errors import HttpError
import google.oauth2.credentials
import googleapiclient.discovery
import googleapiclient.http
from google_auth_oauthlib.flow import Flow

from shorts_engine.config.settings import settings
from shorts_engine.db.models import (
    Clip,
    ClipStatus,
    Channel,
    ChannelCredential,
    PublishJob,
    PublishJobStatus,
    PublishLog,
    PipelineJob,
    PipelineJobStatus,
    PublishSlot,
    PublishSlotStatus,
)
from shorts_engine.db.security import decrypt_secret
from shorts_engine.services.publishing.quota import (
    QuotaTracker,
    get_next_pacific_midnight,
    VIDEOS_INSERT_COST_UNITS,
    VIDEOS_UPDATE_COST_UNITS,
)
from shorts_engine.services.events.bus import global_event_bus
from shorts_engine.services.events.models import PlatformEvent

logger = logging.getLogger(__name__)

# Request both scopes at auth time per specification
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


# ==============================================================================
# Helper & Identity Functions
# ==============================================================================

def format_idempotency_tag(clip_id: str) -> str:
    """Returns the unique idempotency tag embedded in video metadata."""
    return f"[c:{clip_id}]"


def build_youtube_client(
    token_or_refresh: Any,
    client_config: Optional[Dict[str, Any]] = None,
    scopes: Optional[List[str]] = None,
) -> Any:
    """
    Constructs an authenticated Google API Discovery service for YouTube v3.
    Accepts either a refresh token string or a token dictionary.
    """
    cfg = client_config or {}
    token_uri = cfg.get("token_uri", "https://oauth2.googleapis.com/token")
    client_id = cfg.get("client_id")
    client_secret = cfg.get("client_secret")
    effective_scopes = scopes or DEFAULT_SCOPES

    if isinstance(token_or_refresh, str):
        refresh_token = token_or_refresh
        access_token = None
    elif isinstance(token_or_refresh, dict):
        refresh_token = token_or_refresh.get("refresh_token")
        access_token = token_or_refresh.get("access_token")
        token_uri = token_or_refresh.get("token_uri", token_uri)
        client_id = token_or_refresh.get("client_id", client_id)
        client_secret = token_or_refresh.get("client_secret", client_secret)
        effective_scopes = token_or_refresh.get("scopes", effective_scopes)
    else:
        raise ValueError("token_or_refresh must be a string refresh token or dict")

    if not refresh_token:
        raise ValueError("Missing OAuth refresh token for YouTube client.")

    creds = google.oauth2.credentials.Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri=token_uri,
        client_id=client_id,
        client_secret=client_secret,
        scopes=effective_scopes,
    )
    return googleapiclient.discovery.build("youtube", "v3", credentials=creds, cache_discovery=False)


def build_youtube_analytics_client(
    token_or_refresh: Any,
    client_config: Optional[Dict[str, Any]] = None,
    scopes: Optional[List[str]] = None,
) -> Any:
    """
    Builds an authorized YouTube Analytics API v2 client using OAuth credentials.
    """
    config = client_config or get_default_client_config()
    installed = config.get("installed") or config.get("web") or {}
    client_id = installed.get("client_id")
    client_secret = installed.get("client_secret")
    token_uri = installed.get("token_uri", "https://oauth2.googleapis.com/token")
    effective_scopes = scopes or DEFAULT_SCOPES

    if isinstance(token_or_refresh, str):
        refresh_token = token_or_refresh
        access_token = None
    elif isinstance(token_or_refresh, dict):
        refresh_token = token_or_refresh.get("refresh_token")
        access_token = token_or_refresh.get("access_token")
    else:
        raise ValueError("token_or_refresh must be a string refresh token or dict")

    if not refresh_token:
        raise ValueError("Missing OAuth refresh token for YouTube Analytics client.")

    creds = google.oauth2.credentials.Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri=token_uri,
        client_id=client_id,
        client_secret=client_secret,
        scopes=effective_scopes,
    )
    return googleapiclient.discovery.build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)


def fetch_video_analytics_reports(
    client: Any,
    video_id: str,
    start_date: str,
    end_date: str,
) -> List[Dict[str, Any]]:
    """
    Queries YouTube Analytics API for daily video statistics between start_date and end_date.
    Returns list of dicts with day, views, impressions, click_through_rate, average_view_duration, subscribers_gained.
    """
    try:
        req = client.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            metrics="views,estimatedMinutesWatched,averageViewDuration,subscribersGained",
            dimensions="day",
            filters=f"video=={video_id}",
            sort="day",
        )
        res = req.execute()
    except Exception as e:
        logger.error("Failed to query YouTube Analytics API for video %s: %s", video_id, e)
        raise

    col_headers = [col.get("name") if isinstance(col, dict) else str(col) for col in res.get("columnHeaders", [])]
    rows = res.get("rows", [])
    records = []
    for row in rows:
        row_dict = dict(zip(col_headers, row))
        records.append(row_dict)
    return records


def get_channel_refresh_token(channel: Channel, db: Session) -> Optional[str]:
    """Retrieves and decrypts the active refresh token for a channel."""
    cred = db.query(ChannelCredential).filter_by(channel_id=channel.id).first()
    if cred and cred.encrypted_refresh_token:
        try:
            return decrypt_secret(cred.encrypted_refresh_token)
        except Exception as e:
            logger.warning("Decryption failed for channel %s refresh token: %s", channel.id, e)
    if channel.encrypted_oauth_token:
        try:
            token_dict = json.loads(decrypt_secret(channel.encrypted_oauth_token))
            return token_dict.get("refresh_token")
        except Exception:
            pass
    return None


def _upload_video(
    client: Any,
    video_path: Path,
    metadata: Any,
    privacy_status: str = "unlisted",
) -> str:
    """
    Calls YouTube Data API v3 videos.insert() to upload the rendered clip.
    Supports instant public/unlisted/private uploads as well as scheduled publishAt releases.
    Returns the published YouTube video ID.
    """
    body = metadata.to_dict() if hasattr(metadata, "to_dict") else dict(metadata)
    if privacy_status and not getattr(metadata, "publish_at", None):
        if "status" not in body:
            body["status"] = {}
        body["status"]["privacyStatus"] = privacy_status

    media = googleapiclient.http.MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=256 * 1024,  # 256 KB chunks
    )

    request = client.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    response = None
    max_retries = 3
    retry_count = 0
    backoff_sec = 1.0

    while response is None:
        try:
            _, response = request.next_chunk()
            retry_count = 0
        except HttpError as e:
            status_code = getattr(e.resp, "status", None) if hasattr(e, "resp") else None
            err_content = str(e)
            if status_code in (401, 403) or "quotaExceeded" in err_content:
                logger.error("Fast-failing YouTube upload chunk on non-retryable error (status=%s): %s", status_code, e)
                raise

            if status_code and 500 <= int(status_code) < 600:
                retry_count += 1
                if retry_count > max_retries:
                    logger.error("Exceeded max chunk upload retries (%d) on 5xx error: %s", max_retries, e)
                    raise
                logger.warning(
                    "Transient 5xx error (%s) during chunk upload. Retrying in %.1fs (attempt %d/%d)...",
                    status_code, backoff_sec, retry_count, max_retries,
                )
                time.sleep(backoff_sec)
                backoff_sec *= 2.0
            else:
                raise
        except (IOError, OSError) as e:
            retry_count += 1
            if retry_count > max_retries:
                logger.error("Exceeded max chunk upload retries (%d) on socket/IO error: %s", max_retries, e)
                raise
            logger.warning(
                "Socket/IO error during chunk upload: %s. Retrying in %.1fs (attempt %d/%d)...",
                e, backoff_sec, retry_count, max_retries,
            )
            time.sleep(backoff_sec)
            backoff_sec *= 2.0

    youtube_video_id: str = response.get("id", "")
    if not youtube_video_id:
        raise RuntimeError(f"YouTube API returned no video ID. Full response: {response}")

    return youtube_video_id


def set_video_thumbnail(client: Any, youtube_video_id: str, thumb_path: Path) -> Any:
    """Sets custom thumbnail cover on YouTube video via thumbnails.set."""
    media = googleapiclient.http.MediaFileUpload(str(thumb_path), mimetype="image/jpeg", resumable=True)
    req = client.thumbnails().set(videoId=youtube_video_id, media_body=media)
    return req.execute()


def create_oauth_flow(
    client_config: Dict[str, Any],
    scopes: Optional[List[str]] = None,
    redirect_uri: str = "http://127.0.0.1:8000/oauth2callback",
) -> Flow:
    """Factory creating Google OAuth Flow instance with standardized scopes."""
    effective_scopes = scopes or DEFAULT_SCOPES
    return Flow.from_client_config(
        client_config,
        scopes=effective_scopes,
        redirect_uri=redirect_uri,
    )


def fetch_channel_identity(
    token_data: Dict[str, Any],
    client_config: Optional[Dict[str, Any]] = None,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """
    Queries channels.list(mine=True) to extract the authorized Google channel identity.
    Records 1 quota unit in QuotaLedger.
    """
    client = build_youtube_client(token_data, client_config=client_config)
    res = client.channels().list(part="id,snippet", mine=True).execute()

    items = res.get("items", [])
    if not items:
        raise RuntimeError("YouTube API channels.list(mine=true) returned no channels.")

    item = items[0]
    channel_id = item["id"]
    snippet = item.get("snippet", {})
    title = snippet.get("title", "Untitled Channel")
    handle = snippet.get("customUrl", "")
    thumbnails = snippet.get("thumbnails", {})
    avatar_url = thumbnails.get("default", {}).get("url") or thumbnails.get("high", {}).get("url", "")

    QuotaTracker().record_channels_list(channel_id=channel_id, db=db)

    return {
        "channel_id": channel_id,
        "title": title,
        "handle": handle,
        "avatar_url": avatar_url,
    }


def verify_channel_credentials(
    channel: Channel,
    db: Session,
    client_config: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Validates token refresh & identity match against the target channel.
    Returns (is_valid, error_reason).
    """
    cred = db.query(ChannelCredential).filter_by(channel_id=channel.id).first()
    refresh_token = None
    if cred and cred.encrypted_refresh_token:
        try:
            refresh_token = decrypt_secret(cred.encrypted_refresh_token)
        except Exception:
            pass
    if not refresh_token and channel.encrypted_oauth_token:
        try:
            legacy = json.loads(decrypt_secret(channel.encrypted_oauth_token))
            refresh_token = legacy.get("refresh_token")
        except Exception:
            pass

    if not refresh_token:
        if cred:
            cred.status = "needs_auth"
            cred.last_error = "Missing refresh token"
            db.commit()
        return False, "Missing OAuth refresh token"

    QuotaTracker().record_channels_list(channel_id=channel.youtube_channel_id, db=db)

    try:
        client = build_youtube_client(refresh_token, client_config=client_config)
        res = client.channels().list(part="id,snippet", mine=True).execute()
        items = res.get("items", [])
        if not items:
            return False, "Channel not found in authorized Google account"
        actual_id = items[0]["id"]
        if channel.youtube_channel_id and actual_id != channel.youtube_channel_id:
            if cred:
                cred.status = "identity_mismatch"
                cred.last_error = f"Authorized channel is '{actual_id}', expected '{channel.youtube_channel_id}'"
                db.commit()
            return False, f"Identity mismatch: {actual_id} != {channel.youtube_channel_id}"

        if cred:
            cred.status = "active"
            cred.verified_at = datetime.utcnow()
            cred.last_error = None
        channel.last_verified_at = datetime.utcnow()
        db.commit()
        return True, None
    except HttpError as e:
        err_str = str(e)
        if "invalid_grant" in err_str:
            if cred:
                cred.status = "needs_reauth"
                cred.last_error = err_str
            channel.status = "needs_reauth"
            db.commit()
            return False, f"invalid_grant: {err_str}"
        return False, err_str
    except Exception as e:
        return False, str(e)


def check_channel_recent_uploads_for_clip(
    client: Any,
    channel_youtube_id: str,
    clip_id: str,
    quota_tracker: Optional[QuotaTracker] = None,
    db: Optional[Session] = None,
) -> Optional[str]:
    """
    Ambiguous failure protection: Queries the channel's uploads playlist or recent videos
    to detect if a video with tag [c:<clip_id>] or [clip:<clip_id>] was already created.
    """
    token_tag = format_idempotency_tag(clip_id)
    legacy_tag = f"[clip:{clip_id}]"

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
            maxResults=30,
        ).execute()

        for item in playlist_res.get("items", []):
            desc = item.get("snippet", {}).get("description", "")
            if token_tag in desc or legacy_tag in desc:
                yt_id = item.get("snippet", {}).get("resourceId", {}).get("videoId")
                logger.info("Found existing uploaded video %s for clip %s via tag inspection.", yt_id, clip_id)
                return yt_id
    except Exception as e:
        logger.warning("Could not inspect uploads playlist for clip %s: %s", clip_id, e)

    return None


# ==============================================================================
# YouTube Publish Worker Engine
# ==============================================================================

from shorts_engine.orchestration.exceptions import PermanentError, JobCancelledError


class YouTubePublishWorker:
    """
    Enterprise publish worker process executing all video uploads and metadata mutations.
    Strictly isolated: The only code touching tokens and the YouTube Data API.
    """

    def __init__(
        self,
        quota_tracker: Optional[QuotaTracker] = None,
        dry_run: Optional[bool] = None,
    ):
        self.quota_tracker = quota_tracker or QuotaTracker()
        self.dry_run = dry_run if dry_run is not None else getattr(settings, "DRY_RUN_PUBLISHING", False)

    def _resolve_client_config(self) -> Dict[str, Any]:
        """Loads and decrypts Google OAuth client secrets configuration."""
        env_json = os.environ.get("YOUTUBE_CLIENT_SECRET_JSON")
        if env_json and env_json.strip().startswith("{"):
            try:
                return json.loads(env_json.strip())
            except Exception:
                pass

        storage_dir = Path(settings.STORAGE_DIR)
        candidates = [
            storage_dir / "client_secret.enc",
            storage_dir / "client_secret.json.enc",
            storage_dir / "client_secret.json",
        ]
        for c in candidates:
            if c.exists() and c.is_file() and c.stat().st_size > 0:
                content = c.read_text(encoding="utf-8").strip()
                if content.startswith("{"):
                    data = json.loads(content)
                else:
                    from shorts_engine.security.secretbox import decrypt
                    data = json.loads(decrypt(content))
                if "web" in data:
                    return data["web"]
                elif "installed" in data:
                    return data["installed"]
                return data

        return {
            "client_id": getattr(settings, "YOUTUBE_CLIENT_ID", None),
            "client_secret": getattr(settings, "YOUTUBE_CLIENT_SECRET", None),
            "token_uri": "https://oauth2.googleapis.com/token",
        }

    def _get_channel_refresh_token(self, channel: Channel, db: Session) -> Optional[str]:
        """Retrieves and decrypts the active refresh token for a channel."""
        return get_channel_refresh_token(channel, db)

    def _handle_quota_exceeded(
        self,
        job: Any,
        db: Session,
        err_msg: str,
    ) -> None:
        """
        Defers job execution past Pacific midnight (midnight America/Los_Angeles)
        with randomized jitter. Emits high-priority platform alert.
        """
        midnight_utc, reset_iso = get_next_pacific_midnight()
        # Add 15 to 90 seconds jitter past midnight
        deferred_run_at = midnight_utc + timedelta(seconds=random.randint(15, 90))

        logger.warning(
            "YouTube quota exceeded (403 quotaExceeded). Deferring job %s past Pacific midnight to %s UTC. Error: %s",
            getattr(job, "id", "unknown"), deferred_run_at.isoformat(), err_msg,
        )

        if hasattr(job, "next_run_at"):
            job.next_run_at = deferred_run_at
        if hasattr(job, "status"):
            # Set to PENDING so the claim loop picks it up once next_run_at arrives
            if isinstance(getattr(job, "status", None), PipelineJobStatus):
                job.status = PipelineJobStatus.PENDING
            else:
                job.status = "PENDING"
        if hasattr(job, "error_message"):
            job.error_message = f"Quota exceeded. Deferred until Pacific midnight ({reset_iso}): {err_msg[:200]}"
        elif hasattr(job, "last_error"):
            job.last_error = f"Quota exceeded. Deferred until Pacific midnight ({reset_iso}): {err_msg[:200]}"

        db.commit()

        try:
            from shorts_engine.services.events.taxonomy import emit_platform_event, QUOTA_LOW
            job_id = getattr(job, "id", None)
            emit_platform_event(
                event_name=QUOTA_LOW,
                payload={
                    "job_id": job_id,
                    "next_run_at": deferred_run_at.isoformat(),
                    "reset_iso": reset_iso,
                    "error": str(err_msg),
                },
                entity_id=job_id,
                entity_type="job",
                level="WARNING",
                title="YouTube Quota Exceeded — Job Deferred",
                message=f"YouTube daily quota limit reached. Job deferred until Pacific midnight ({reset_iso}).",
                db=db,
            )
        except Exception:
            pass

    def _handle_invalid_grant(
        self,
        channel: Channel,
        db: Session,
        err_msg: str,
        job: Optional[Any] = None,
    ) -> None:
        """
        Marks channel credential as 'needs_reauth', halts retries, and quarantines job.
        """
        logger.error(
            "Channel %s encountered invalid_grant. Marking 'needs_reauth' and parking retries. Error: %s",
            channel.youtube_channel_id, err_msg,
        )

        cred = db.query(ChannelCredential).filter_by(channel_id=channel.id).first()
        if cred:
            cred.status = "needs_reauth"
            cred.last_error = f"OAuth invalid_grant: {err_msg}"

        channel.status = "needs_reauth"

        if job:
            if hasattr(job, "status"):
                if isinstance(getattr(job, "status", None), PipelineJobStatus):
                    job.status = PipelineJobStatus.QUARANTINED
                else:
                    job.status = "QUARANTINED"
            if hasattr(job, "error_message"):
                job.error_message = f"Channel requires re-authentication: {err_msg}"
            elif hasattr(job, "last_error"):
                job.last_error = f"Channel requires re-authentication: {err_msg}"

        db.commit()

        try:
            global_event_bus.publish_sync(PlatformEvent(
                category="auth",
                level="ERROR",
                title=f"Channel Needs Re-Authentication: {channel.display_title}",
                message="Google OAuth refresh token revoked or expired (invalid_grant). Reconnection required.",
                action_url="/channels",
            ))
        except Exception:
            pass

    # ==========================================================================
    # Intent 1: 'publish' Job Execution
    # ==========================================================================

    def execute_publish_intent(
        self,
        clip_id: str,
        payload: Dict[str, Any],
        db: Session,
        job_entity: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Executes full video upload workflow with resumable upload, idempotency,
        selfDeclaredMadeForKids enforcement, and quota ledger tracking.
        """
        clip = db.query(Clip).filter_by(id=clip_id).first()
        if not clip:
            raise PermanentError(f"Target Clip not found: {clip_id}")

        cand = clip.candidate
        video = cand.source_video if cand else None
        channel = video.channel if video and video.channel else None

        if not channel:
            # Fall back to default active channel
            channel = db.query(Channel).first()

        if not channel:
            raise PermanentError("No YouTube Channel configured for publishing.")

        # Mandatory slot binding validation (I2)
        slot_id = payload.get("slot_id")
        if not slot_id:
            raise PermanentError(f"Missing mandatory slot_id binding in publish payload for clip {clip_id}")

        slot = db.query(PublishSlot).filter_by(id=slot_id).first()
        if not slot:
            raise PermanentError(f"Corrupt slot binding: slot {slot_id} not found in database for clip {clip_id}")

        if slot.channel_id != channel.id:
            raise PermanentError(f"Slot channel mismatch: slot {slot_id} channel {slot.channel_id} does not match clip channel {channel.id}")

        # If slot was CANCELLED or clip status was reverted / rejected: fail fast with JobCancelledError
        if slot.status == PublishSlotStatus.CANCELLED.value or clip.status in (
            ClipStatus.WAITING_APPROVAL,
            ClipStatus.REJECTED,
        ):
            logger.info(
                "Publish intent cancelled: slot %s is %s, clip %s is %s. Failing fast.",
                slot_id, slot.status, clip.id, clip.status.value,
            )
            if job_entity and hasattr(job_entity, "status"):
                job_entity.status = PipelineJobStatus.CANCELLED
                job_entity.completed_at = datetime.utcnow()
                job_entity.locked_by = None
                job_entity.locked_at = None
                db.commit()
            raise JobCancelledError(f"Publish cancelled: slot {slot_id} status {slot.status}, clip {clip_id} status {clip.status.value}")

        eligible_statuses = [ClipStatus.APPROVED, ClipStatus.SCHEDULED, ClipStatus.PUBLISHING]
        if clip.status not in eligible_statuses:
            raise PermanentError(f"Clip {clip_id} in ineligible status {clip.status.value} for publishing")

        # Check channel authorization status
        cred = db.query(ChannelCredential).filter_by(channel_id=channel.id).first()
        if cred and cred.status == "needs_reauth":
            logger.warning("Refusing publish for channel %s in needs_reauth state.", channel.youtube_channel_id)
            if job_entity:
                if hasattr(job_entity, "status"):
                    job_entity.status = PipelineJobStatus.QUARANTINED
                job_entity.error_message = "Channel is marked needs_reauth. Halting execution."
                db.commit()
            return {"success": False, "status": "QUARANTINED", "error": "Channel needs re-authentication."}

        # Resolve video path
        video_path = Path(clip.rendered_path) if clip.rendered_path else None
        if not video_path or not video_path.exists() or video_path.stat().st_size == 0:
            raise FileNotFoundError(f"Rendered video file missing for clip {clip_id}: {clip.rendered_path}")

        # Metadata preparation
        title = payload.get("title") or clip.draft_title or clip.generated_title or (cand.hook_text if cand else "Viral Clip")
        clean_title = title[:100]

        tag = format_idempotency_tag(clip.id)
        base_desc = payload.get("description") or clip.draft_description or clip.generated_description or f"Highlight clip from {video.title if video else 'video'} #Shorts"
        if tag not in base_desc:
            description = f"{base_desc}\n\n{tag}".strip()
        else:
            description = base_desc

        tags = payload.get("tags")
        if not tags and clip.generated_tags:
            try:
                tags = json.loads(clip.generated_tags)
            except Exception:
                tags = ["#Shorts", "#Viral"]
        tags = tags or ["#Shorts", "#Viral"]

        # Requirement 5: Set selfDeclaredMadeForKids explicitly from config on every upload
        made_for_kids_config = bool(
            payload.get("made_for_kids")
            if "made_for_kids" in payload
            else getattr(settings, "YOUTUBE_MADE_FOR_KIDS", False)
        )

        privacy_status = payload.get("privacy_status") or clip.draft_privacy_status or "unlisted"
        category_id = str(payload.get("category_id") or clip.draft_category_id or "22")

        # Resolve and validate scheduled slot time (supports ISO string and datetime)
        sched_dt: Optional[datetime] = None
        raw_sched = payload.get("scheduled_at") or clip.scheduled_publish_at
        if isinstance(raw_sched, str):
            try:
                sched_dt = datetime.fromisoformat(raw_sched)
            except Exception:
                sched_dt = None
        elif isinstance(raw_sched, datetime):
            sched_dt = raw_sched

        # If slot_start is already in the past at execution time, publish immediately
        # without error and emit job.late_publish event for observability (W3-2 A2).
        if sched_dt:
            sched_naive = sched_dt.replace(tzinfo=None) if sched_dt.tzinfo else sched_dt
            now_utc = datetime.utcnow()
            if sched_naive <= now_utc:
                logger.info(
                    "Execution start for clip %s is later than scheduled slot (%s <= %s). Publishing immediately.",
                    clip.id, sched_naive.isoformat(), now_utc.isoformat(),
                )
                try:
                    from shorts_engine.services.events.taxonomy import emit_platform_event, JOB_LATE_PUBLISH
                    emit_platform_event(
                        event_name=JOB_LATE_PUBLISH,
                        payload={
                            "clip_id": clip.id,
                            "scheduled_at": sched_naive.isoformat(),
                            "executed_at": now_utc.isoformat(),
                            "delay_seconds": (now_utc - sched_naive).total_seconds(),
                            "message": f"Publish execution started later than slot_start ({sched_naive.isoformat()}). Publishing immediately.",
                        },
                        entity_id=clip.id,
                        entity_type="clip",
                        level="WARNING",
                        title="Late Publish Execution",
                        message=f"Publish execution started later than slot_start ({sched_naive.isoformat()}). Publishing immediately.",
                        db=db,
                    )
                except Exception as telem_err:
                    logger.warning("Failed to emit JOB_LATE_PUBLISH event for clip %s: %s", clip.id, telem_err)

        # Handle DRY-RUN mode (I5: sandbox-only preview execution)
        is_dry_run = self.dry_run or payload.get("dry_run", False)
        if is_dry_run:
            allow_preview_flag = os.environ.get("ALLOW_IN_PROCESS_PREVIEW", "").lower() in ("1", "true", "yes")
            bind_url = str(db.get_bind().url).lower()
            is_non_prod_db = (
                ":memory:" in bind_url
                or "tmp" in bind_url
                or "test" in bind_url
                or "pytest" in bind_url
            )
            if not (allow_preview_flag and is_non_prod_db):
                raise PermanentError(
                    "Dry-run simulation mode is only permitted when ALLOW_IN_PROCESS_PREVIEW=1 is set "
                    "and execution is connected to an isolated test/in-memory database."
                )

            logger.info(
                "[DRY-RUN] Simulating YouTube publish for clip %s (Title: '%s', MadeForKids: %s, Privacy: %s)",
                clip.id, clean_title, made_for_kids_config, privacy_status,
            )
            simulated_id = f"dry_run_{clip.id[:8]}"
            # Preview mode: NEVER mark clip status PUBLISHED, NEVER change slot status, NEVER emit CLIP_PUBLISHED
            # Create PublishLog with status="DRY_RUN" ONLY in test DB
            p_log = db.query(PublishLog).filter_by(clip_id=clip.id).first()
            if not p_log:
                p_log = PublishLog(
                    clip_id=clip.id,
                    youtube_video_id=simulated_id,
                    published_at=datetime.utcnow(),
                    title=clean_title,
                    description=description,
                    status="DRY_RUN",
                )
                db.add(p_log)
            else:
                p_log.youtube_video_id = simulated_id
                p_log.status = "DRY_RUN"
            db.commit()

            try:
                from shorts_engine.services.events.taxonomy import emit_platform_event, CLIP_PUBLISHED
                emit_platform_event(
                    event_name=CLIP_PUBLISHED,
                    payload={
                        "clip_id": clip.id,
                        "youtube_video_id": simulated_id,
                        "channel_id": channel.youtube_channel_id,
                        "title": clean_title,
                        "youtube_url": f"https://youtube.com/shorts/{simulated_id}",
                        "dry_run": True,
                    },
                    entity_id=clip.id,
                    entity_type="clip",
                    level="SUCCESS",
                    title="Clip Published (Dry Run)",
                    message=f"[DRY-RUN] Clip '{clean_title}' simulated as {simulated_id}.",
                    db=db,
                )
            except Exception:
                pass

            return {
                "success": True,
                "status": "DRY_RUN",
                "youtube_video_id": simulated_id,
                "dry_run": True,
            }

        # Resolve credentials & YouTube client
        refresh_token = self._get_channel_refresh_token(channel, db)
        if not refresh_token:
            self._handle_invalid_grant(channel, db, "Missing refresh token", job=job_entity)
            return {"success": False, "status": "QUARANTINED", "error": "No OAuth refresh token"}

        client_config = self._resolve_client_config()
        client = build_youtube_client(refresh_token, client_config=client_config)

        # Requirement 4 & I6: Ambiguous-failure handling — check recent uploads for idempotency tag before uploading
        existing_video_id = check_channel_recent_uploads_for_clip(
            client=client,
            channel_youtube_id=channel.youtube_channel_id,
            clip_id=clip.id,
            quota_tracker=self.quota_tracker,
            db=db,
        )
        if existing_video_id:
            logger.info("Found existing video %s with tag %s. Reclaiming without re-upload.", existing_video_id, tag)
            is_future_schedule = bool(sched_dt and (sched_dt.replace(tzinfo=None) if sched_dt.tzinfo else sched_dt) > datetime.utcnow())
            if is_future_schedule:
                clip.status = ClipStatus.SCHEDULED
                slot.status = PublishSlotStatus.SCHEDULED.value
            else:
                clip.status = ClipStatus.PUBLISHED
                slot.status = PublishSlotStatus.PUBLISHED.value

            slot.youtube_video_id = existing_video_id
            clip.youtube_video_id = existing_video_id

            p_log = db.query(PublishLog).filter_by(clip_id=clip.id).first()
            if not p_log:
                p_log = PublishLog(
                    clip_id=clip.id,
                    youtube_video_id=existing_video_id,
                    published_at=datetime.utcnow(),
                    title=clean_title,
                    description=description,
                    status="SUCCESS",
                )
                db.add(p_log)
            else:
                p_log.youtube_video_id = existing_video_id
                p_log.published_at = datetime.utcnow()
                p_log.status = "SUCCESS"

            db.commit()

            try:
                from shorts_engine.services.events.taxonomy import emit_platform_event, CLIP_PUBLISHED
                emit_platform_event(
                    event_name=CLIP_PUBLISHED,
                    payload={
                        "clip_id": clip.id,
                        "youtube_video_id": existing_video_id,
                        "channel_id": channel.youtube_channel_id,
                        "title": clean_title,
                        "youtube_url": f"https://youtube.com/shorts/{existing_video_id}",
                        "reclaimed": True,
                    },
                    entity_id=clip.id,
                    entity_type="clip",
                    level="SUCCESS",
                    title="Clip Published (Reclaimed)",
                    message=f"Clip '{clean_title}' reclaimed as {existing_video_id}.",
                    db=db,
                )
            except Exception:
                pass
            return {
                "success": True,
                "status": clip.status.value,
                "youtube_video_id": existing_video_id,
                "reclaimed": True,
            }

        # Check quota availability
        can_pub, remaining_quota, quota_reason = self.quota_tracker.can_publish(db=db, cost=VIDEOS_INSERT_COST_UNITS)
        if not can_pub:
            self._handle_quota_exceeded(job_entity, db, quota_reason)
            return {"success": False, "status": "DEFERRED", "error": quota_reason}

        # Build YouTube insert request body
        body = {
            "snippet": {
                "title": clean_title,
                "description": description,
                "tags": tags,
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": privacy_status,
                # Explicit selfDeclaredMadeForKids on every upload
                "selfDeclaredMadeForKids": made_for_kids_config,
                "embeddable": True,
            },
        }

        # Native scheduled release support
        if sched_dt:
            sched_naive = sched_dt.replace(tzinfo=None) if sched_dt.tzinfo else sched_dt
            if sched_naive > datetime.utcnow():
                body["status"]["privacyStatus"] = "private"
                dt_sched = sched_dt
                if dt_sched.tzinfo is None:
                    dt_sched = dt_sched.replace(tzinfo=timezone.utc)
                body["status"]["publishAt"] = dt_sched.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Resumable protocol setup
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

        # Restore persisted upload session URI if previously crashed
        saved_session_uri = payload.get("upload_session_uri")
        if saved_session_uri and hasattr(request, "resumable_uri"):
            request.resumable_uri = saved_session_uri

        response = None
        max_retries = 3
        retry_count = 0
        backoff_sec = 1.0

        while response is None:
            try:
                status, response = request.next_chunk()
                # Persist upload session URI as soon as received
                current_uri = getattr(request, "resumable_uri", None)
                if current_uri and current_uri != payload.get("upload_session_uri"):
                    payload["upload_session_uri"] = current_uri
                    if job_entity and hasattr(job_entity, "payload_json"):
                        job_entity.payload_json = json.dumps(payload)
                        db.commit()
                retry_count = 0
            except HttpError as e:
                raw_content = getattr(e, "content", b"") or b""
                content_str = raw_content.decode("utf-8", errors="ignore") if isinstance(raw_content, bytes) else str(raw_content)
                err_str = f"{str(e)} {content_str}".strip()
                try:
                    status_code = int(getattr(e.resp, "status", 0))
                except (ValueError, TypeError):
                    status_code = 0

                # Check for 403 quotaExceeded
                if status_code == 403 and ("quota" in err_str.lower() or "uploadlimitexceeded" in err_str.lower()):
                    self._handle_quota_exceeded(job_entity, db, err_str)
                    return {"success": False, "status": "DEFERRED", "error": err_str}

                # Check for invalid_grant
                if "invalid_grant" in err_str or status_code == 401:
                    self._handle_invalid_grant(channel, db, err_str, job=job_entity)
                    return {"success": False, "status": "QUARANTINED", "error": err_str}

                # 5xx server errors during upload
                if 500 <= status_code < 600:
                    retry_count += 1
                    if retry_count > max_retries:
                        raise
                    time.sleep(backoff_sec + random.uniform(0.1, 0.5))
                    backoff_sec *= 2.0
                else:
                    raise

            except (IOError, OSError, TimeoutError) as net_err:
                # Ambiguous network failure: connection dropped or timed out
                logger.warning("Ambiguous network failure during upload for clip %s: %s", clip.id, net_err)
                # Verify whether the video was actually created before blind retry
                recovered_id = check_channel_recent_uploads_for_clip(
                    client=client,
                    channel_youtube_id=channel.youtube_channel_id,
                    clip_id=clip.id,
                    quota_tracker=self.quota_tracker,
                    db=db,
                )
                if recovered_id:
                    logger.info("Video %s was created despite network error! Reclaiming.", recovered_id)
                    response = {"id": recovered_id}
                    break

                retry_count += 1
                if retry_count > max_retries:
                    raise
                time.sleep(backoff_sec + random.uniform(0.1, 0.5))
                backoff_sec *= 2.0

        youtube_video_id = response.get("id") if response else None
        if not youtube_video_id:
            raise RuntimeError(f"YouTube upload finished with no video ID. Response: {response}")

        # Success state transitions (I6)
        is_future_schedule = bool(sched_dt and (sched_dt.replace(tzinfo=None) if sched_dt.tzinfo else sched_dt) > datetime.utcnow())
        if is_future_schedule:
            clip.status = ClipStatus.SCHEDULED
            slot.status = PublishSlotStatus.SCHEDULED.value
        else:
            clip.status = ClipStatus.PUBLISHED
            slot.status = PublishSlotStatus.PUBLISHED.value

        slot.youtube_video_id = youtube_video_id
        clip.youtube_video_id = youtube_video_id

        # Record quota consumption (1,600 units)
        self.quota_tracker.record_publish(
            cost=VIDEOS_INSERT_COST_UNITS,
            channel_id=channel.youtube_channel_id,
            job_id=getattr(job_entity, "id", None),
            db=db,
        )

        # Record PublishLog
        p_log = db.query(PublishLog).filter_by(clip_id=clip.id).first()
        if not p_log:
            p_log = PublishLog(
                clip_id=clip.id,
                youtube_video_id=youtube_video_id,
                published_at=datetime.utcnow(),
                title=clean_title,
                description=description,
                status="SUCCESS",
            )
            db.add(p_log)
        else:
            p_log.youtube_video_id = youtube_video_id
            p_log.published_at = datetime.utcnow()
            p_log.status = "SUCCESS"
        db.commit()

        try:
            from shorts_engine.services.events.taxonomy import emit_platform_event, CLIP_PUBLISHED
            emit_platform_event(
                event_name=CLIP_PUBLISHED,
                payload={
                    "clip_id": clip.id,
                    "youtube_video_id": youtube_video_id,
                    "channel_id": channel.youtube_channel_id,
                    "title": clean_title,
                    "youtube_url": f"https://youtube.com/shorts/{youtube_video_id}",
                },
                entity_id=clip.id,
                entity_type="clip",
                level="SUCCESS",
                title="Clip Published",
                message=f"Clip '{clean_title}' published to YouTube as {youtube_video_id}.",
                db=db,
            )
        except Exception as e:
            logger.warning("Failed to emit CLIP_PUBLISHED event for clip %s: %s", clip.id, e)

        logger.info("Successfully published clip %s to YouTube (Video ID: %s)", clip.id, youtube_video_id)
        return {
            "success": True,
            "status": "PUBLISHED",
            "youtube_video_id": youtube_video_id,
            "youtube_url": f"https://youtube.com/shorts/{youtube_video_id}",
        }

    # ==========================================================================
    # Intent 2: 'metadata_update' Job Execution
    # ==========================================================================

    def execute_metadata_update_intent(
        self,
        clip_id: str,
        payload: Dict[str, Any],
        db: Session,
        job_entity: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Executes video metadata modification (videos.update = 50 units) and
        thumbnail promotion (thumbnails.set). Used by winner_daemon.
        """
        youtube_video_id = payload.get("youtube_video_id")
        if not youtube_video_id:
            # Look up via PublishLog
            p_log = db.query(PublishLog).filter_by(clip_id=clip_id).first()
            if p_log:
                youtube_video_id = p_log.youtube_video_id

        if not youtube_video_id:
            raise ValueError(f"Cannot update metadata: No YouTube video ID found for clip {clip_id}")

        clip = db.query(Clip).filter_by(id=clip_id).first()
        channel = clip.candidate.source_video.channel if (clip and clip.candidate and clip.candidate.source_video) else None
        if not channel:
            channel = db.query(Channel).first()

        new_title = payload.get("title")
        new_thumb = payload.get("thumbnail_path")

        is_dry_run = self.dry_run or payload.get("dry_run", False)
        if is_dry_run:
            logger.info(
                "[DRY-RUN] Simulating metadata update for video %s: title='%s', thumb='%s'",
                youtube_video_id, new_title, new_thumb,
            )
            return {"success": True, "dry_run": True, "youtube_video_id": youtube_video_id}

        refresh_token = self._get_channel_refresh_token(channel, db) if channel else None
        if not refresh_token:
            if channel:
                self._handle_invalid_grant(channel, db, "Missing refresh token for metadata update", job=job_entity)
            return {"success": False, "status": "QUARANTINED", "error": "No OAuth refresh token"}

        client = build_youtube_client(refresh_token, client_config=self._resolve_client_config())

        # 1. Title / Snippet Update (videos.update = 50 units)
        if new_title:
            try:
                get_req = client.videos().list(part="snippet", id=youtube_video_id)
                get_resp = get_req.execute()
                items = get_resp.get("items", [])
                if not items:
                    raise RuntimeError(f"Video {youtube_video_id} not found on YouTube.")

                snippet = items[0]["snippet"]
                snippet["title"] = str(new_title)[:100]

                update_req = client.videos().update(
                    part="snippet",
                    body={"id": youtube_video_id, "snippet": snippet},
                )
                update_req.execute()

                # Record 50 units in QuotaLedger
                self.quota_tracker.record_videos_update(
                    cost=VIDEOS_UPDATE_COST_UNITS,
                    channel_id=channel.youtube_channel_id if channel else None,
                    job_id=getattr(job_entity, "id", None),
                    db=db,
                )
                logger.info("Updated title for video %s -> '%s'", youtube_video_id, new_title)
            except HttpError as e:
                raw_content = getattr(e, "content", b"") or b""
                content_str = raw_content.decode("utf-8", errors="ignore") if isinstance(raw_content, bytes) else str(raw_content)
                err_str = f"{str(e)} {content_str}".strip()
                try:
                    status_code = int(getattr(e.resp, "status", 0))
                except (ValueError, TypeError):
                    status_code = 0
                if status_code == 403 and ("quota" in err_str.lower() or "uploadlimitexceeded" in err_str.lower()):
                    self._handle_quota_exceeded(job_entity, db, err_str)
                    return {"success": False, "status": "DEFERRED", "error": err_str}
                if "invalid_grant" in err_str or status_code == 401:
                    if channel:
                        self._handle_invalid_grant(channel, db, err_str, job=job_entity)
                    return {"success": False, "status": "QUARANTINED", "error": err_str}
                raise

        # 2. Thumbnail Cover Update (thumbnails.set)
        if new_thumb and Path(new_thumb).exists():
            try:
                media = googleapiclient.http.MediaFileUpload(str(new_thumb), mimetype="image/jpeg", resumable=True)
                thumb_req = client.thumbnails().set(videoId=youtube_video_id, media_body=media)
                thumb_req.execute()
                logger.info("Updated custom thumbnail for video %s -> %s", youtube_video_id, new_thumb)
            except HttpError as e:
                logger.warning("Could not update thumbnail for video %s: %s", youtube_video_id, e)

        return {"success": True, "youtube_video_id": youtube_video_id, "title": new_title}

    def execute_delete_intent(
        self,
        youtube_video_id: str,
        channel_id: Optional[str] = None,
        db: Optional[Session] = None,
        job_entity: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Deletes a video from YouTube (videos.delete = 50 units default).
        Used when un-approving a clip that was already uploaded.
        """
        if not youtube_video_id:
            return {"success": False, "error": "No youtube_video_id provided"}

        channel = None
        if channel_id and db:
            channel = db.query(Channel).filter(
                (Channel.id == channel_id) | (Channel.youtube_channel_id == channel_id)
            ).first()
        if not channel and db:
            channel = db.query(Channel).first()

        is_dry_run = self.dry_run or youtube_video_id.startswith("dry_run_")
        if is_dry_run:
            logger.info("[DRY-RUN] Simulating videos().delete for %s", youtube_video_id)
            return {"success": True, "dry_run": True, "youtube_video_id": youtube_video_id, "deleted": True}

        refresh_token = self._get_channel_refresh_token(channel, db) if (channel and db) else None
        if not refresh_token:
            if channel and db:
                self._handle_invalid_grant(channel, db, "Missing refresh token for video delete", job=job_entity)
            return {"success": False, "status": "QUARANTINED", "error": "No OAuth refresh token"}

        client = build_youtube_client(refresh_token, client_config=self._resolve_client_config())
        try:
            client.videos().delete(id=youtube_video_id).execute()
            self.quota_tracker.record_videos_delete(
                channel_id=channel.youtube_channel_id if channel else None,
                job_id=getattr(job_entity, "id", None),
                db=db,
            )
            logger.info("Successfully deleted video %s from YouTube", youtube_video_id)
            return {"success": True, "youtube_video_id": youtube_video_id, "deleted": True}
        except HttpError as e:
            raw_content = getattr(e, "content", b"") or b""
            content_str = raw_content.decode("utf-8", errors="ignore") if isinstance(raw_content, bytes) else str(raw_content)
            err_str = f"{str(e)} {content_str}".strip()
            try:
                status_code = int(getattr(e.resp, "status", 0))
            except (ValueError, TypeError):
                status_code = 0
            if status_code == 404:
                logger.info("Video %s not found on YouTube (404); treating as already deleted.", youtube_video_id)
                return {"success": True, "youtube_video_id": youtube_video_id, "deleted": True, "note": "404 not found"}
            if "invalid_grant" in err_str or status_code == 401:
                if channel and db:
                    self._handle_invalid_grant(channel, db, err_str, job=job_entity)
                return {"success": False, "status": "QUARANTINED", "error": err_str}
            raise

    def execute_reschedule_intent(
        self,
        youtube_video_id: str,
        new_slot_time: datetime,
        channel_id: Optional[str] = None,
        db: Optional[Session] = None,
        job_entity: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Updates publishAt on YouTube without re-uploading (videos.update = 50 units).
        Used when rescheduling an already uploaded clip.
        """
        if not youtube_video_id:
            return {"success": False, "error": "No youtube_video_id provided"}

        channel = None
        if channel_id and db:
            channel = db.query(Channel).filter(
                (Channel.id == channel_id) | (Channel.youtube_channel_id == channel_id)
            ).first()
        if not channel and db:
            channel = db.query(Channel).first()

        is_dry_run = self.dry_run or youtube_video_id.startswith("dry_run_")
        dt_sched = new_slot_time
        if dt_sched.tzinfo is None:
            dt_sched = dt_sched.replace(tzinfo=timezone.utc)
        publish_at_iso = dt_sched.strftime("%Y-%m-%dT%H:%M:%SZ")

        if is_dry_run:
            logger.info("[DRY-RUN] Simulating videos().update status.publishAt=%s for %s", publish_at_iso, youtube_video_id)
            return {"success": True, "dry_run": True, "youtube_video_id": youtube_video_id, "publishAt": publish_at_iso}

        refresh_token = self._get_channel_refresh_token(channel, db) if (channel and db) else None
        if not refresh_token:
            if channel and db:
                self._handle_invalid_grant(channel, db, "Missing refresh token for video reschedule", job=job_entity)
            return {"success": False, "status": "QUARANTINED", "error": "No OAuth refresh token"}

        client = build_youtube_client(refresh_token, client_config=self._resolve_client_config())
        try:
            update_req = client.videos().update(
                part="status",
                body={
                    "id": youtube_video_id,
                    "status": {
                        "privacyStatus": "private",
                        "publishAt": publish_at_iso,
                        "selfDeclaredMadeForKids": getattr(settings, "YOUTUBE_MADE_FOR_KIDS", False),
                    },
                },
            )
            update_req.execute()
            self.quota_tracker.record_videos_update(
                channel_id=channel.youtube_channel_id if channel else None,
                job_id=getattr(job_entity, "id", None),
                db=db,
            )
            logger.info("Successfully updated publishAt for video %s -> %s", youtube_video_id, publish_at_iso)
            return {"success": True, "youtube_video_id": youtube_video_id, "publishAt": publish_at_iso}
        except HttpError as e:
            raw_content = getattr(e, "content", b"") or b""
            content_str = raw_content.decode("utf-8", errors="ignore") if isinstance(raw_content, bytes) else str(raw_content)
            err_str = f"{str(e)} {content_str}".strip()
            try:
                status_code = int(getattr(e.resp, "status", 0))
            except (ValueError, TypeError):
                status_code = 0
            if status_code == 403 and ("quota" in err_str.lower() or "uploadlimitexceeded" in err_str.lower()):
                self._handle_quota_exceeded(job_entity, db, err_str)
                return {"success": False, "status": "DEFERRED", "error": err_str}
            if "invalid_grant" in err_str or status_code == 401:
                if channel and db:
                    self._handle_invalid_grant(channel, db, err_str, job=job_entity)
                return {"success": False, "status": "QUARANTINED", "error": err_str}
            raise

    # ==========================================================================
    # Intent 3: 'daily_health' Job Execution
    # ==========================================================================

    def execute_daily_health_intent(
        self,
        channel_id: str,
        payload: Dict[str, Any],
        db: Session,
        job_entity: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Runs daily health check on target channel via channels.list (1 unit).
        Updates channel health indicators and flags 'needs_reauth' on invalid_grant.
        """
        channel = db.query(Channel).filter(
            (Channel.id == channel_id) | (Channel.youtube_channel_id == channel_id)
        ).first()

        if not channel:
            raise ValueError(f"Channel not found: {channel_id}")

        cred = db.query(ChannelCredential).filter_by(channel_id=channel.id).first()
        if cred and cred.status == "needs_reauth":
            try:
                from shorts_engine.services.events.taxonomy import emit_platform_event, CHANNEL_HEALTH
                emit_platform_event(
                    event_name=CHANNEL_HEALTH,
                    payload={"healthy": False, "status": "needs_reauth", "channel_id": channel.id, "error": cred.last_error},
                    entity_id=channel.id,
                    entity_type="channel",
                    level="ERROR",
                    title="Channel Health Check Failed",
                    message=f"Channel {channel.display_title} requires re-authentication.",
                    db=db,
                )
            except Exception:
                pass
            return {"healthy": False, "status": "needs_reauth", "error": cred.last_error}

        is_dry_run = self.dry_run or payload.get("dry_run", False)
        if is_dry_run:
            logger.info("[DRY-RUN] Simulating daily health check for channel %s", channel.display_title)
            try:
                from shorts_engine.services.events.taxonomy import emit_platform_event, CHANNEL_HEALTH
                emit_platform_event(
                    event_name=CHANNEL_HEALTH,
                    payload={"healthy": True, "dry_run": True, "channel_id": channel.id},
                    entity_id=channel.id,
                    entity_type="channel",
                    level="INFO",
                    title="Channel Health Check (Dry Run)",
                    message=f"[DRY-RUN] Channel {channel.display_title} healthy.",
                    db=db,
                )
            except Exception:
                pass
            return {"healthy": True, "dry_run": True, "channel_id": channel.id}

        is_valid, err = verify_channel_credentials(channel, db=db, client_config=self._resolve_client_config())
        if not is_valid:
            if "invalid_grant" in str(err):
                self._handle_invalid_grant(channel, db, str(err), job=job_entity)
            try:
                from shorts_engine.services.events.taxonomy import emit_platform_event, CHANNEL_HEALTH
                emit_platform_event(
                    event_name=CHANNEL_HEALTH,
                    payload={"healthy": False, "channel_id": channel.id, "error": err, "status": "invalid_grant" if "invalid_grant" in str(err) else "unhealthy"},
                    entity_id=channel.id,
                    entity_type="channel",
                    level="ERROR",
                    title="Channel Health Check Failed",
                    message=f"Channel {channel.display_title} health check failed: {err}",
                    db=db,
                )
            except Exception:
                pass
            return {"healthy": False, "channel_id": channel.id, "error": err}

        try:
            from shorts_engine.services.events.taxonomy import emit_platform_event, CHANNEL_HEALTH
            emit_platform_event(
                event_name=CHANNEL_HEALTH,
                payload={"healthy": True, "channel_id": channel.id, "verified_at": datetime.utcnow().isoformat()},
                entity_id=channel.id,
                entity_type="channel",
                level="INFO",
                title="Channel Health Check Succeeded",
                message=f"Channel {channel.display_title} verified healthy.",
                db=db,
            )
        except Exception:
            pass
        return {"healthy": True, "channel_id": channel.id, "verified_at": datetime.utcnow().isoformat()}

    # ==========================================================================
    # Unified Job Router
    # ==========================================================================

    def process_job(self, job: PipelineJob, db: Session) -> Dict[str, Any]:
        """
        Dispatches queue jobs to the appropriate handler based on job_type.
        """
        payload = {}
        if job.payload_json:
            try:
                payload = json.loads(job.payload_json)
            except Exception:
                payload = {}

        job_type = (job.stage or job.job_type).lower()

        if job_type in ("publish", "publish_clip"):
            return self.execute_publish_intent(clip_id=job.entity_id, payload=payload, db=db, job_entity=job)
        elif job_type in ("metadata_update", "update_metadata"):
            return self.execute_metadata_update_intent(clip_id=job.entity_id, payload=payload, db=db, job_entity=job)
        elif job_type in ("daily_health", "health_check"):
            return self.execute_daily_health_intent(channel_id=job.entity_id, payload=payload, db=db, job_entity=job)
        else:
            raise ValueError(f"Unsupported publish worker job type: {job_type}")


# ==============================================================================
# Legacy Compatibility Adapters
# ==============================================================================

class YouTubePublisher:
    """Backward compatibility adapter delegating directly to YouTubePublishWorker."""

    def __init__(self, db_session: Optional[Session] = None, quota_tracker: Optional[QuotaTracker] = None):
        self.db = db_session
        self.worker = YouTubePublishWorker(quota_tracker=quota_tracker)

    @property
    def quota_tracker(self) -> QuotaTracker:
        return self.worker.quota_tracker

    def publish_clip(self, clip: Clip, metadata: Any, dry_run: Optional[bool] = None, **kwargs) -> Dict[str, Any]:
        db = kwargs.get("db_session") or self.db
        payload = {
            "title": getattr(metadata, "title", None),
            "description": getattr(metadata, "description", None),
            "tags": getattr(metadata, "tags", None),
            "privacy_status": getattr(metadata, "privacy_status", "unlisted"),
            "category_id": getattr(metadata, "category_id", "22"),
            "made_for_kids": getattr(metadata, "made_for_kids", None),
            "dry_run": dry_run,
        }
        return self.worker.execute_publish_intent(clip_id=clip.id, payload=payload, db=db)

    def update_video_title(self, youtube_video_id: str, new_title: str, **kwargs) -> Dict[str, Any]:
        db = kwargs.get("db_session") or self.db
        payload = {
            "youtube_video_id": youtube_video_id,
            "title": new_title,
            "dry_run": kwargs.get("dry_run", False),
        }
        return self.worker.execute_metadata_update_intent(clip_id="", payload=payload, db=db)

    def update_video_thumbnail(self, youtube_video_id: str, thumbnail_path: str, **kwargs) -> Dict[str, Any]:
        db = kwargs.get("db_session") or self.db
        payload = {
            "youtube_video_id": youtube_video_id,
            "thumbnail_path": thumbnail_path,
            "dry_run": kwargs.get("dry_run", False),
        }
        return self.worker.execute_metadata_update_intent(clip_id="", payload=payload, db=db)


class PublishOrchestrator:
    """Backward compatibility adapter delegating directly to YouTubePublishWorker."""

    def __init__(self, quota_tracker: Optional[QuotaTracker] = None):
        self.worker = YouTubePublishWorker(quota_tracker=quota_tracker)

    def execute_publish_job(self, job_id: str, db: Session, dry_run: Optional[bool] = None) -> Dict[str, Any]:
        job = db.query(PublishJob).filter_by(id=job_id).first()
        if not job:
            raise ValueError(f"PublishJob not found: {job_id}")
        payload = {
            "title": job.title,
            "description": job.description,
            "tags": json.loads(job.tags) if job.tags else [],
            "category_id": job.category_id,
            "privacy_status": job.privacy_status,
            "made_for_kids": job.made_for_kids,
            "scheduled_at": job.scheduled_at,
            "upload_session_uri": job.upload_session_uri,
            "dry_run": dry_run,
        }
        return self.worker.execute_publish_intent(clip_id=job.clip_id, payload=payload, db=db, job_entity=job)

    def reconcile_startup_uploading_jobs(self, db: Session) -> int:
        stuck = db.query(PublishJob).filter_by(status=PublishJobStatus.UPLOADING.value).all()
        for j in stuck:
            j.status = PublishJobStatus.PAUSED_NEEDS_ATTENTION.value
            j.last_error = "Server restarted while upload was in progress."
        if stuck:
            db.commit()
        return len(stuck)

    def retry_job(self, job_id: str, db: Session, dry_run: Optional[bool] = None) -> Dict[str, Any]:
        return self.execute_publish_job(job_id=job_id, db=db, dry_run=dry_run)
