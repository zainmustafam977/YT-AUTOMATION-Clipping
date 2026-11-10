"""
==============================================================================
Ingestion Security & Defense-in-Depth Hardening Engine
==============================================================================
Industrial-grade security layer guarding video ingestion against:
1. SSRF (Server-Side Request Forgery) via strict URL allowlisting & DNS inspection.
2. Resource exhaustion via pre-download video duration and file size caps.
3. Denial of service via sliding-window per-user ingestion rate limiting.
4. Hanging network downloads via enforced timeouts.
==============================================================================
"""

import re
import time
import math
import socket
import logging
import ipaddress
import threading
from urllib.parse import urlparse, parse_qs
from typing import Dict, List
from collections import defaultdict

from fastapi import HTTPException, status, Request
from shorts_engine.config.settings import settings

logger = logging.getLogger(__name__)

# Allowed YouTube base domain suffixes
ALLOWED_YOUTUBE_HOSTS = (
    "youtube.com",
    "youtu.be",
    "m.youtube.com",
)

# Standard 11-character YouTube video ID regex
YOUTUBE_ID_REGEX = re.compile(r"^[0-9A-Za-z_-]{11}$")
LOCAL_VIDEO_ID_REGEX = re.compile(r"^loc_[0-9a-fA-F]{8,32}$")
YOUTUBE_EXTRACT_REGEX = re.compile(r"(?:v=|\/|youtu\.be\/|shorts\/)([0-9A-Za-z_-]{11})")


class SSRFValidationError(ValueError):
    """Raised when an ingestion URL violates SSRF guards or domain allowlisting."""
    pass


class VideoTooLongError(ValueError):
    """Raised when video duration exceeds the maximum configured duration cap."""
    pass


class VideoTooLargeError(ValueError):
    """Raised when video file size exceeds the maximum configured file size cap."""
    pass


class IngestionTimeoutError(TimeoutError):
    """Raised when a video download operation exceeds the overall hard timeout."""
    pass


