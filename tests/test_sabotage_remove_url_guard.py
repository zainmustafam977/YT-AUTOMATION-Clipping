"""Sabotage check 09: remove url_guard from webhooks.

The webhook engine validates URLs through is_safe_url. The
sabotage patches the guard to allow loopback, and the test
asserts the webhook creation now succeeds (demonstrating
the attack surface if the guard were removed).
"""
import pytest

from shorts_engine.services.webhooks import WebhookError, validate_url


def test_url_guard_blocks_loopback():
    with pytest.raises(WebhookError, match="SSRF"):
        validate_url("http://127.0.0.1:80")


def test_url_guard_blocks_private():
    with pytest.raises(WebhookError, match="SSRF"):
        validate_url("http://10.0.0.1/x")


def test_url_guard_blocks_metadata():
    with pytest.raises(WebhookError, match="SSRF"):
        validate_url("http://169.254.169.254/x")


def test_url_guard_blocks_ipv6_loopback():
    with pytest.raises(WebhookError, match="SSRF"):
        validate_url("http://[::1]:80/x")


def test_url_guard_rejects_file_scheme():
    with pytest.raises(WebhookError, match="scheme"):
        validate_url("file:///etc/passwd")


def test_sabotage_url_guard_disabled_allows_loopback(monkeypatch):
    """SABOTAGE: with url_guard bypassed, a webhook pointing at
    loopback would be accepted. This is the failing-path test."""
    monkeypatch.setattr(
        "shorts_engine.services.webhooks.is_safe_url", lambda url: True
    )
    # Now validate_url should let loopback through.
    validate_url("http://127.0.0.1:80/x")  # does not raise
