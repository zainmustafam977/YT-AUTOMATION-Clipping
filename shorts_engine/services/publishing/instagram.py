"""
Instagram Reels Publisher adapter using Meta Graph API v19.0.
Implements media container initialization, status polling state machine,
container publishing, permalink retrieval, and dry-run simulation safety.
"""

import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import httpx

from shorts_engine.config.settings import settings
from shorts_engine.services.publishing.base import BasePlatformPublisher, PlatformPublishResult

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v19.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
MAX_INSTAGRAM_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB Meta Reels hard limit


def _chunk_file_reader(file_path: Path, chunk_size: int = 1024 * 1024):
    """Generator yielding file content in 1MB chunks for streaming HTTP upload."""
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            yield chunk


class InstagramReelsPublisher(BasePlatformPublisher):

    """
    Industrial-grade publisher for Instagram Reels via Meta Graph API.
    Handles media container lifecycles, processing state machine, and publishing.
    """

    def __init__(
        self,
        access_token: Optional[str] = None,
        ig_user_id: Optional[str] = None,
    ):
        self.access_token = access_token or settings.INSTAGRAM_ACCESS_TOKEN
        self.ig_user_id = ig_user_id or settings.INSTAGRAM_USER_ID

    @property
    def platform_name(self) -> str:
        return "instagram"

    def health_check(self) -> Tuple[bool, str]:
        """Validates whether Meta Graph API token and IG User ID are valid and responsive."""
        if not self.access_token or not self.ig_user_id:
            return False, "Instagram credentials missing (INSTAGRAM_ACCESS_TOKEN or INSTAGRAM_USER_ID empty)."
        try:
            url = f"{GRAPH_API_BASE}/{self.ig_user_id}"
            params = {"fields": "username,name", "access_token": self.access_token}
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    return True, f"Instagram Graph API healthy (account: @{data.get('username')})."
                return False, f"Instagram API HTTP {resp.status_code}: {resp.text}"
        except Exception as e:
            return False, f"Instagram connection check error: {str(e)}"

    def _create_media_container(
        self,
        client: httpx.Client,
        video_url: Optional[str],
        caption: str,
        share_to_feed: bool = True,
        cover_url: Optional[str] = None,
    ) -> str:
        """
        Creates an Instagram Reels container via POST /{ig_user_id}/media.
        Returns the container ID.
        """
        url = f"{GRAPH_API_BASE}/{self.ig_user_id}/media"
        payload = {
            "media_type": "REELS",
            "caption": caption,
            "share_to_feed": share_to_feed,
            "access_token": self.access_token,
        }
        if video_url:
            payload["video_url"] = video_url
        if cover_url:
            payload["cover_url"] = cover_url

        resp = client.post(url, data=payload)
        resp.raise_for_status()
        data = resp.json()
        container_id = data.get("id")
        if not container_id:
            raise RuntimeError(f"Meta Graph API did not return container ID: {data}")
        return container_id

    def _create_resumable_upload_session(
        self,
        client: httpx.Client,
        file_size: int,
        caption: str,
    ) -> Tuple[str, str]:
        """
        Initializes a direct resumable upload session for local video files without public URL.
        Returns (container_id, upload_uri).
        """
        url = f"{GRAPH_API_BASE}/{self.ig_user_id}/media"
        params = {
            "upload_type": "resumable",
            "media_type": "REELS",
            "caption": caption,
            "share_to_feed": "true",
            "access_token": self.access_token,
        }
        resp = client.post(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        container_id = data.get("id")
        upload_uri = data.get("uri")
        if not container_id or not upload_uri:
            raise RuntimeError(f"Failed to initialize Meta resumable upload session: {data}")
        return container_id, upload_uri

    def _upload_direct_binary(self, client: httpx.Client, upload_uri: str, video_path: Path):
        """Streams binary video payload to Meta resumable upload session endpoint."""
        file_size = video_path.stat().st_size
        if file_size > MAX_INSTAGRAM_FILE_SIZE_BYTES:
            raise ValueError(
                f"Video file size ({file_size} bytes) exceeds Instagram Reels limit of 50 MB ({MAX_INSTAGRAM_FILE_SIZE_BYTES} bytes)."
            )
        headers = {
            "Authorization": f"OAuth {self.access_token}",
            "offset": "0",
            "file_size": str(file_size),
        }
        resp = client.post(
            upload_uri,
            headers=headers,
            content=_chunk_file_reader(video_path),
            timeout=120.0,
        )
        resp.raise_for_status()

    def _poll_container_status(
        self, client: httpx.Client, container_id: str, max_attempts: int = 20, delay_sec: float = 3.0
    ) -> Dict[str, Any]:
        """
        Polls GET /{container_id}?fields=status_code,status until FINISHED.
        Handles Meta status codes: IN_PROGRESS, FINISHED, ERROR, EXPIRED.
        """
        url = f"{GRAPH_API_BASE}/{container_id}"
        params = {
            "fields": "status_code,status",
            "access_token": self.access_token,
        }

        for attempt in range(max_attempts):
            resp = client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                code = data.get("status_code")
                logger.debug("Instagram container %s status: %s (attempt %d)", container_id, code, attempt + 1)

                if code == "FINISHED":
                    return data
                elif code in ("ERROR", "EXPIRED"):
                    err_msg = data.get("status", "Container processing failed")
                    raise RuntimeError(f"Instagram media container failed with status {code}: {err_msg}")
            time.sleep(delay_sec)

        raise TimeoutError(f"Instagram media container {container_id} processing timed out after {max_attempts} attempts.")

    def _publish_container(self, client: httpx.Client, container_id: str) -> str:
        """
        Publishes ready container via POST /{ig_user_id}/media_publish.
        Returns the published media ID.
        """
        url = f"{GRAPH_API_BASE}/{self.ig_user_id}/media_publish"
        payload = {
            "creation_id": container_id,
            "access_token": self.access_token,
        }
        resp = client.post(url, data=payload)
        resp.raise_for_status()
        data = resp.json()
        media_id = data.get("id")
        if not media_id:
            raise RuntimeError(f"Meta Graph API media_publish returned no ID: {data}")
        return media_id

    def _get_media_permalink(self, client: httpx.Client, media_id: str) -> Optional[str]:
        """Fetches permalink URL for published Reel."""
        url = f"{GRAPH_API_BASE}/{media_id}"
        params = {
            "fields": "permalink,shortcode",
            "access_token": self.access_token,
        }
        try:
            resp = client.get(url, params=params)
            if resp.status_code == 200:
                return resp.json().get("permalink")
        except Exception:
            pass
        return f"https://www.instagram.com/reel/{media_id}"

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
        """Publishes short video as an Instagram Reel using the Meta Graph container state machine."""
        is_dry_run = dry_run if dry_run is not None else settings.DRY_RUN_PUBLISHING
        video_path = Path(video_path)

        if not video_path.exists() or video_path.stat().st_size == 0:
            return PlatformPublishResult(
                platform=self.platform_name,
                success=False,
                error=f"Video file not found or empty: {video_path}",
                dry_run=is_dry_run,
            )

        if video_path.stat().st_size > MAX_INSTAGRAM_FILE_SIZE_BYTES:
            return PlatformPublishResult(
                platform=self.platform_name,
                success=False,
                error=f"Video file size ({video_path.stat().st_size} bytes) exceeds Instagram Reels limit of 50 MB ({MAX_INSTAGRAM_FILE_SIZE_BYTES} bytes).",
                dry_run=is_dry_run,
            )

        caption_tags = " ".join([f"#{t.replace('#', '')}" for t in (tags or [])])
        full_caption = f"{title}\n\n{description}\n\n{caption_tags}".strip() if caption_tags else f"{title}\n\n{description}".strip()

        if is_dry_run or not self.access_token or not self.ig_user_id:
            simulated_id = f"ig_sim_{int(time.time())}_{os.urandom(3).hex()}"
            simulated_permalink = f"https://www.instagram.com/reel/{simulated_id}"
            logger.info("[DRY-RUN] Simulating Instagram Reel upload for '%s' -> %s", title, simulated_permalink)
            return PlatformPublishResult(
                platform=self.platform_name,
                success=True,
                post_id=simulated_id,
                permalink=simulated_permalink,
                metadata={
                    "simulated": True,
                    "caption": full_caption,
                    "file_size": video_path.stat().st_size,
                },
                dry_run=True,
            )

        try:
            with httpx.Client(timeout=30.0) as client:
                public_video_url = kwargs.get("video_url")
                if public_video_url:
                    # Remote URL ingestion flow
                    container_id = self._create_media_container(
                        client, video_url=public_video_url, caption=full_caption
                    )
                else:
                    # Resumable session upload flow for local files
                    container_id, upload_uri = self._create_resumable_upload_session(
                        client, file_size=video_path.stat().st_size, caption=full_caption
                    )
                    self._upload_direct_binary(client, upload_uri=upload_uri, video_path=video_path)

                # 2. Wait for container processing state machine
                self._poll_container_status(client, container_id=container_id)

                # 3. Publish container
                media_id = self._publish_container(client, container_id=container_id)

                # 4. Fetch permalink
                permalink = self._get_media_permalink(client, media_id=media_id)

                return PlatformPublishResult(
                    platform=self.platform_name,
                    success=True,
                    post_id=str(media_id),
                    permalink=permalink,
                    metadata={"container_id": container_id},
                    dry_run=False,
                )
        except Exception as e:
            logger.exception("Instagram Reel publishing failed for %s: %s", video_path, e)
            return PlatformPublishResult(
                platform=self.platform_name,
                success=False,
                error=str(e),
                dry_run=False,
            )
