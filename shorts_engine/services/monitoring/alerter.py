"""
Monitoring and Alerting module: pushes failure and quota alerts to Slack, Discord,
Telegram, and Sentry when exceptions occur or dead-letter limits are reached.

Sentry SDK must be initialized at application startup (see run.py).
This module only calls capture_* — never re-initializes the SDK.
"""

import os
import json
import logging
import urllib.request
from datetime import datetime
from typing import Dict, Any, Optional

from shorts_engine.config.settings import settings

from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

_alerter_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="alerter")


class PipelineAlerter:
    def __init__(self, alert_webhook_url: Optional[str] = None):
        self.alert_webhook_url = alert_webhook_url or getattr(settings, "ALERT_WEBHOOK_URL", None) or os.environ.get("ALERT_WEBHOOK_URL")
        self.slack_url = getattr(settings, "SLACK_WEBHOOK_URL", None)
        self.discord_url = getattr(settings, "DISCORD_WEBHOOK_URL", None)
        self.telegram_bot = getattr(settings, "TELEGRAM_BOT_TOKEN", None)
        self.telegram_chat = getattr(settings, "TELEGRAM_CHAT_ID", None)
        self.sentry_dsn = getattr(settings, "SENTRY_DSN", None)

    def has_configured_channels(self) -> bool:
        generic_url = self.alert_webhook_url or getattr(settings, "ALERT_WEBHOOK_URL", None) or os.environ.get("ALERT_WEBHOOK_URL")
        return bool(
            generic_url
            or self.slack_url
            or self.discord_url
            or (self.telegram_bot and self.telegram_chat)
            or self.sentry_dsn
        )

    def alert(
        self,
        title: str,
        message: str,
        level: str = "ERROR",
        context: Optional[Dict[str, Any]] = None,
        exc: Optional[Exception] = None,
        wait: bool = False,
    ) -> bool:
        """
        Sends an alert through all configured notification channels.
        Always logs locally immediately; dispatches to webhooks via background
        threadpool so worker pipelines are never blocked by external network latency (PLAN-P2-07 / F-24).

        Args:
            title: Short alert title.
            message: Detailed alert message.
            level: Severity — ERROR, WARNING, CRITICAL, INFO.
            context: Optional dict of extra key/value context included in alert body.
            exc: Optional exception to forward to Sentry's capture_exception().
            wait: If True, waits for webhook delivery synchronously (default: False).
        """
        log_msg = f"[{level}] {title}: {message} | Context: {context or {}}"
        if level in ("ERROR", "CRITICAL"):
            logger.error(log_msg)
        elif level == "WARNING":
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

        if not self.has_configured_channels():
            return False

        # In-process Sentry capture runs synchronously without thread delay
        sentry_sent = False
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
                sentry_sent = True
            except Exception as e:
                logger.warning("Failed to send Sentry alert: %s", e)

        generic_url = self.alert_webhook_url or getattr(settings, "ALERT_WEBHOOK_URL", None) or os.environ.get("ALERT_WEBHOOK_URL")
        has_webhooks = bool(
            generic_url
            or self.slack_url
            or self.discord_url
            or (self.telegram_bot and self.telegram_chat)
        )
        if not has_webhooks:
            return sentry_sent

        if wait:
            webhook_sent = self._dispatch_webhooks(title, message, level, context, exc)
            return sentry_sent or webhook_sent

        try:
            _alerter_pool.submit(self._dispatch_webhooks, title, message, level, context, exc)
            return True
        except Exception as e:
            logger.warning("Could not submit alert to threadpool: %s", e)
            webhook_sent = self._dispatch_webhooks(title, message, level, context, exc)
            return sentry_sent or webhook_sent

    def _dispatch_webhooks(
        self,
        title: str,
        message: str,
        level: str = "ERROR",
        context: Optional[Dict[str, Any]] = None,
        exc: Optional[Exception] = None,
    ) -> bool:
        sent = False
        slack_text = f"*{level}: {title}*\n{message}\n```{json.dumps(context or {}, indent=2)}```"

        # ── Generic Outgoing Webhook ──────────────────────────────────────────
        generic_url = self.alert_webhook_url or getattr(settings, "ALERT_WEBHOOK_URL", None) or os.environ.get("ALERT_WEBHOOK_URL")
        if generic_url:
            try:
                alert_payload = {
                    "event": "alert",
                    "alert_type": (context or {}).get("alert_type", "general"),
                    "level": level,
                    "title": title,
                    "message": message,
                    "timestamp": datetime.utcnow().isoformat(),
                    "context": context or {},
                }
                req = urllib.request.Request(
                    generic_url,
                    data=json.dumps(alert_payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if getattr(resp, "status", 200) < 400:
                        sent = True
            except Exception as e:
                logger.warning("Failed to send Generic Alert Webhook to %s: %s", generic_url, e)

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

        return sent

    def alert_quarantined_job(
        self,
        job_id: str,
        stage: str,
        last_error: str,
        attempt_count: int = 0,
        wait: bool = False,
    ) -> bool:
        return self.alert(
            title=f"Job Quarantined: {stage}",
            message=f"Job {job_id[:8]} quarantined in stage '{stage}': {last_error}",
            level="CRITICAL",
            context={
                "alert_type": "quarantined_job",
                "job_id": job_id,
                "stage": stage,
                "last_error": str(last_error),
                "attempt_count": attempt_count,
            },
            wait=wait,
        )

    def alert_channel_needs_reauth(
        self,
        channel_id: str,
        channel_name: str,
        error: str,
        wait: bool = False,
    ) -> bool:
        return self.alert(
            title=f"Channel Needs Re-Auth: {channel_name}",
            message=f"OAuth token for channel '{channel_name}' ({channel_id}) was invalidated: {error}",
            level="ERROR",
            context={
                "alert_type": "channel_needs_reauth",
                "channel_id": channel_id,
                "channel_name": channel_name,
                "error": str(error),
            },
            wait=wait,
        )

    def alert_disk_space(
        self,
        percent_used: float,
        free_gb: float,
        total_gb: float,
        path: str,
        wait: bool = False,
    ) -> bool:
        return self.alert(
            title=f"Disk Space Alert: {percent_used}% Used",
            message=f"Media root '{path}' reached {percent_used}% disk usage. Only {free_gb} GB free out of {total_gb} GB.",
            level="WARNING" if percent_used < 90.0 else "CRITICAL",
            context={
                "alert_type": "disk_space_critical",
                "percent_used": percent_used,
                "free_gb": free_gb,
                "total_gb": total_gb,
                "path": str(path),
            },
            wait=wait,
        )

    def alert_quota_low(
        self,
        remaining_units: int,
        spent_units: int,
        limit: int,
        wait: bool = False,
    ) -> bool:
        pct_remaining = round((remaining_units / limit) * 100, 1) if limit > 0 else 0
        return self.alert(
            title=f"Quota Alert: {pct_remaining}% Remaining",
            message=f"Daily YouTube quota is low: {remaining_units} units remaining ({spent_units} spent of {limit}).",
            level="WARNING",
            context={
                "alert_type": "quota_exhaustion_warning",
                "remaining_units": remaining_units,
                "spent_units": spent_units,
                "daily_limit": limit,
                "remaining_pct": pct_remaining,
            },
            wait=wait,
        )


pipeline_alerter = PipelineAlerter()

