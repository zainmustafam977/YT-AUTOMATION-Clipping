"""
YouTube Data API v3 Publisher client with OAuth2 decryption,
quota enforcement, dry-run simulation mode, and duplicate publish prevention.
"""

import json
import logging
import time
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from googleapiclient.errors import HttpError

from shorts_engine.config.settings import settings
from shorts_engine.db.models import Clip, ClipStatus, PublishLog
from shorts_engine.db.security import decrypt_secret
from shorts_engine.services.publishing.quota import QuotaTracker
from shorts_engine.services.publishing.metadata import ShortsMetadata
from shorts_engine.services.events.bus import global_event_bus
from shorts_engine.services.events.models import PlatformEvent

logger = logging.getLogger(__name__)


def _build_youtube_client(oauth_token_json: str):
    """
    Builds an authenticated YouTube Data API v3 client from a decrypted OAuth token JSON blob.
    The blob must contain: refresh_token, and optionally client_id, client_secret, token_uri.
    """
    import google.oauth2.credentials
    import googleapiclient.discovery
    import googleapiclient.http

    try:
        token_data = json.loads(oauth_token_json)
    except Exception as e:
        raise ValueError(
            f"Invalid OAuth credentials format in database ({e}). "
            "Please authorize the channel via Google OAuth consent screen in Publishing Studio."
        ) from e

    if not isinstance(token_data, dict):
        raise ValueError(
            "Channel credentials do not contain a valid OAuth token dictionary. "
            "Please authorize the channel via Google OAuth consent screen."
        )

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise ValueError(
            "Channel OAuth credentials do not contain a valid refresh_token. "
            "Please click 'Connect Channel' in the Publishing Studio to complete Google authorization."
        )

    client_id = token_data.get("client_id")
    client_secret = token_data.get("client_secret")

    # Fall back to client secrets file if missing in token blob
    if not client_id or not client_secret:
        try:
            from shorts_engine.services.publishing.oauth_manager import ChannelOAuthManager
            cfg = ChannelOAuthManager().get_client_config()
            client_id = client_id or cfg.get("client_id")
            client_secret = client_secret or cfg.get("client_secret")
        except Exception:
            pass

    if not client_id or not client_secret:
        raise ValueError("OAuth client_id or client_secret missing from credentials and storage/client_secret.json")

    credentials = google.oauth2.credentials.Credentials(
        token=token_data.get("access_token"),
        refresh_token=refresh_token,
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=client_id,
        client_secret=client_secret,
        scopes=token_data.get("scopes", ["https://www.googleapis.com/auth/youtube.upload"]),
    )
    return googleapiclient.discovery.build(
        "youtube", "v3", credentials=credentials, cache_discovery=False
    )


