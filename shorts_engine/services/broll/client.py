"""
B-Roll Stock Footage Integration Service:
Fetches relevant contextual stock video cutaways from Pexels Video API
with local caching and graceful offline fallback.
"""

import re
import logging
import urllib.request
import urllib.parse
import json
from pathlib import Path
from typing import Optional

from shorts_engine.config.settings import settings

logger = logging.getLogger(__name__)


class BrollClient:
    """Client for discovering, downloading, and caching stock B-roll video footage."""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or (settings.OUTPUT_DIR / "broll_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch_broll_clip(
        self,
        query: str,
        orientation: str = "portrait",
        max_duration_sec: int = 6,
    ) -> Optional[Path]:
        """
        Fetches an MP4 B-roll clip matching the query.
        Returns the cached/downloaded Path or None if unavailable.
        """
        if not query or not query.strip():
            return None

        clean_q = re.sub(r"[^a-zA-Z0-9_\-]", "_", query.lower().strip())[:40]
        cached_file = self.cache_dir / f"{clean_q}.mp4"

        # Check local cache first
        if cached_file.exists() and cached_file.stat().st_size > 1024:
            logger.info("Found cached B-roll clip for '%s' at %s", query, cached_file)
            return cached_file

        api_key = getattr(settings, "PEXELS_API_KEY", None)
        if not api_key or not api_key.strip():
            logger.debug("No PEXELS_API_KEY configured. Skipping online B-roll download for '%s'.", query)
            return None

        try:
            params = urllib.parse.urlencode({
                "query": query.strip(),
                "orientation": orientation,
                "per_page": 5,
                "size": "medium",
            })
            url = f"https://api.pexels.com/videos/search?{params}"

            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": api_key.strip(),
                    "User-Agent": "ShortsEngine/2.0 (B-Roll Integration)",
                },
            )

            with urllib.request.urlopen(req, timeout=7.0) as resp:
                if resp.status != 200:
                    logger.warning("Pexels API returned status %d for query '%s'", resp.status, query)
                    return None
                data = json.loads(resp.read().decode("utf-8"))

            videos = data.get("videos", [])
            if not videos:
                logger.info("No stock videos found on Pexels for '%s'", query)
                return None

            # Find best matching video file
            download_url = None
            for v in videos:
                dur = v.get("duration", 0)
                if dur > 0 and dur > (max_duration_sec * 3):
                    continue  # Skip overly long videos

                files = v.get("video_files", [])
                # Sort files preferring HD / portrait
                for f in files:
                    link = f.get("link")
                    w = f.get("width", 0)
                    h = f.get("height", 0)
                    if link and (h >= 720 or w >= 720):
                        download_url = link
                        break
                if download_url:
                    break

            if not download_url and videos and videos[0].get("video_files"):
                download_url = videos[0]["video_files"][0].get("link")

            if not download_url:
                logger.warning("Could not find suitable MP4 link in Pexels results for '%s'", query)
                return None

            # Download to cache file
            temp_target = self.cache_dir / f"{clean_q}.tmp.mp4"
            logger.info("Downloading B-roll video for '%s' from %s...", query, download_url[:60])
            with urllib.request.urlopen(download_url, timeout=15.0) as dl_resp, open(temp_target, "wb") as out_f:
                while chunk := dl_resp.read(64 * 1024):
                    out_f.write(chunk)

            if temp_target.exists() and temp_target.stat().st_size > 1024:
                temp_target.replace(cached_file)
                logger.info("Successfully downloaded and cached B-roll for '%s' -> %s", query, cached_file)
                return cached_file

        except Exception as e:
            logger.warning("Failed to fetch B-roll for '%s': %s", query, e)

        return None
