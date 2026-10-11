"""
Standardized Event Taxonomy and Platform Event Emitter.

Defines the minimum platform event taxonomy:
1. stage.finished
2. stage.failed
3. stage.quarantined
4. video.ingested
5. clip.approved
6. clip.published
7. channel.health
8. quota.low

Provides `emit_platform_event` which synchronously:
1. Persists an EventLog record to the database (for catch-up and audits).
2. Broadcasts a PlatformEvent over the global event bus (for live SSE subscribers).
"""

import json
import logging
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session

from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import EventLog
from shorts_engine.services.events.bus import global_event_bus
from shorts_engine.services.events.models import PlatformEvent

logger = logging.getLogger(__name__)

# Standard Event Taxonomy Names
STAGE_FINISHED = "stage.finished"
STAGE_FAILED = "stage.failed"
STAGE_QUARANTINED = "stage.quarantined"
VIDEO_INGESTED = "video.ingested"
CLIP_APPROVED = "clip.approved"
CLIP_PUBLISHED = "clip.published"
CHANNEL_HEALTH = "channel.health"
QUOTA_LOW = "quota.low"

ALL_TAXONOMY_EVENTS: List[str] = [
    STAGE_FINISHED,
    STAGE_FAILED,
    STAGE_QUARANTINED,
    VIDEO_INGESTED,
    CLIP_APPROVED,
    CLIP_PUBLISHED,
    CHANNEL_HEALTH,
    QUOTA_LOW,
]


def emit_platform_event(
    event_name: str,
    payload: Optional[Dict[str, Any]] = None,
    entity_id: Optional[str] = None,
    entity_type: Optional[str] = None,
    level: str = "INFO",
    title: Optional[str] = None,
    message: Optional[str] = None,
    stage_progress_pct: Optional[float] = None,
    action_url: Optional[str] = None,
    category: Optional[str] = None,
    db: Optional[Session] = None,
) -> EventLog:
    """
    Standard platform emitter that records an event to the `event_logs` table
    and broadcasts it to all connected subscribers via `global_event_bus`.
    """
    event_data = dict(payload or {})

    # Auto-generate informative defaults if not supplied
    if not title:
        title = event_name.replace(".", " ").title()
    if not message:
        message = f"Event '{event_name}' occurred for entity {entity_id or 'system'}."

    if not category:
        if event_name.startswith("stage."):
            category = "pipeline"
        elif event_name.startswith("video."):
            category = "ingestion"
        elif event_name.startswith("clip."):
            category = "publishing"
        elif event_name.startswith("channel."):
            category = "system"
        elif event_name.startswith("quota."):
            category = "quota"
        else:
            category = "system"

    # 1. Persist to EventLog table
    def _persist(session: Session) -> EventLog:
        log_entry = EventLog(
            event_name=event_name,
            entity_id=entity_id,
            entity_type=entity_type,
            payload_json=json.dumps(event_data),
        )
        session.add(log_entry)
        session.commit()
        return log_entry

    if db is not None:
        try:
            persisted_log = _persist(db)
        except Exception as e:
            logger.error("Failed to persist EventLog with active session: %s", e)
            persisted_log = EventLog(
                event_name=event_name,
                entity_id=entity_id,
                entity_type=entity_type,
                payload_json=json.dumps(event_data),
            )
    else:
        try:
            with SessionLocal() as session:
                persisted_log = _persist(session)
        except Exception as e:
            logger.error("Failed to persist EventLog with scoped session: %s", e)
            persisted_log = EventLog(
                event_name=event_name,
                entity_id=entity_id,
                entity_type=entity_type,
                payload_json=json.dumps(event_data),
            )

    # 2. Broadcast via EventBus
    try:
        platform_event = PlatformEvent(
            event_name=event_name,
            category=category,
            level=level.upper(),
            title=title,
            message=message,
            stage_progress_pct=stage_progress_pct,
            entity_id=entity_id,
            entity_type=entity_type,
            action_url=action_url,
            details=event_data,
        )
        global_event_bus.publish_sync(platform_event)
    except Exception as bus_err:
        logger.warning("Failed to broadcast PlatformEvent to event bus: %s", bus_err)

    # 3. Webhook Alert Dispatch on Critical Telemetry Events
    try:
        from shorts_engine.services.monitoring.alerter import pipeline_alerter
        if event_name == STAGE_QUARANTINED:
            pipeline_alerter.alert_quarantined_job(
                job_id=entity_id or event_data.get("job_id", "unknown"),
                stage=event_data.get("stage", "UNKNOWN"),
                last_error=event_data.get("last_error", message),
                attempt_count=event_data.get("attempt_count", 0),
            )
        elif event_name == CHANNEL_HEALTH and (event_data.get("status") == "needs_reauth" or "invalid_grant" in str(event_data.get("error", ""))):
            pipeline_alerter.alert_channel_needs_reauth(
                channel_id=entity_id or event_data.get("channel_id", "unknown"),
                channel_name=event_data.get("channel_name", entity_id or "channel"),
                error=str(event_data.get("error", "OAuth refresh token invalidated")),
            )
        elif event_name == QUOTA_LOW:
            pipeline_alerter.alert_quota_low(
                remaining_units=event_data.get("remaining_units", 0),
                spent_units=event_data.get("spent_units", 0),
                limit=event_data.get("limit", 10000),
            )
    except Exception as alert_err:
        logger.debug("Failed to dispatch alert webhook: %s", alert_err)

    return persisted_log
