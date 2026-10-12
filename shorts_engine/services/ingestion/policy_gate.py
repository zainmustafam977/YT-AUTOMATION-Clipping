"""
Multi-Mode Content Policy Gate & Content Safety Triage (Pillar 2).
Pre-flight metadata probing via oEmbed & InnerTube Player API to classify
Restricted Mode, age gating, private/unavailable states, and sensitive themes.
Evaluates videos against settings.INGESTION_POLICY_MODE ('two_tier', 'strict_gate', 'auto_skip').
"""

import re
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
import httpx

from shorts_engine.config.settings import settings
from shorts_engine.services.ingestion.oauth_flow import YouTubeDeviceOAuth

logger = logging.getLogger(__name__)

OEMBED_URL = "https://www.youtube.com/oembed"
INNERTUBE_PLAYER_URL = "https://www.youtube.com/youtubei/v1/player"

# High-sensitivity keyword heuristics for rapid pre-download thematic triage
SENSITIVE_KEYWORD_PATTERNS = [
    r"\bdeath penalty\b",
    r"\bexecution\b",
    r"\bgore\b",
    r"\bsuicide\b",
    r"\bmurder\b",
    r"\bviolent crime\b",
    r"\bwar crime\b",
    r"\bmass casualty\b",
    r"\bnsfw\b",
    r"\bgraphic violence\b",
    r"\bexplicit\b",
]


@dataclass
class PolicyResult:
    video_id: str
    is_restricted: bool = False
    age_limit: int = 0
    is_private: bool = False
    is_unavailable: bool = False
    sensitive_themes: List[str] = field(default_factory=list)
    action: str = "allow"  # "allow", "skip", "hold", "require_auth"
    reason: str = ""
    title: Optional[str] = None
    uploader: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "video_id": self.video_id,
            "is_restricted": self.is_restricted,
            "age_limit": self.age_limit,
            "is_private": self.is_private,
            "is_unavailable": self.is_unavailable,
            "sensitive_themes": self.sensitive_themes,
            "action": self.action,
            "reason": self.reason,
            "title": self.title,
            "uploader": self.uploader,
        }


