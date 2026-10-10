"""
Tests for PipelineAlerter: Telegram API, Sentry capture, and graceful
no-op when notification channels are unconfigured.
"""

import json
from unittest.mock import patch, MagicMock
from shorts_engine.services.monitoring.alerter import PipelineAlerter


def _make_alerter(**kwargs) -> PipelineAlerter:
    """Creates a PipelineAlerter with settings overridden via mock."""
    alerter = PipelineAlerter.__new__(PipelineAlerter)
    alerter.slack_url = kwargs.get("slack_url")
    alerter.discord_url = kwargs.get("discord_url")
    alerter.telegram_bot = kwargs.get("telegram_bot")
    alerter.telegram_chat = kwargs.get("telegram_chat")
    alerter.sentry_dsn = kwargs.get("sentry_dsn")
    return alerter


def test_telegram_alert_posts_to_api():
    """Alert with Telegram credentials configured must POST to the Telegram Bot API."""
    alerter = _make_alerter(telegram_bot="BOT_TOKEN_123", telegram_chat="CHAT_ID_456")

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        sent = alerter.alert("Test Title", "Test message", level="ERROR",
                             context={"video_id": "abc123"})

    assert sent is True
    # Verify the Telegram API URL was targeted
    call_args = mock_urlopen.call_args_list
    tg_calls = [c for c in call_args
                if "api.telegram.org" in getattr(c[0][0], "full_url", "")]
    assert len(tg_calls) == 1, "Expected exactly one Telegram API call"

    # Verify payload contains expected fields
    req_obj = tg_calls[0][0][0]  # first positional arg = Request object
    payload = json.loads(req_obj.data.decode("utf-8"))
    assert payload["chat_id"] == "CHAT_ID_456"
    assert "Test Title" in payload["text"]


def test_sentry_alert_calls_capture_message():
    """Alert with SENTRY_DSN set must call sentry_sdk.capture_message."""
    alerter = _make_alerter(sentry_dsn="https://fake@sentry.io/123")

    mock_sentry = MagicMock()
    mock_sentry.push_scope = MagicMock(return_value=MagicMock(__enter__=MagicMock(return_value=MagicMock()),
                                                                __exit__=MagicMock(return_value=False)))

    with patch.dict("sys.modules", {"sentry_sdk": mock_sentry}):
        sent = alerter.alert("Sentry Title", "Sentry message", level="WARNING")

    mock_sentry.capture_message.assert_called_once()
    call_kwargs = mock_sentry.capture_message.call_args
    assert "Sentry Title" in call_kwargs[0][0]
    assert sent is True


def test_alert_gracefully_skips_unconfigured_channels():
    """When no notification channels are configured, alert should return False but never raise."""
    alerter = _make_alerter()  # all channels None/unset

    try:
        sent = alerter.alert("No channels", "Should not crash", level="INFO")
    except Exception as e:
        raise AssertionError(f"alert() raised an unexpected exception: {e}") from e

    assert sent is False, "sent must be False when no channels are configured"