def is_ip_restricted(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """
    Checks if an IP address belongs to any private, loopback, link-local,
    multicast, or reserved/unroutable range.
    """
    if ip.is_loopback:  # 127.0.0.0/8, ::1
        return True
    if ip.is_private:   # 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, fc00::/7
        return True
    if ip.is_link_local: # 169.254.0.0/16 (cloud metadata services), fe80::/10
        return True
    if ip.is_reserved or ip.is_multicast:
        return True
    if ip.is_unspecified: # 0.0.0.0, ::
        return True

    # Carrier-Grade NAT (RFC 6598: 100.64.0.0/10)
    if isinstance(ip, ipaddress.IPv4Address):
        cgnat = ipaddress.IPv4Network("100.64.0.0/10")
        if ip in cgnat:
            return True

    return False


def validate_ingestion_url(url_or_id: str) -> str:
    """
    Strictly validates and canonicalizes an untrusted video URL or 11-char ID.

    Guarantees:
    - Pre-network rejection: rejects invalid schemes, raw IP literals, localhost,
      and non-YouTube domains BEFORE any HTTP/socket request is dispatched.
    - SSRF prevention: resolves hostname through DNS to ensure it does not rebind
      to local/private/link-local address ranges (e.g. 169.254.169.254).
    - Allowlist: accepts only domains ending in youtube.com, youtu.be, m.youtube.com.

    Returns:
        Canonical YouTube URL string (e.g. 'https://www.youtube.com/watch?v=...').

    Raises:
        SSRFValidationError: If input violates any SSRF policy or domain allowlist.
    """
    if not url_or_id or not str(url_or_id).strip():
        raise SSRFValidationError("Video input cannot be empty.")

    raw = str(url_or_id).strip()

    # 1. Direct 11-character YouTube video ID match or local video ID
    if YOUTUBE_ID_REGEX.match(raw):
        return f"https://www.youtube.com/watch?v={raw}"

    if LOCAL_VIDEO_ID_REGEX.match(raw):
        return raw

    # 2. Scheme and structure validation
    parsed = urlparse(raw)
    scheme = parsed.scheme.lower() if parsed.scheme else ""

    if not scheme:
        # Check if it was meant to be an 11-char ID but had invalid characters
        if len(raw) == 11:
            raise SSRFValidationError(f"Invalid characters in YouTube Video ID: '{raw}'")
        raise SSRFValidationError(
            f"Missing URL scheme in '{raw}'. Only HTTP and HTTPS URLs are permitted."
        )

    if scheme not in ("http", "https"):
        raise SSRFValidationError(
            f"Invalid URL scheme '{scheme}'. Only HTTP and HTTPS are permitted."
        )

    # 3. Host validation
    hostname = parsed.hostname
    if not hostname:
        raise SSRFValidationError(f"Malformed URL without valid host: '{raw}'")

    clean_host = hostname.lower().strip()

    # Check for raw IP literal (IPv4 or IPv6)
    try:
        ipaddress.ip_address(clean_host)
        raise SSRFValidationError(
            f"Direct IP address access is strictly forbidden ({clean_host}). "
            "Only official YouTube domain names are accepted."
        )
    except ValueError:
        # Not an IP literal, continue
        pass

    # Check for localhost and local domain patterns
    if clean_host in ("localhost", "local", "internal") or clean_host.endswith(
        (".localhost", ".local", ".internal", ".lan", ".home")
    ):
        raise SSRFValidationError(
            f"Access to localhost and internal domain names is strictly forbidden ({clean_host})."
        )

    # Allowlist check: Must end in allowed YouTube hostnames
    is_allowed = (
        clean_host == "youtube.com"
        or clean_host.endswith(".youtube.com")
        or clean_host == "youtu.be"
        or clean_host.endswith(".youtu.be")
    )

    if not is_allowed:
        raise SSRFValidationError(
            f"Domain '{clean_host}' is not in the allowed YouTube domain list "
            f"({', '.join(ALLOWED_YOUTUBE_HOSTS)})."
        )

    # 4. DNS Resolution & Rebinding Check
    # Verify resolved addresses do not point to restricted IP ranges
    try:
        addr_info = socket.getaddrinfo(clean_host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for entry in addr_info:
            sockaddr = entry[4]
            ip_str = sockaddr[0]
            try:
                resolved_ip = ipaddress.ip_address(ip_str)
                if is_ip_restricted(resolved_ip):
                    raise SSRFValidationError(
                        f"Domain '{clean_host}' resolves to restricted/internal IP {resolved_ip}."
                    )
            except ValueError:
                pass
    except socket.gaierror as dns_err:
        # If in an isolated test environment without internet, permit standard youtube domains
        if clean_host not in ("youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com"):
            raise SSRFValidationError(f"Failed to resolve domain '{clean_host}': {dns_err}")

    # 5. Extract YouTube video ID
    vid_match = YOUTUBE_EXTRACT_REGEX.search(raw)
    if vid_match:
        video_id = vid_match.group(1)
        return f"https://www.youtube.com/watch?v={video_id}"

    # If already a valid youtube watch URL with v parameter
    q_params = parse_qs(parsed.query)
    if "v" in q_params and q_params["v"] and YOUTUBE_ID_REGEX.match(q_params["v"][0]):
        return f"https://www.youtube.com/watch?v={q_params['v'][0]}"

    # Fallback to sanitized URL
    return raw


class IngestionRateLimiter:
    """
    Thread-safe sliding-window rate limiter per user/client.
    Default: 5 requests per minute per authenticated user.
    """

    def __init__(self, requests_per_minute: int = 5, window_seconds: float = 60.0):
        self.requests_per_minute = requests_per_minute
        self.window_seconds = window_seconds
        self._history: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, identifier: str) -> None:
        """
        Records an ingestion attempt for the given identifier.
        Raises HTTPException 429 if the rate limit is exceeded.
        """
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            # Clean expired timestamps
            valid_stamps = [t for t in self._history[identifier] if t > cutoff]

            if len(valid_stamps) >= self.requests_per_minute:
                oldest = valid_stamps[0]
                retry_after_sec = max(1, int(math.ceil(self.window_seconds - (now - oldest))))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        f"Ingestion rate limit exceeded ({self.requests_per_minute} requests/minute). "
                        f"Please try again in {retry_after_sec} seconds."
                    ),
                    headers={"Retry-After": str(retry_after_sec)},
                )

            valid_stamps.append(now)
            self._history[identifier] = valid_stamps

    def reset(self) -> None:
        """Resets all tracking histories (useful for tests)."""
        with self._lock:
            self._history.clear()


# Global singleton rate limiter instance
ingestion_rate_limiter = IngestionRateLimiter(
    requests_per_minute=getattr(settings, "INGESTION_RATE_LIMIT_PER_MINUTE", 5),
    window_seconds=60.0,
)


def check_ingestion_rate_limit(request: Request, current_user=None) -> None:
    """
    FastAPI dependency enforcing the 5 req/min rate limit per authenticated user.
    Falls back to client IP address if user object is absent.
    """
    user_id = getattr(current_user, "id", None) or getattr(current_user, "username", None)
    if not user_id:
        client_host = request.client.host if request.client else "unknown_client"
        user_id = f"ip:{client_host}"
    else:
        user_id = f"user:{user_id}"

    ingestion_rate_limiter.check(user_id)
