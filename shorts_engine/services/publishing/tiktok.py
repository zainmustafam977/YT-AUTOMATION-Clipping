"""
TikTok Content Posting API v2 Publisher adapter.
Supports chunked resumable video uploads, creator info privacy verification,
asynchronous publishing status polling, and dry-run simulation safety.
"""

import logging
import math
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import httpx

from shorts_engine.config.settings import settings
from shorts_engine.services.publishing.base import BasePlatformPublisher, PlatformPublishResult

logger = logging.getLogger(__name__)

TIKTOK_API_BASE = "https://open.tiktokapis.com"
CHUNK_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB per chunk (TikTok requirement: 5MB <= chunk <= 64MB)


class TikTokPublisher(BasePlatformPublisher):
    """
    Industrial-grade publisher for TikTok using Content Posting API v2.
    Handles creator pre-flight checks, resumable chunk streaming, and status polling.
    """

    def __init__(
        self,
        access_token: Optional[str] = None,
        client_key: Optional[str] = None,
        client_secret: Optional[str] = None,
        open_id: Optional[str] = None,
    ):
        self.access_token = access_token or settings.TIKTOK_ACCESS_TOKEN
        self.client_key = client_key or settings.TIKTOK_CLIENT_KEY
        self.client_secret = client_secret or settings.TIKTOK_CLIENT_SECRET
        self.open_id = open_id or settings.TIKTOK_OPEN_ID

    @property
    def platform_name(self) -> str:
        return "tiktok"

    def health_check(self) -> Tuple[bool, str]:
        """Validates whether TikTok API credentials are configured and active."""
        if not self.access_token:
            return False, "TikTok access token is not configured (TIKTOK_ACCESS_TOKEN is empty)."
        try:
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    f"{TIKTOK_API_BASE}/v2/post/publish/creator_info/query/",
                    headers=headers,
                    json={},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("error", {}).get("code") == "ok":
                        return True, "TikTok Content Posting API connection healthy."
                    return False, f"TikTok API error: {data.get('error', {}).get('message')}"
                return False, f"TikTok HTTP {resp.status_code}: {resp.text}"
        except Exception as e:
            return False, f"TikTok connection check failed: {str(e)}"

    def _query_creator_info(self, client: httpx.Client) -> Dict[str, Any]:
        """Queries creator permissions, default privacy levels, and max video duration."""
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        resp = client.post(
            f"{TIKTOK_API_BASE}/v2/post/publish/creator_info/query/",
            headers=headers,
            json={},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("error", {}).get("code") != "ok":
            raise RuntimeError(f"Creator info query failed: {data.get('error')}")
        return data.get("data", {})

    def _init_chunked_upload(
        self, client: httpx.Client, file_size: int, title: str, privacy_level: str = "SELF_ONLY"
    ) -> Tuple[str, str]:
        """
        Initializes video upload with TikTok Content Posting API v2.
        Returns: (publish_id, upload_url)
        """
        total_chunks = math.ceil(file_size / CHUNK_SIZE_BYTES)
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }
        payload = {
            "post_info": {
                "title": title[:150],  # TikTok title cap
                "privacy_level": privacy_level,
                "disable_duet": False,
                "disable_stitch": False,
                "disable_comment": False,
                "video_cover_timestamp_ms": 1000,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": CHUNK_SIZE_BYTES,
                "total_chunk_count": total_chunks,
            },
        }

        resp = client.post(
            f"{TIKTOK_API_BASE}/v2/post/publish/video/init/",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("error", {}).get("code") != "ok":
            raise RuntimeError(f"TikTok upload init error: {data.get('error')}")

        res_data = data.get("data", {})
        publish_id = res_data.get("publish_id")
        upload_url = res_data.get("upload_url")
        if not publish_id or not upload_url:
            raise RuntimeError(f"TikTok init missing publish_id or upload_url: {res_data}")

        return publish_id, upload_url

    def _upload_chunks(self, client: httpx.Client, upload_url: str, video_path: Path, file_size: int):
        """Uploads video data in discrete byte range chunks to the presigned upload URL."""
        with open(video_path, "rb") as f:
            chunk_index = 0
            while True:
                start_byte = chunk_index * CHUNK_SIZE_BYTES
                chunk_data = f.read(CHUNK_SIZE_BYTES)
                if not chunk_data:
                    break

                end_byte = start_byte + len(chunk_data) - 1
                headers = {
                    "Content-Type": "video/mp4",
                    "Content-Range": f"bytes {start_byte}-{end_byte}/{file_size}",
                    "Content-Length": str(len(chunk_data)),
                }

                put_resp = client.put(upload_url, headers=headers, content=chunk_data, timeout=60.0)
                if put_resp.status_code not in (200, 201, 206):
                    raise RuntimeError(
                        f"TikTok chunk upload failed at bytes {start_byte}-{end_byte}: "
                        f"HTTP {put_resp.status_code} {put_resp.text}"
                    )
                chunk_index += 1

    def _poll_publish_status(
        self, client: httpx.Client, publish_id: str, max_attempts: int = 15, delay_sec: float = 2.0
    ) -> Dict[str, Any]:
        """Polls /v2/post/publish/status/fetch/ until PUBLISH_COMPLETE or error."""
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload = {"publish_id": publish_id}

        for attempt in range(max_attempts):
            resp = client.post(
                f"{TIKTOK_API_BASE}/v2/post/publish/status/fetch/",
                headers=headers,
                json=payload,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                status = data.get("status")
                logger.debug("TikTok publish_id %s status: %s (attempt %d)", publish_id, status, attempt + 1)

                if status == "PUBLISH_COMPLETE":
                    return data
                elif status in ("FAILED", "AUDIT_REJECT"):
                    fail_reason = data.get("fail_reason", "Unknown failure")
                    raise RuntimeError(f"TikTok publishing failed with status: {status} ({fail_reason})")

            time.sleep(delay_sec)

        logger.warning("TikTok publish status polling timed out for %s; returning intermediate data", publish_id)
        return {"status": "PROCESSING", "publish_id": publish_id}

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
        """Publishes short video to TikTok with chunked resumable upload and status polling."""
        is_dry_run = dry_run if dry_run is not None else settings.DRY_RUN_PUBLISHING
        video_path = Path(video_path)

        if not video_path.exists() or video_path.stat().st_size == 0:
            return PlatformPublishResult(
                platform=self.platform_name,
                success=False,
                error=f"Video file not found or empty: {video_path}",
                dry_run=is_dry_run,
            )

        caption_tags = " ".join([f"#{t.replace('#', '')}" for t in (tags or [])])
        full_caption = f"{title}\n\n{caption_tags}".strip() if caption_tags else title

        if is_dry_run or not self.access_token:
            simulated_id = f"tt_sim_{int(time.time())}_{os.urandom(3).hex()}"
            simulated_url = f"https://www.tiktok.com/@creator/video/{simulated_id}"
            logger.info("[DRY-RUN] Simulating TikTok upload for '%s' -> %s", title, simulated_url)
            return PlatformPublishResult(
                platform=self.platform_name,
                success=True,
                post_id=simulated_id,
                permalink=simulated_url,
                metadata={
                    "simulated": True,
                    "caption": full_caption,
                    "file_size": video_path.stat().st_size,
                    "chunk_size": CHUNK_SIZE_BYTES,
                },
                dry_run=True,
            )

        file_size = video_path.stat().st_size
        try:
            with httpx.Client(timeout=30.0) as client:
                # 1. Creator Pre-flight verification
                creator_info = self._query_creator_info(client)
                privacy_level = kwargs.get("privacy_level", "PUBLIC_TO_EVERYONE")
                avail_privacy = creator_info.get("privacy_level_options", [])
                if avail_privacy and privacy_level not in avail_privacy:
                    privacy_level = avail_privacy[0]

                # 2. Initialize chunked upload
                publish_id, upload_url = self._init_chunked_upload(
                    client, file_size=file_size, title=full_caption, privacy_level=privacy_level
                )

                # 3. Stream byte ranges
                self._upload_chunks(client, upload_url=upload_url, video_path=video_path, file_size=file_size)

                # 4. Status polling
                publish_data = self._poll_publish_status(client, publish_id=publish_id)

                post_id = publish_data.get("publicaly_available_post_id", [publish_id])[0] if isinstance(publish_data.get("publicaly_available_post_id"), list) else publish_id
                permalink = f"https://www.tiktok.com/@video/{post_id}"

                return PlatformPublishResult(
                    platform=self.platform_name,
                    success=True,
                    post_id=str(post_id),
                    permalink=permalink,
                    metadata={"publish_id": publish_id, "tiktok_status": publish_data.get("status")},
                    dry_run=False,
                )
        except Exception as e:
            logger.exception("TikTok publishing failed for %s: %s", video_path, e)
            return PlatformPublishResult(
                platform=self.platform_name,
                success=False,
                error=str(e),
                dry_run=False,
            )
