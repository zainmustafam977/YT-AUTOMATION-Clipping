"""
Monitoring and Alerting module: pushes failure and quota alerts to Slack, Discord,
Telegram, and Sentry when exceptions occur or dead-letter limits are reached.

Sentry SDK must be initialized at application startup (see run.py).
This module only calls capture_* — never re-initializes the SDK.
"""

import json
import logging
import urllib.request
from typing import Dict, Any, Optional

from shorts_engine.config.settings import settings

logger = logging.getLogger(__name__)


class PipelineAlerter:
    def __init__(self):
        self.slack_url = settings.SLACK_WEBHOOK_URL
        self.discord_url = settings.DISCORD_WEBHOOK_URL
        self.telegram_bot = settings.TELEGRAM_BOT_TOKEN
        self.telegram_chat = settings.TELEGRAM_CHAT_ID
        self.sentry_dsn = settings.SENTRY_DSN

    def alert(
        self,
        title: str,
        message: str,
        level: str = "ERROR",
        context: Optional[Dict[str, Any]] = None,
        exc: Optional[Exception] = None,
    ) -> bool:
        """
        Sends an alert through all configured notification channels.
        Always logs locally; dispatches to webhooks if configured.

        Args:
            title: Short alert title.
            message: Detailed alert message.
            level: Severity — ERROR, WARNING, CRITICAL, INFO.
            context: Optional dict of extra key/value context included in alert body.
            exc: Optional exception to forward to Sentry's capture_exception().
        """
        log_msg = f"[{level}] {title}: {message} | Context: {context or {}}"
        if level in ("ERROR", "CRITICAL"):
            logger.error(log_msg)
        elif level == "WARNING":
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

        sent = False
        slack_text = f"*{level}: {title}*\n{message}\n```{json.dumps(context or {}, indent=2)}```"

        # ── Slack ──────────────────────────────────────────────────────────────
        if self.slack_url:
            try:
                req = urllib.request.Request(
                    self.slack_url,
                    data=json.dumps({"text": slack_text}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=5)
                sent = True
            except Exception as e:
                logger.warning("Failed to send Slack alert: %s", e)

        # ── Discord ────────────────────────────────────────────────────────────
        if self.discord_url:
            try:
                req = urllib.request.Request(
                    self.discord_url,
                    data=json.dumps(
                        {"content": f"**{level}: {title}**\n{message}"}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=5)
                sent = True
            except Exception as e:
                logger.warning("Failed to send Discord alert: %s", e)

        # ── Telegram ───────────────────────────────────────────────────────────
        if self.telegram_bot and self.telegram_chat:
            try:
                tg_text = f"🚨 *{level}: {title}*\n{message}"
                if context:
                    ctx_str = "\n".join(f"• `{k}`: {v}" for k, v in context.items())
                    tg_text += f"\n\n{ctx_str}"
                payload = {
                    "chat_id": self.telegram_chat,
                    "text": tg_text,
                    "parse_mode": "Markdown",
                }
                url = f"https://api.telegram.org/bot{self.telegram_bot}/sendMessage"
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=5)
                sent = True
            except Exception as e:
                logger.warning("Failed to send Telegram alert: %s", e)

        # ── Sentry ─────────────────────────────────────────────────────────────
        if self.sentry_dsn:
            try:
                import sentry_sdk  # type: ignore

                if exc is not None:
                    with sentry_sdk.push_scope() as scope:
                        scope.set_tag("alert_level", level)
                        scope.set_tag("alert_title", title)
                        if context:
                            for k, v in context.items():
                                scope.set_extra(k, v)
                        sentry_sdk.capture_exception(exc)
                else:
                    sentry_sdk.capture_message(
                        f"{title}: {message}",
                        level=level.lower(),
                        extras=context or {},
                    )
                sent = True
            except Exception as e:
                logger.warning("Failed to send Sentry alert: %s", e)

        return sent
