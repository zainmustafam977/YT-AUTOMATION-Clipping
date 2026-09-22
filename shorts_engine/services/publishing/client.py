"""
YouTube Data API v3 Publisher client with OAuth2 decryption,
quota enforcement, dry-run simulation mode, and duplicate publish prevention.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from shorts_engine.config.settings import settings
from shorts_engine.db.models import Clip, ClipStatus, PublishLog
from shorts_engine.db.security import decrypt_secret
from shorts_engine.services.publishing.quota import QuotaTracker
from shorts_engine.services.publishing.metadata import ShortsMetadata

logger = logging.getLogger(__name__)


def _build_youtube_client(oauth_token_json: str):
    """
    Builds an authenticated YouTube Data API v3 client from a decrypted OAuth token JSON blob.
    The blob must contain: client_id, client_secret, refresh_token, token_uri.
    """
    import google.oauth2.credentials
    import googleapiclient.discovery
    import googleapiclient.http

    token_data = json.loads(oauth_token_json)
    credentials = google.oauth2.credentials.Credentials(
        token=token_data.get("access_token"),
        refresh_token=token_data["refresh_token"],
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    return googleapiclient.discovery.build(
        "youtube", "v3", credentials=credentials, cache_discovery=False
    )


def _upload_video(client, video_path: Path, metadata: "ShortsMetadata",
                  privacy_status: str = "unlisted") -> str:
    """
    Calls YouTube Data API v3 videos.insert() to upload the rendered clip.
    Returns the published YouTube video ID.
    """
    import googleapiclient.http

    body = {
        "snippet": {
            "title": metadata.title,
            "description": metadata.description,
            "tags": metadata.tags,
            "categoryId": "22",  # People & Blogs (common for Shorts)
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

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
    while response is None:
        _, response = request.next_chunk()

    youtube_video_id: str = response.get("id", "")
    if not youtube_video_id:
        raise RuntimeError(f"YouTube API returned no video ID. Full response: {response}")

    return youtube_video_id


class YouTubePublisher:
    def __init__(self, db_session: Session, quota_tracker: Optional[QuotaTracker] = None):
        self.db = db_session
        self.quota_tracker = quota_tracker or QuotaTracker()

    def publish_clip(
        self,
        clip: Clip,
        metadata: ShortsMetadata,
        dry_run: Optional[bool] = None,
        encrypted_oauth_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Publishes an approved clip to YouTube Shorts.
        Enforces idempotency: never republishes an already published clip.
        """
        is_dry_run = dry_run if dry_run is not None else settings.DRY_RUN_PUBLISHING

        # 1. Idempotency Check (FR-17, NFR-01)
        if clip.status == ClipStatus.PUBLISHED:
            existing_log = self.db.query(PublishLog).filter_by(clip_id=clip.id).first()
            logger.warning("Clip %s is already published! Returning existing record.", clip.id)
            return {
                "success": True,
                "already_published": True,
                "youtube_video_id": existing_log.youtube_video_id if existing_log else None,
            }

        # 2. Quota Check
        can_pub, remaining, reason = self.quota_tracker.can_publish()
        if not can_pub:
            logger.warning("Cannot publish clip %s: %s", clip.id, reason)
            return {
                "success": False,
                "quota_exhausted": True,
                "reason": reason,
            }

        # 3. Verify rendered video file exists
        video_path = Path(clip.rendered_path) if clip.rendered_path else None
        if not video_path or not video_path.exists() or video_path.stat().st_size == 0:
            raise FileNotFoundError(f"Rendered video file not found for clip {clip.id}")

        clip.status = ClipStatus.PUBLISHING
        self.db.commit()

        youtube_video_id: Optional[str] = None
        status_label = "SUCCESS"

        if is_dry_run:
            logger.info("[DRY-RUN] Simulating YouTube Shorts upload for clip %s...", clip.id)
            youtube_video_id = f"dry_run_{clip.id[:8]}"
            status_label = "DRY_RUN"
        else:
            logger.info("Initiating live YouTube upload for clip %s...", clip.id)
            # Decrypt OAuth token from channel or from explicit argument
            raw_token = encrypted_oauth_token
            if raw_token is None and clip.candidate and clip.candidate.source_video:
                channel = clip.candidate.source_video.channel
                raw_token = channel.encrypted_oauth_token if channel else None

            if not raw_token:
                raise ValueError(
                    f"No OAuth token available for clip {clip.id}. "
                    "Set DRY_RUN_PUBLISHING=true or configure channel OAuth credentials."
                )

            decrypted_token = decrypt_secret(raw_token)
            client = _build_youtube_client(decrypted_token)
            youtube_video_id = _upload_video(
                client,
                video_path,
                metadata,
                privacy_status=settings.DEFAULT_PRIVACY_STATUS,
            )

        # 4. Record quota consumption
        self.quota_tracker.record_publish()

        # 5. Update Clip status and create PublishLog atomically
        clip.status = ClipStatus.PUBLISHED
        publish_log = PublishLog(
            clip_id=clip.id,
            youtube_video_id=youtube_video_id,
            published_at=datetime.utcnow(),
            title=metadata.title,
            description=metadata.description,
            tags_json=json.dumps(metadata.tags),
            status=status_label,
        )
        self.db.add(publish_log)
        self.db.commit()

        logger.info(
            "Successfully published clip %s — YouTube ID: %s (dry_run=%s)",
            clip.id, youtube_video_id, is_dry_run,
        )
        return {
            "success": True,
            "youtube_video_id": youtube_video_id,
            "published_at": publish_log.published_at.isoformat(),
            "dry_run": is_dry_run,
        }
