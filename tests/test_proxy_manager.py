"""
Tests for Dynamic Proxy Mesh & Anti-Bot Defense Manager (Pillar 3).
Verifies:
1. Thread-safe round-robin proxy rotation.
2. 429 rate limit backoff (10-minute cooldown & degradation tracking).
3. Strict mode enforcement (RuntimeError vs direct fallback).
4. Real-time egress ping diagnostics with mocked httpx.
"""

import time
import pytest
from unittest.mock import patch, MagicMock
import httpx

from shorts_engine.services.ingestion.proxy_manager import (
    ProxyManager,
)


@pytest.fixture(autouse=True)
def clean_proxy_manager():
    """Resets ProxyManager state before and after each test."""
    ProxyManager.reset_degraded_proxies()
    yield
    ProxyManager.reset_degraded_proxies()


def test_round_robin_rotation():
    """Round-robin proxy rotation cycles uniformly through configured proxies."""
    mock_pool = [
        "http://proxy1.internal:8080",
        "http://proxy2.internal:8080",
        "http://proxy3.internal:8080",
    ]
    with patch.object(ProxyManager, "get_configured_proxies", return_value=mock_pool):
        results = [ProxyManager.get_next_proxy(strict_mode=False) for _ in range(6)]
        assert results == [
            "http://proxy1.internal:8080",
            "http://proxy2.internal:8080",
            "http://proxy3.internal:8080",
            "http://proxy1.internal:8080",
            "http://proxy2.internal:8080",
            "http://proxy3.internal:8080",
        ]


def test_handle_429_rate_limit_degradation():
    """Proxy hitting 429 is marked degraded and excluded from round-robin pool."""
    mock_pool = [
        "http://proxy1.internal:8080",
        "http://proxy2.internal:8080",
    ]
    with patch.object(ProxyManager, "get_configured_proxies", return_value=mock_pool):
        # Initial status
        status = ProxyManager.get_status()
        assert status["total_proxies"] == 2
        assert status["healthy_proxies"] == 2
        assert status["degraded_proxies"] == 0

        # Mark proxy1 as rate-limited
        ProxyManager.handle_429_rate_limit("http://proxy1.internal:8080")

        # Status after 429
        status = ProxyManager.get_status()
        assert status["healthy_proxies"] == 1
        assert status["degraded_proxies"] == 1

        # Only proxy2 should now be selected
        for _ in range(3):
            assert ProxyManager.get_next_proxy(strict_mode=False) == "http://proxy2.internal:8080"


def test_429_cooldown_expiry():
    """After cooldown window expires, degraded proxy returns to healthy rotation."""
    mock_pool = ["http://proxy1.internal:8080"]
    with patch.object(ProxyManager, "get_configured_proxies", return_value=mock_pool):
        ProxyManager.handle_429_rate_limit("http://proxy1.internal:8080")

        # Fast forward time beyond COOLDOWN_SECONDS
        with patch("time.time", return_value=time.time() + ProxyManager.COOLDOWN_SECONDS + 5):
            res = ProxyManager.get_next_proxy(strict_mode=False)
            assert res == "http://proxy1.internal:8080"
            status = ProxyManager.get_status()
            assert status["degraded_proxies"] == 0
            assert status["healthy_proxies"] == 1


def test_strict_mode_empty_pool_raises():
    """Strict mode raises RuntimeError if no proxies are configured."""
    with patch.object(ProxyManager, "get_configured_proxies", return_value=[]):
        with pytest.raises(RuntimeError, match="Strict Proxy Enforcement is enabled, but INGESTION_PROXY_POOL is empty"):
            ProxyManager.get_next_proxy(strict_mode=True)


def test_direct_fallback_empty_pool_returns_none():
    """Non-strict mode returns None when pool is empty, allowing direct fallback."""
    with patch.object(ProxyManager, "get_configured_proxies", return_value=[]):
        assert ProxyManager.get_next_proxy(strict_mode=False) is None


def test_strict_mode_all_degraded_raises():
    """Strict mode raises RuntimeError when all configured proxies are degraded."""
    mock_pool = ["http://node1:8080", "http://node2:8080"]
    with patch.object(ProxyManager, "get_configured_proxies", return_value=mock_pool):
        ProxyManager.handle_429_rate_limit("http://node1:8080")
        ProxyManager.handle_429_rate_limit("http://node2:8080")

        with pytest.raises(RuntimeError, match="all 2 proxies in pool are degraded"):
            ProxyManager.get_next_proxy(strict_mode=True)


def test_direct_fallback_all_degraded_returns_none():
    """Non-strict mode returns None when all proxies are degraded."""
    mock_pool = ["http://node1:8080"]
    with patch.object(ProxyManager, "get_configured_proxies", return_value=mock_pool):
        ProxyManager.handle_429_rate_limit("http://node1:8080")
        assert ProxyManager.get_next_proxy(strict_mode=False) is None


def test_test_proxy_empty_url():
    """Passing empty proxy URL returns healthy=False immediately."""
    res = ProxyManager.test_proxy("")
    assert res["healthy"] is False
    assert res["error"] == "Empty proxy URL"


def test_test_proxy_success():
    """Egress ping test succeeds and parses external IP via httpx."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = '{"ip": "198.51.100.42"}'
    mock_response.json.return_value = {"ip": "198.51.100.42"}

    with patch("httpx.Client.get", return_value=mock_response):
        res = ProxyManager.test_proxy("http://proxy.test:8080", timeout_sec=2.0)
        assert res["healthy"] is True
        assert res["external_ip"] == "198.51.100.42"
        assert res["url"] == "http://proxy.test:8080"
        assert "latency_ms" in res


def test_test_proxy_all_endpoints_fail():
    """Egress ping test reports failure when all IP check endpoints fail."""
    with patch("httpx.Client.get", side_effect=httpx.ConnectTimeout("Connection timed out")):
        res = ProxyManager.test_proxy("http://broken-proxy:8080", timeout_sec=1.0)
        assert res["healthy"] is False
        assert "Failed to connect" in res["error"]
        assert res["url"] == "http://broken-proxy:8080"


def test_test_all_proxies_summary():
    """test_all_proxies pings each proxy and aggregates results."""
    mock_pool = ["http://p1:8080", "http://p2:8080"]
    with patch.object(ProxyManager, "get_configured_proxies", return_value=mock_pool):
        with patch.object(ProxyManager, "test_proxy", side_effect=[
            {"healthy": True, "url": "http://p1:8080", "latency_ms": 45.0, "external_ip": "1.1.1.1"},
            {"healthy": False, "url": "http://p2:8080", "latency_ms": 1000.0, "error": "Timeout"},
        ]):
            results = ProxyManager.test_all_proxies()
            assert len(results) == 2
            assert results[0]["healthy"] is True
            assert results[1]["healthy"] is False


def test_handle_429_empty_input_noop():
    """handle_429_rate_limit with None or empty string does nothing."""
    ProxyManager.handle_429_rate_limit(None)
    ProxyManager.handle_429_rate_limit("")
    status = ProxyManager.get_status()
    assert status["degraded_proxies"] == 0
