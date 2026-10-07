"""
Dynamic Proxy Mesh & Anti-Bot Defense Manager (Pillar 3).
Provides:
1. Thread-safe round-robin proxy rotation across INGESTION_PROXY_POOL.
2. Real-time HTTP ping & egress IP diagnostic testing.
3. Automated HTTP 429 rate limit failover & cooldown degradation.
4. Strict Proxy Enforcement vs Graceful Direct Fallback postures.
"""

import time
import logging
import threading
from typing import Optional, Dict, Any, List
import httpx

from shorts_engine.config.settings import settings
from shorts_engine.services.events.bus import global_event_bus
from shorts_engine.services.events.models import PlatformEvent

logger = logging.getLogger(__name__)

IP_CHECK_SERVICES = [
    "https://api.ipify.org?format=json",
    "https://httpbin.org/ip",
    "https://icanhazip.com",
]


class ProxyManager:
    """Manages proxy pool rotation, health states, and anti-bot failover."""

    _lock = threading.Lock()
    _round_robin_index: int = 0
    _degraded_proxies: Dict[str, float] = {}  # proxy_url -> degraded_until_timestamp
    COOLDOWN_SECONDS: float = 600.0  # 10 minutes degradation on HTTP 429

    @classmethod
    def get_configured_proxies(cls) -> List[str]:
        """Returns clean list of configured proxy URLs from settings."""
        return settings.get_proxy_pool()

    @classmethod
    def reset_degraded_proxies(cls) -> None:
        """Clears all degradation records."""
        with cls._lock:
            cls._degraded_proxies.clear()
            cls._round_robin_index = 0

    @classmethod
    def test_proxy(cls, proxy_url: str, timeout_sec: float = 6.0) -> Dict[str, Any]:
        """
        Executes an egress ping test through the specified proxy.
        Measures network latency in milliseconds and retrieves external IP.
        """
        clean_url = proxy_url.strip()
        if not clean_url:
            return {"healthy": False, "error": "Empty proxy URL", "url": clean_url}

        start_time = time.time()

        for test_endpoint in IP_CHECK_SERVICES:
            try:
                client_kwargs = {"timeout": timeout_sec, "follow_redirects": True}
                try:
                    client = httpx.Client(proxy=clean_url, **client_kwargs)
                except TypeError:
                    client = httpx.Client(proxies={"http://": clean_url, "https://": clean_url}, **client_kwargs)

                with client:
                    res = client.get(test_endpoint)
                    if res.status_code == 200:
                        elapsed_ms = round((time.time() - start_time) * 1000.0, 1)
                        external_ip = "unknown"
                        try:
                            if "{" in res.text:
                                external_ip = res.json().get("ip", "unknown")
                            else:
                                external_ip = res.text.strip()
                        except Exception:
                            external_ip = res.text.strip()

                        return {
                            "healthy": True,
                            "latency_ms": elapsed_ms,
                            "external_ip": external_ip,
                            "url": clean_url,
                            "endpoint_used": test_endpoint,
                        }
            except Exception as e:
                logger.debug("Proxy test endpoint %s failed on %s: %s", test_endpoint, clean_url, e)

        elapsed_ms = round((time.time() - start_time) * 1000.0, 1)
        return {
            "healthy": False,
            "latency_ms": elapsed_ms,
            "error": "Failed to connect to egress verification endpoints through proxy",
            "url": clean_url,
        }

    @classmethod
    def get_next_proxy(cls, strict_mode: Optional[bool] = None) -> Optional[str]:
        """
        Selects next available healthy proxy using round-robin rotation.
        If all proxies are degraded or pool is empty:
        - If strict_mode is True: raises RuntimeError.
        - If strict_mode is False: returns None (allowing direct fallback).
        """
        pool = cls.get_configured_proxies()
        is_strict = strict_mode if strict_mode is not None else getattr(settings, "INGESTION_PROXY_STRICT_MODE", False)

        if not pool:
            if is_strict:
                raise RuntimeError("Strict Proxy Enforcement is enabled, but INGESTION_PROXY_POOL is empty.")
            return None

        now = time.time()
        with cls._lock:
            # Clean expired cooldowns
            expired = [p for p, until in cls._degraded_proxies.items() if until <= now]
            for p in expired:
                del cls._degraded_proxies[p]

            healthy_pool = [p for p in pool if p not in cls._degraded_proxies]

            if not healthy_pool:
                logger.warning("All %d proxies in pool are currently marked degraded.", len(pool))
                if is_strict:
                    raise RuntimeError(
                        f"Strict Proxy Enforcement rejected download: all {len(pool)} proxies in pool are degraded due to rate-limiting."
                    )
                return None

            selected = healthy_pool[cls._round_robin_index % len(healthy_pool)]
            cls._round_robin_index = (cls._round_robin_index + 1) % len(healthy_pool)
            return selected

    @classmethod
    def handle_429_rate_limit(cls, failed_proxy: Optional[str]) -> None:
        """
        Marks a proxy as degraded for COOLDOWN_SECONDS and publishes an anti-bot alert.
        """
        if not failed_proxy:
            return

        clean_proxy = failed_proxy.strip()
        until = time.time() + cls.COOLDOWN_SECONDS
        with cls._lock:
            cls._degraded_proxies[clean_proxy] = until

        logger.warning(
            "Proxy %s encountered HTTP 429 rate limit. Placed on cooldown for %ds.",
            clean_proxy,
            int(cls.COOLDOWN_SECONDS),
        )

        try:
            event = PlatformEvent(
                category="ingestion",
                level="WARNING",
                title="Proxy 429 Rate Limit Detected",
                message=f"Proxy node {clean_proxy} hit YouTube rate limits. Cycling to next node in mesh.",
                details={"proxy": clean_proxy, "cooldown_sec": cls.COOLDOWN_SECONDS},
            )
            global_event_bus.publish_sync(event)
        except Exception:
            pass

    @classmethod
    def test_all_proxies(cls) -> List[Dict[str, Any]]:
        """Pings and tests all proxies in INGESTION_PROXY_POOL."""
        pool = cls.get_configured_proxies()
        results = []
        for p in pool:
            results.append(cls.test_proxy(p))
        return results

    @classmethod
    def get_status(cls) -> Dict[str, Any]:
        """Returns diagnostic overview of proxy pool and current postures."""
        pool = cls.get_configured_proxies()
        now = time.time()
        with cls._lock:
            degraded_count = sum(1 for until in cls._degraded_proxies.values() if until > now)

        strict_mode = getattr(settings, "INGESTION_PROXY_STRICT_MODE", False)
        return {
            "total_proxies": len(pool),
            "healthy_proxies": max(0, len(pool) - degraded_count),
            "degraded_proxies": degraded_count,
            "strict_mode": strict_mode,
            "proxies": pool,
            "has_active_pool": len(pool) > 0,
        }


# Convenience singleton aliases
get_next_proxy = ProxyManager.get_next_proxy
test_proxy = ProxyManager.test_proxy
handle_429_rate_limit = ProxyManager.handle_429_rate_limit
get_proxy_status = ProxyManager.get_status