class PolicyGate:
    """Pre-flight content policy triager."""

    @classmethod
    def has_vault_authentication(cls) -> bool:
        """
        Verifies if Tier 1 (OAuth2) or Tier 2/3 (Cookies) credentials are active.
        """
        # 1. Check OAuth2 token
        if getattr(settings, "INGESTION_ENABLE_OAUTH", True):
            if YouTubeDeviceOAuth.has_active_oauth():
                return True

        # 2. Check Harvester cookies
        harvester_path = getattr(settings, "YOUTUBE_HARVESTER_COOKIES_PATH", None) or (settings.STORAGE_DIR / "harvester_cookies.txt.enc")
        if harvester_path and Path(harvester_path).exists() and Path(harvester_path).stat().st_size > 0:
            return True

        # 3. Check Manual cookies
        manual_path = getattr(settings, "YOUTUBE_COOKIES_PATH", None) or (settings.STORAGE_DIR / "cookies.txt")
        if manual_path and manual_path.exists() and manual_path.stat().st_size > 0:
            return True

        return False

    @classmethod
    def probe_oembed(cls, video_id: str, timeout_sec: float = 3.0) -> Dict[str, Any]:
        """Probes YouTube oEmbed endpoint for quick status, accessibility, and title."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            with httpx.Client(timeout=timeout_sec) as client:
                res = client.get(OEMBED_URL, params={"url": url, "format": "json"})
                if res.status_code == 200:
                    data = res.json()
                    return {
                        "accessible": True,
                        "status_code": 200,
                        "title": data.get("title"),
                        "author_name": data.get("author_name"),
                        "author_url": data.get("author_url"),
                    }
                return {
                    "accessible": False,
                    "status_code": res.status_code,
                    "error": f"oEmbed returned HTTP {res.status_code}",
                }
        except Exception as e:
            return {
                "accessible": False,
                "status_code": 0,
                "error": str(e),
            }

    @classmethod
    def probe_innertube(cls, video_id: str, timeout_sec: float = 4.0) -> Dict[str, Any]:
        """
        Probes YouTube InnerTube player endpoint for playabilityStatus,
        Restricted Mode indicators, and age gate details.
        """
        payload = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20240101.00.00",
                    "hl": "en",
                    "gl": "US",
                }
            },
            "videoId": video_id,
        }

        try:
            with httpx.Client(timeout=timeout_sec) as client:
                res = client.post(INNERTUBE_PLAYER_URL, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    playability = data.get("playabilityStatus", {})
                    p_status = playability.get("status", "OK")
                    reason = playability.get("reason", "")
                    messages = playability.get("messages", [])

                    v_details = data.get("videoDetails", {})
                    return {
                        "success": True,
                        "status": p_status,
                        "reason": reason,
                        "messages": messages,
                        "title": v_details.get("title"),
                        "author": v_details.get("author"),
                        "is_live_content": v_details.get("isLiveContent", False),
                        "raw_playability": playability,
                    }
                return {
                    "success": False,
                    "status_code": res.status_code,
                    "error": f"InnerTube returned HTTP {res.status_code}",
                }
        except Exception as e:
            return {
                "success": False,
                "status_code": 0,
                "error": str(e),
            }

    @classmethod
    def check_thematic_sensitivity(cls, text: str) -> List[str]:
        """Scans video title/description text for high-risk sensitive themes."""
        if not text:
            return []
        found = []
        low = text.lower()
        for pat in SENSITIVE_KEYWORD_PATTERNS:
            if re.search(pat, low):
                found.append(pat.replace(r"\b", "").replace(r"\b", "").strip())
        return list(set(found))

    @classmethod
    def assess_video(
        cls,
        video_id: str,
        policy_mode: Optional[str] = None,
        auth_available: Optional[bool] = None,
    ) -> PolicyResult:
        """
        Evaluates video accessibility, age limits, Restricted Mode flags, and safety themes.
        Triage postures:
        - 'auto_skip': automatically marks video skipped to safeguard channel monetization/algorithm.
        - 'strict_gate': holds sensitive or restricted videos in PENDING_POLICY_REVIEW.
        - 'two_tier': if authenticated, allows download with SENSITIVE_CONTENT badge; if unauthenticated, fails fast with POLICY_RESTRICTED (0 retry burns).
        """
        mode = (policy_mode or getattr(settings, "INGESTION_POLICY_MODE", "two_tier")).lower().strip()
        has_auth = auth_available if auth_available is not None else cls.has_vault_authentication()

        result = PolicyResult(video_id=video_id)

        # 1. InnerTube Probe
        inner = cls.probe_innertube(video_id)
        if inner.get("success"):
            status_str = str(inner.get("status", "OK")).upper()
            reason = inner.get("reason", "")
            raw_text = f"{reason} {' '.join(inner.get('messages') or [])}".lower()

            result.title = inner.get("title")
            result.uploader = inner.get("author")
            result.metadata["innertube"] = inner

            if status_str in ("LOGIN_REQUIRED", "AGE_CHECK_REQUIRED") or "sign in to confirm your age" in raw_text or "age-restricted" in raw_text:
                result.is_restricted = True
                result.age_limit = 18
                result.sensitive_themes.append("age_restricted")
                result.reason = reason or "Video is age-restricted"
            elif "restricted mode" in raw_text:
                result.is_restricted = True
                result.sensitive_themes.append("restricted_mode")
                result.reason = reason or "Content hidden in Restricted Mode"
            elif status_str in ("UNPLAYABLE", "ERROR"):
                if "private" in raw_text:
                    result.is_private = True
                    result.reason = reason or "Private video"
                else:
                    result.is_unavailable = True
                    result.reason = reason or f"Video unplayable: {status_str}"

        # 2. oEmbed Probe fallback & title enrichment
        oembed = cls.probe_oembed(video_id)
        if oembed.get("accessible"):
            if not result.title:
                result.title = oembed.get("title")
            if not result.uploader:
                result.uploader = oembed.get("author_name")
        else:
            code = oembed.get("status_code", 0)
            if code in (401, 403):
                result.is_restricted = True
                if not result.reason:
                    result.reason = f"oEmbed restricted access ({code})"
            elif code == 404:
                result.is_unavailable = True
                if not result.reason:
                    result.reason = "Video not found or deleted (HTTP 404)"

        # 3. Thematic content scan
        text_corpus = f"{result.title or ''} {result.reason or ''}"
        themes = cls.check_thematic_sensitivity(text_corpus)
        if themes:
            result.sensitive_themes.extend(themes)
            result.sensitive_themes = list(set(result.sensitive_themes))

        # 4. Multi-Mode Triage Evaluation
        is_flagged = result.is_restricted or result.age_limit > 0 or bool(result.sensitive_themes)

        if mode == "auto_skip":
            if is_flagged:
                result.action = "skip"
                theme_str = ", ".join(result.sensitive_themes) if result.sensitive_themes else "restricted content"
                result.reason = f"Auto-Skip policy: content flagged as {theme_str}"
            else:
                result.action = "allow"
                result.reason = "Video cleared policy gate (standard content)"
            return result

        if mode == "strict_gate":
            if is_flagged:
                result.action = "hold"
                theme_str = ", ".join(result.sensitive_themes) if result.sensitive_themes else "restricted content"
                result.reason = f"Strict gate: flagged as {theme_str}, holding for operator approval"
            else:
                result.action = "allow"
                result.reason = "Video cleared policy gate (standard content)"
            return result

        # Default mode: 'two_tier'
        if is_flagged:
            if has_auth:
                result.action = "allow"
                result.reason = "Authenticated session available; proceeding with SENSITIVE_CONTENT advisory"
            else:
                result.action = "require_auth"
                theme_str = ", ".join(result.sensitive_themes) if result.sensitive_themes else "restricted content"
                result.reason = f"Restricted content ({theme_str}) requires authenticated session (OAuth/cookies)"
        else:
            result.action = "allow"
            result.reason = "Video cleared policy gate (standard content)"

        return result


# Convenience alias
assess_video = PolicyGate.assess_video