def _upload_video(client, video_path: Path, metadata: "ShortsMetadata",
                  privacy_status: str = "unlisted") -> str:
    """
    Calls YouTube Data API v3 videos.insert() to upload the rendered clip.
    Supports instant public/unlisted/private uploads as well as scheduled publishAt releases.
    Returns the published YouTube video ID.
    """
    import googleapiclient.http

    body = metadata.to_dict()
    if privacy_status and not metadata.publish_at:
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
            # Fast-fail immediately on non-retryable errors (401, 403, quotaExceeded)
            status_code = getattr(e.resp, "status", None) if hasattr(e, "resp") else None
            err_content = str(e)
            if status_code in (401, 403) or "quotaExceeded" in err_content:
                logger.error("Fast-failing YouTube upload chunk on non-retryable error (status=%s): %s", status_code, e)
                raise

            # Retry transient 5xx server errors
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
            # Transient socket / connection error
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

        # Check quota availability before attempting publish (Spec 15.2 / acceptance criteria)
        can_pub, remaining_quota, quota_reason = self.quota_tracker.can_publish(db=self.db)
        if not can_pub:
            logger.warning("YouTube quota exhausted for clip %s: %s", clip.id, quota_reason)
            try:
                global_event_bus.publish_sync(PlatformEvent(
                    category="publishing",
                    level="WARNING",
                    title="Publishing Halted: Quota Exhausted",
                    message=f"Upload halted for clip {clip.id[:8]}: {quota_reason}",
                    entity_id=clip.id,
                    entity_type="clip",
                    action_url="/settings",
                ))
            except Exception:
                pass
            return {
                "success": False,
                "quota_exhausted": True,
                "remaining_quota": remaining_quota,
                "reason": quota_reason,
            }

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

        try:
            global_event_bus.publish_sync(PlatformEvent(
                category="publishing",
                level="INFO",
                title="Publishing Clip Started",
                message=f"Starting YouTube upload for clip {clip.id[:8]} ({'DRY RUN' if is_dry_run else 'LIVE'})...",
                stage_progress_pct=20.0,
                entity_id=clip.id,
                entity_type="clip",
                action_url=f"/queue?clip={clip.id}",
            ))
        except Exception:
            pass

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

                channel_name = channel.name if channel else "target channel"
                if not raw_token:
                    raise ValueError(
                        f"No OAuth token configured for channel '{channel_name}' (clip {clip.id}). "
                        "Please click 'Connect Channel' in the Publishing Studio or enable DRY_RUN_PUBLISHING."
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
                    try:
                        global_event_bus.publish_sync(PlatformEvent(
                            category="publishing",
                            level="SUCCESS",
                            title="Clip Reclaimed & Published",
                            message=f"Reclaimed existing upload for clip {clip.id[:8]} (YouTube ID: {existing_yt_id}).",
                            stage_progress_pct=100.0,
                            entity_id=clip.id,
                            entity_type="clip",
                            action_url=f"https://youtube.com/shorts/{existing_yt_id}",
                            details={"youtube_video_id": existing_yt_id, "reclaimed": True},
                        ))
                    except Exception:
                        pass
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
                    try:
                        global_event_bus.publish_sync(PlatformEvent(
                            category="publishing",
                            level="WARNING",
                            title="Publishing Halted: Quota Exhausted",
                            message=f"Upload halted for clip {clip.id[:8]}: {reason}",
                            entity_id=clip.id,
                            entity_type="clip",
                            action_url="/settings",
                        ))
                    except Exception:
                        pass
                    return {
                        "success": False,
                        "quota_exhausted": True,
                        "reason": reason,
                    }

                upload_privacy = metadata.privacy_status or getattr(settings, "DEFAULT_PRIVACY_STATUS", "unlisted")
                youtube_video_id = _upload_video(
                    client,
                    video_path,
                    metadata,
                    privacy_status=upload_privacy,
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
            try:
                yt_link = f"https://youtube.com/shorts/{youtube_video_id}" if not is_dry_run else f"/queue?clip={clip.id}"
                global_event_bus.publish_sync(PlatformEvent(
                    category="publishing",
                    level="SUCCESS",
                    title="Clip Published",
                    message=f"Clip {clip.id[:8]} published successfully! YouTube ID: {youtube_video_id}",
                    stage_progress_pct=100.0,
                    entity_id=clip.id,
                    entity_type="clip",
                    action_url=yt_link,
                    details={"youtube_video_id": youtube_video_id, "dry_run": is_dry_run},
                ))
            except Exception:
                pass
            return {
                "success": True,
                "youtube_video_id": youtube_video_id,
                "published_at": publish_log.published_at.isoformat(),
                "dry_run": is_dry_run,
            }
        except Exception as e:
            clip.status = ClipStatus.APPROVED
            publish_log.status = "FAILED"
            publish_log.error_message = str(e)
            self.db.commit()
            try:
                global_event_bus.publish_sync(PlatformEvent(
                    category="publishing",
                    level="ERROR",
                    title="Publishing Failed",
                    message=f"Publishing failed for clip {clip.id[:8]}: {str(e)[:250]}",
                    entity_id=clip.id,
                    entity_type="clip",
                    action_url=f"/queue?clip={clip.id}",
                ))
            except Exception:
                pass
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

    def update_video_thumbnail(
        self,
        youtube_video_id: str,
        thumbnail_path: str,
        encrypted_oauth_token: Optional[str] = None,
        dry_run: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Updates the custom video thumbnail cover on YouTube Data API v3 (thumbnails().set).
        Used by the Bayesian A/B promotion daemon to promote winning thumbnail variants.
        """
        is_dry_run = dry_run if dry_run is not None else settings.DRY_RUN_PUBLISHING
        thumb_file = Path(thumbnail_path)
        if not thumb_file.exists():
            raise FileNotFoundError(f"Thumbnail file not found at: {thumbnail_path}")

        if is_dry_run:
            logger.info("[DRY-RUN] Simulating YouTube thumbnail update for %s -> %s", youtube_video_id, thumb_file.name)
            return {
                "success": True,
                "dry_run": True,
                "video_id": youtube_video_id,
                "thumbnail_path": str(thumb_file),
            }

        if not encrypted_oauth_token:
            raise ValueError(f"No OAuth token provided to update thumbnail for video {youtube_video_id}")

        decrypted_token = decrypt_secret(encrypted_oauth_token)
        client = _build_youtube_client(decrypted_token)

        try:
            from googleapiclient.http import MediaFileUpload
            media = MediaFileUpload(str(thumb_file), mimetype="image/jpeg", resumable=True)
            req = client.thumbnails().set(videoId=youtube_video_id, media_body=media)
            resp = req.execute()
            logger.info("Successfully updated YouTube video %s thumbnail cover.", youtube_video_id)
            return {
                "success": True,
                "dry_run": False,
                "video_id": youtube_video_id,
                "thumbnail_path": str(thumb_file),
                "response": resp,
            }
        except Exception as e:
            logger.error("Failed to upload YouTube thumbnail for %s: %s", youtube_video_id, e)
            raise

