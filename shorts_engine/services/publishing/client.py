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


def _check_recent_uploads_for_clip(client: Any, channel_youtube_id: str, clip_id: str) -> Optional[str]:
    """
    Queries the channel's uploads playlist on YouTube Data API to check
    if a video with token [clip:<clip_id>] in its description was already uploaded.
    """
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
            maxResults=20,
        ).execute()

        token = f"[clip:{clip_id}]"
        for item in playlist_res.get("items", []):
            desc = item.get("snippet", {}).get("description", "")
            if token in desc:
                return item.get("snippet", {}).get("resourceId", {}).get("videoId")
    except Exception as e:
        logger.warning("Could not check uploads playlist for clip %s: %s", clip_id, e)
    return None


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
        Enforces idempotency: checks uploads playlist and never republishes an already published clip.
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

        # 2. Verify rendered video file exists
        video_path = Path(clip.rendered_path) if clip.rendered_path else None
        if not video_path or not video_path.exists() or video_path.stat().st_size == 0:
            raise FileNotFoundError(f"Rendered video file not found for clip {clip.id}")

        # 3. Write initial "upload started" record in PublishLog and mark PUBLISHING (PLAN-P0-05)
        publish_log = self.db.query(PublishLog).filter_by(clip_id=clip.id).first()
        if not publish_log:
            publish_log = PublishLog(
                clip_id=clip.id,
                youtube_video_id=None,
                published_at=datetime.utcnow(),
                title=metadata.title,
                description=metadata.description,
                tags_json=json.dumps(metadata.tags),
                status="UPLOADING",
            )
            self.db.add(publish_log)
        else:
            publish_log.status = "UPLOADING"
            publish_log.title = metadata.title
            publish_log.description = metadata.description
            publish_log.tags_json = json.dumps(metadata.tags)

        clip.status = ClipStatus.PUBLISHING
        self.db.commit()

        youtube_video_id: Optional[str] = None
        status_label = "SUCCESS"

        try:
            if is_dry_run:
                logger.info("[DRY-RUN] Simulating YouTube Shorts upload for clip %s...", clip.id)
                youtube_video_id = f"dry_run_{clip.id[:8]}"
                status_label = "DRY_RUN"
            else:
                logger.info("Initiating live YouTube upload for clip %s...", clip.id)
                raw_token = encrypted_oauth_token
                channel_yt_id = None
                if clip.candidate and clip.candidate.source_video and clip.candidate.source_video.channel:
                    channel = clip.candidate.source_video.channel
                    raw_token = raw_token or channel.encrypted_oauth_token
                    channel_yt_id = channel.youtube_channel_id

                if not raw_token:
                    raise ValueError(
                        f"No OAuth token available for clip {clip.id}. "
                        "Set DRY_RUN_PUBLISHING=true or configure channel OAuth credentials."
                    )

                decrypted_token = decrypt_secret(raw_token)
                client = _build_youtube_client(decrypted_token)

                # Check channel's uploads playlist for existing out-of-band upload (PLAN-P0-05)
                existing_yt_id = None
                if channel_yt_id:
                    existing_yt_id = _check_recent_uploads_for_clip(client, channel_yt_id, clip.id)

                if existing_yt_id:
                    logger.info(
                        "Found existing out-of-band YouTube upload for clip %s: %s. Reclaiming and marking PUBLISHED.",
                        clip.id, existing_yt_id,
                    )
                    clip.status = ClipStatus.PUBLISHED
                    publish_log.youtube_video_id = existing_yt_id
                    publish_log.status = "RECLAIMED"
                    publish_log.published_at = datetime.utcnow()
                    self.db.commit()
                    return {
                        "success": True,
                        "youtube_video_id": existing_yt_id,
                        "published_at": publish_log.published_at.isoformat(),
                        "dry_run": False,
                        "reclaimed": True,
                    }


                # Quota Check before heavy insert
                can_pub, remaining, reason = self.quota_tracker.can_publish(db=self.db)
                if not can_pub:
                    logger.warning("Cannot publish clip %s: %s", clip.id, reason)
                    publish_log.status = "QUOTA_EXHAUSTED"
                    self.db.commit()
                    return {
                        "success": False,
                        "quota_exhausted": True,
                        "reason": reason,
                    }

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
            publish_log.youtube_video_id = youtube_video_id
            publish_log.published_at = datetime.utcnow()
            publish_log.status = status_label
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
        except Exception as e:
            publish_log.status = "FAILED"
            publish_log.error_message = str(e)
            self.db.commit()
            raise

    def update_video_title(
        self,
        youtube_video_id: str,
        new_title: str,
        encrypted_oauth_token: Optional[str] = None,
        dry_run: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Updates the video title on YouTube Data API v3 (videos().update).
        Used by the Bayesian Multi-Armed Bandit promotion daemon to switch titles.
        """
        is_dry_run = dry_run if dry_run is not None else settings.DRY_RUN_PUBLISHING
        clean_title = new_title.strip()
        if "#Shorts" not in clean_title and "#shorts" not in clean_title:
            clean_title = f"{clean_title} #Shorts"
        clean_title = clean_title[:100]

        if is_dry_run:
            logger.info("[DRY-RUN] Simulating YouTube title update for %s -> '%s'", youtube_video_id, clean_title)
            return {
                "success": True,
                "dry_run": True,
                "video_id": youtube_video_id,
                "title": clean_title,
            }

        if not encrypted_oauth_token:
            raise ValueError(f"No OAuth token provided to update title for video {youtube_video_id}")

        decrypted_token = decrypt_secret(encrypted_oauth_token)
        client = _build_youtube_client(decrypted_token)

        # Retrieve current snippet
        get_req = client.videos().list(part="snippet", id=youtube_video_id)
        get_resp = get_req.execute()
        items = get_resp.get("items", [])
        if not items:
            raise RuntimeError(f"Video {youtube_video_id} not found on YouTube.")

        snippet = items[0]["snippet"]
        snippet["title"] = clean_title

        # Execute snippet update
        update_req = client.videos().update(
            part="snippet",
            body={
                "id": youtube_video_id,
                "snippet": snippet,
            },
        )
        update_resp = update_req.execute()
        updated_title = update_resp.get("snippet", {}).get("title", clean_title)

        logger.info("Updated YouTube video %s title -> '%s'", youtube_video_id, updated_title)
        return {
            "success": True,
            "dry_run": False,
            "video_id": youtube_video_id,
            "title": updated_title,
        }

