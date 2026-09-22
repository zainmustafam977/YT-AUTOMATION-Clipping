"""
WebSub (PubSubHubbub) subscriber for YouTube channel detection.

Handles:
  - Subscribing to YouTube's hub for near-instant push notifications
  - Verifying incoming hub challenge requests (hub.mode=subscribe verification)
  - Validating push notifications via HMAC-SHA1 (X-Hub-Signature header)
  - Automatic 80% lease renewal via WebSubLeaseRenewer

YouTube's hub: https://pubsubhubbub.appspot.com/subscribe
YouTube's feed topic: https://www.youtube.com/xml/feeds/videos.xml?channel_id={id}
"""

import hashlib
import hmac
import json
import logging
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Tuple

from shorts_engine.config.settings import settings

logger = logging.getLogger(__name__)

YOUTUBE_TOPIC_URL = "https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}"
ATOM_NS = "http://www.w3.org/2005/Atom"
YT_NS = "http://www.youtube.com/xml/schemas/2015"

# Persisted subscription state (simple JSON sidecar; Postgres upgrade replaces this in M3)
_SUBS_STATE_FILE = settings.STORAGE_DIR / "websub_subscriptions.json"


def _load_subscriptions() -> dict:
    if _SUBS_STATE_FILE.exists():
        try:
            return json.loads(_SUBS_STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_subscriptions(subs: dict) -> None:
    _SUBS_STATE_FILE.write_text(json.dumps(subs, indent=2))


class WebSubSubscriber:
    """
    Sends subscribe/unsubscribe requests to YouTube's PubSubHubbub hub.
    """

    def __init__(self, hub_url: Optional[str] = None, callback_url: Optional[str] = None,
                 secret: Optional[str] = None):
        self.hub_url = hub_url or settings.WEBSUB_HUB_URL
        self.callback_url = callback_url or settings.WEBSUB_CALLBACK_URL
        self.secret = secret or settings.WEBSUB_SECRET

    def subscribe(self, channel_id: str, lease_seconds: Optional[int] = None) -> bool:
        """
        Sends a hub.mode=subscribe POST to YouTube's PubSubHubbub hub.
        Records subscription timestamp for lease renewal tracking.
        Returns True on HTTP 202 Accepted.
        """
        if not self.callback_url:
            logger.debug("WEBSUB_CALLBACK_URL is not configured. Cannot subscribe.")
            return False

        topic = YOUTUBE_TOPIC_URL.format(channel_id=channel_id)
        lease = lease_seconds or settings.WEBSUB_LEASE_SECONDS

        params = {
            "hub.mode": "subscribe",
            "hub.topic": topic,
            "hub.callback": self.callback_url,
            "hub.lease_seconds": str(lease),
        }
        if self.secret:
            params["hub.secret"] = self.secret

        body = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(
            self.hub_url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
        except Exception as e:
            logger.error("WebSub subscribe failed for channel %s: %s", channel_id, e)
            return False

        if status == 202:
            # Record subscription for lease renewal tracking
            subs = _load_subscriptions()
            subs[channel_id] = {
                "subscribed_at": datetime.now(tz=timezone.utc).isoformat(),
                "lease_seconds": lease,
                "topic": topic,
            }
            _save_subscriptions(subs)
            logger.info("WebSub subscription requested for channel %s (lease=%ds).", channel_id, lease)
            return True

        logger.warning(
            "WebSub hub returned unexpected status %d for channel %s.", status, channel_id
        )
        return False

    def verify_challenge(self, hub_mode: str, hub_topic: str, hub_challenge: str,
                         hub_lease_seconds: Optional[str] = None) -> Optional[str]:
        """
        Validates a hub GET verification challenge.
        Returns hub_challenge string if valid (must be echoed back as 200 response body).
        Returns None if the mode or topic is unexpected.
        """
        if hub_mode not in ("subscribe", "unsubscribe"):
            logger.warning("Unexpected hub.mode: %s", hub_mode)
            return None
        # Basic sanity: topic should reference YouTube
        if "youtube.com" not in hub_topic:
            logger.warning("Unexpected hub.topic: %s", hub_topic)
            return None
        logger.info("WebSub challenge verified for mode=%s topic=%s.", hub_mode, hub_topic)
        return hub_challenge


class WebSubNotificationParser:
    """
    Parses and authenticates incoming WebSub push notification payloads from YouTube.
    """

    def __init__(self, secret: Optional[str] = None):
        self.secret = secret or settings.WEBSUB_SECRET

    def verify_signature(self, body: bytes, x_hub_signature: Optional[str]) -> bool:
        """
        Verifies the X-Hub-Signature header using HMAC-SHA1.
        YouTube sends: X-Hub-Signature: sha1=<hex_digest>

        Uses hmac.compare_digest() for timing-safe comparison.
        Returns True if signature is valid or if no secret is configured (dev mode).
        """
        if not self.secret:
            logger.debug("No WEBSUB_SECRET configured — skipping signature verification (dev mode).")
            return True

        if not x_hub_signature or not x_hub_signature.startswith("sha1="):
            logger.warning("Missing or malformed X-Hub-Signature header.")
            return False

        expected_hex = x_hub_signature[len("sha1="):]
        computed = hmac.new(
            self.secret.encode("utf-8"),
            body,
            hashlib.sha1,
        ).hexdigest()

        if hmac.compare_digest(computed, expected_hex):
            return True

        logger.warning("X-Hub-Signature mismatch — possible spoofed notification.")
        return False

    def parse_notification(self, body: bytes) -> List[Tuple[str, str]]:
        """
        Parses a YouTube WebSub Atom push notification body.
        Returns list of (channel_id, video_id) tuples for each new/updated entry.
        """
        try:
            root = ET.fromstring(body)
        except ET.ParseError as e:
            logger.error("Failed to parse WebSub notification XML: %s", e)
            return []

        results: List[Tuple[str, str]] = []
        for entry in root.findall(f"{{{ATOM_NS}}}entry"):
            vid_elem = entry.find(f"{{{YT_NS}}}videoId")
            ch_elem = entry.find(f"{{{YT_NS}}}channelId")
            if vid_elem is not None and ch_elem is not None:
                results.append((ch_elem.text.strip(), vid_elem.text.strip()))

        return results


class WebSubLeaseRenewer:
    """
    Checks all active WebSub subscriptions and renews those that have reached
    the configured renewal threshold (default: 80% of lease lifetime).
    """

    def __init__(self, subscriber: Optional[WebSubSubscriber] = None):
        self.subscriber = subscriber or WebSubSubscriber()
        self.threshold = settings.WEBSUB_RENEW_THRESHOLD

    def check_and_renew_all(self) -> List[str]:
        """
        Iterates all stored subscriptions and renews those past the renewal threshold.
        Returns list of channel IDs that were renewed.
        """
        subs = _load_subscriptions()
        renewed: List[str] = []
        now = datetime.now(tz=timezone.utc)

        for channel_id, sub_info in subs.items():
            try:
                subscribed_at = datetime.fromisoformat(sub_info["subscribed_at"])
                lease_seconds = sub_info["lease_seconds"]
                elapsed = (now - subscribed_at).total_seconds()
                fraction = elapsed / lease_seconds

                if fraction >= self.threshold:
                    logger.info(
                        "Channel %s WebSub lease at %.0f%% — renewing.",
                        channel_id, fraction * 100
                    )
                    success = self.subscriber.subscribe(channel_id, lease_seconds=lease_seconds)
                    if success:
                        renewed.append(channel_id)
            except Exception as e:
                logger.error("Error checking lease for channel %s: %s", channel_id, e)

        return renewed
