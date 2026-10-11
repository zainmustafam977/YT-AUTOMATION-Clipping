"""
Orchestration package exports.
"""

from shorts_engine.orchestration.exceptions import (
    PipelineError,
    TransientError,
    PermanentError,
    classify_exception,
    NetworkTransientError,
    RateLimitTransientError,
    ComputeTransientError,
    AuthPermanentError,
    MediaPermanentError,
    QuotaPermanentError,
    PolicyPermanentError,
)
from shorts_engine.orchestration.stage_registry import (
    Stage,
    StageContext,
    StageExecutor,
    PIPELINE,
    calculate_backoff_run_at,
    get_quarantined_jobs,
)
from shorts_engine.orchestration.reaper import (
    reap_stale_jobs,
    worker_heartbeat,
    ReaperDaemon,
)
from shorts_engine.orchestration.telemetry import (
    TelemetryEmitter,
    StructuredLogEmitter,
    EventBusEmitter,
    CompositeEmitter,
    get_telemetry_emitter,
    set_telemetry_emitter,
    emit,
)
from shorts_engine.orchestration.runner import PipelineRunner

__all__ = [
    "PipelineError",
    "TransientError",
    "PermanentError",
    "classify_exception",
    "NetworkTransientError",
    "RateLimitTransientError",
    "ComputeTransientError",
    "AuthPermanentError",
    "MediaPermanentError",
    "QuotaPermanentError",
    "PolicyPermanentError",
    "Stage",
    "StageContext",
    "StageExecutor",
    "PIPELINE",
    "calculate_backoff_run_at",
    "get_quarantined_jobs",
    "reap_stale_jobs",
    "worker_heartbeat",
    "ReaperDaemon",
    "TelemetryEmitter",
    "StructuredLogEmitter",
    "EventBusEmitter",
    "CompositeEmitter",
    "get_telemetry_emitter",
    "set_telemetry_emitter",
    "emit",
    "PipelineRunner",
]
