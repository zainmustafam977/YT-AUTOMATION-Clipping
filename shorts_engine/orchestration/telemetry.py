"""
Structured Telemetry and Event Emitter for Pipeline Orchestration.
Provides a swappable emit interface for:
- Job Finished
- Job Retried
- Job Quarantined

Defaults to StructuredLogEmitter (JSON lines) with swappable EventBusEmitter.
"""

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Dict, Any

logger = logging.getLogger("shorts_engine.telemetry")


class TelemetryEmitter(ABC):
    """Abstract interface for pipeline telemetry events."""

    @abstractmethod
    def emit_job_finished(
        self,
        job_id: str,
        stage: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """Emitted when a pipeline job finishes successfully."""
        pass

    @abstractmethod
    def emit_job_retried(
        self,
        job_id: str,
        attempt_count: int,
        next_run_at: datetime,
        error: str,
        stage: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Emitted when a job encounters a transient failure and is scheduled for backoff retry."""
        pass

    @abstractmethod
    def emit_job_quarantined(
        self,
        job_id: str,
        last_error: str,
        stage: Optional[str] = None,
        attempt_count: int = 0,
        **kwargs: Any,
    ) -> None:
        """Emitted when a job encounters a permanent failure or exhausts maximum attempts."""
        pass


class StructuredLogEmitter(TelemetryEmitter):
    """Default telemetry emitter: writes structured JSON lines to the logger."""

    def __init__(self, log_level: int = logging.INFO):
        self.log_level = log_level

    def _emit_json(self, event_name: str, payload: Dict[str, Any]) -> None:
        data = {
            "telemetry_event": event_name,
            "timestamp": datetime.utcnow().isoformat(),
            **payload,
        }
        logger.log(self.log_level, json.dumps(data))

    def emit_job_finished(
        self,
        job_id: str,
        stage: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        self._emit_json("job_finished", {
            "job_id": job_id,
            "stage": stage or "COMPLETED",
            "status": "DONE",
            "result": result or {},
            **kwargs,
        })

    def emit_job_retried(
        self,
        job_id: str,
        attempt_count: int,
        next_run_at: datetime,
        error: str,
        stage: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        self._emit_json("job_retried", {
            "job_id": job_id,
            "attempt_count": attempt_count,
            "next_run_at": next_run_at.isoformat() if isinstance(next_run_at, datetime) else str(next_run_at),
            "error": str(error),
            "stage": stage or "UNKNOWN",
            **kwargs,
        })

    def emit_job_quarantined(
        self,
        job_id: str,
        last_error: str,
        stage: Optional[str] = None,
        attempt_count: int = 0,
        **kwargs: Any,
    ) -> None:
        self._emit_json("job_quarantined", {
            "job_id": job_id,
            "last_error": str(last_error),
            "stage": stage or "UNKNOWN",
            "attempt_count": attempt_count,
            "status": "QUARANTINED",
            **kwargs,
        })


class EventBusEmitter(TelemetryEmitter):
    """Adapter emitting to the application platform EventBus."""

    def emit_job_finished(
        self,
        job_id: str,
        stage: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        try:
            from shorts_engine.services.events.bus import global_event_bus
            from shorts_engine.services.events.models import PlatformEvent
            global_event_bus.publish_sync(PlatformEvent(
                category="pipeline",
                level="INFO",
                title="Job Finished",
                message=f"Job {job_id[:8]} finished successfully in stage {stage or 'DONE'}.",
                entity_id=job_id,
                entity_type="pipeline_job",
                details=result or {},
            ))
        except Exception:
            pass

    def emit_job_retried(
        self,
        job_id: str,
        attempt_count: int,
        next_run_at: datetime,
        error: str,
        stage: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        try:
            from shorts_engine.services.events.bus import global_event_bus
            from shorts_engine.services.events.models import PlatformEvent
            global_event_bus.publish_sync(PlatformEvent(
                category="pipeline",
                level="WARNING",
                title=f"Job Retried (Attempt {attempt_count})",
                message=f"Job {job_id[:8]} retried: {str(error)[:200]}",
                entity_id=job_id,
                entity_type="pipeline_job",
                details={"attempt_count": attempt_count, "next_run_at": str(next_run_at), "stage": stage},
            ))
        except Exception:
            pass

    def emit_job_quarantined(
        self,
        job_id: str,
        last_error: str,
        stage: Optional[str] = None,
        attempt_count: int = 0,
        **kwargs: Any,
    ) -> None:
        try:
            from shorts_engine.services.events.bus import global_event_bus
            from shorts_engine.services.events.models import PlatformEvent
            global_event_bus.publish_sync(PlatformEvent(
                category="pipeline",
                level="ERROR",
                title="Job Quarantined",
                message=f"Job {job_id[:8]} quarantined in stage {stage}: {str(last_error)[:200]}",
                entity_id=job_id,
                entity_type="pipeline_job",
                details={"last_error": last_error, "stage": stage, "attempt_count": attempt_count},
            ))
        except Exception:
            pass


class CompositeEmitter(TelemetryEmitter):
    """Fans out telemetry events to multiple backend emitters."""

    def __init__(self, emitters: list[TelemetryEmitter]):
        self.emitters = emitters

    def emit_job_finished(self, job_id: str, stage: Optional[str] = None, result: Optional[Dict[str, Any]] = None, **kwargs: Any) -> None:
        for em in self.emitters:
            em.emit_job_finished(job_id, stage=stage, result=result, **kwargs)

    def emit_job_retried(self, job_id: str, attempt_count: int, next_run_at: datetime, error: str, stage: Optional[str] = None, **kwargs: Any) -> None:
        for em in self.emitters:
            em.emit_job_retried(job_id, attempt_count=attempt_count, next_run_at=next_run_at, error=error, stage=stage, **kwargs)

    def emit_job_quarantined(self, job_id: str, last_error: str, stage: Optional[str] = None, attempt_count: int = 0, **kwargs: Any) -> None:
        for em in self.emitters:
            em.emit_job_quarantined(job_id, last_error=last_error, stage=stage, attempt_count=attempt_count, **kwargs)


# Global default emitter instance
_GLOBAL_EMITTER: TelemetryEmitter = StructuredLogEmitter()


def get_telemetry_emitter() -> TelemetryEmitter:
    """Gets the active telemetry emitter."""
    return _GLOBAL_EMITTER


def set_telemetry_emitter(emitter: TelemetryEmitter) -> None:
    """Sets the active telemetry emitter (allows swappable destinations)."""
    global _GLOBAL_EMITTER
    _GLOBAL_EMITTER = emitter


def emit(event_type: str, job_id: str, **kwargs: Any) -> None:
    """
    Convenience function for structured telemetry emission.
    Supported event types: 'job_finished', 'job_retried', 'job_quarantined'.
    """
    em = get_telemetry_emitter()
    if event_type == "job_finished":
        em.emit_job_finished(job_id, **kwargs)
    elif event_type == "job_retried":
        em.emit_job_retried(job_id, **kwargs)
    elif event_type == "job_quarantined":
        em.emit_job_quarantined(job_id, **kwargs)
    else:
        logger.warning("Unknown telemetry event type: %s for job %s", event_type, job_id)
