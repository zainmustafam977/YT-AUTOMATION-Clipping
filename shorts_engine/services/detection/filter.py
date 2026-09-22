"""
Shorts Detection Filter: Identifies existing YouTube Shorts to avoid redundant processing.
Applies #Shorts hashtag detection (primary) and <= 180s duration boundary (secondary).
"""

import logging
from typing import Tuple, Optional, List
from shorts_engine.config.settings import settings

logger = logging.getLogger(__name__)


def is_existing_short(
    title: str,
    duration_sec: Optional[float] = None,
    tags: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    """
    Checks whether a video is already a YouTube Short so it can be skipped before downloading/rendering.
    Primary signal: #Shorts tag in title or tags.
    Secondary signal: duration <= settings.MAX_EXTENDED_DURATION_SEC (180s).

    Returns (is_short: bool, reason: str).
    """
    title_lower = (title or "").lower()

    # 1. Primary signal: #Shorts tag in title
    if "#shorts" in title_lower or "#short" in title_lower:
        return True, f"title contains #Shorts tag ('{title}')"

    # 1b. Primary signal: #shorts in tags list
    if tags:
        for t in tags:
            if t.lower().strip() in ("#shorts", "#short", "shorts", "short"):
                return True, f"tags contain #Shorts tag ('{t}')"

    # 2. Secondary signal: duration <= 180s ceiling
    if duration_sec is not None and 0 < duration_sec <= settings.MAX_EXTENDED_DURATION_SEC:
        return True, f"duration {duration_sec}s <= {settings.MAX_EXTENDED_DURATION_SEC}s ceiling"

    return False, ""
