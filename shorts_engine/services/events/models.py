"""
Event Telemetry & Notification Data Models.
Standardized event payloads for the real-time SSE event bus, structured logging,
and live UI toast notification system.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class PlatformEvent(BaseModel):
    """
    Structured platform event broadcast over SSE to the web dashboard,
    persisted in the database event_logs table, and retained in the ring buffer.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_name: Optional[str] = None            # e.g. "stage.finished", "video.ingested"
    category: str = "system"                    # e.g. "pipeline", "ingestion", "publishing", etc.
    level: str = "INFO"                         # "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"
    title: str
    message: str
    stage_progress_pct: Optional[float] = None  # 0.0 - 100.0 for progress bars
    entity_id: Optional[str] = None             # video_id or clip_id
    entity_type: Optional[str] = None           # "video", "clip", "channel", "job", "key"
    action_url: Optional[str] = None            # Clickable deep-link (e.g. "/queue?clip=...")
    details: Optional[Dict[str, Any]] = None    # Diagnostics, metadata, metrics

    def to_sse_data(self) -> str:
        """Serializes event to clean JSON string for SSE data field."""
        return self.model_dump_json()

    def to_sse_message(self) -> str:
        """Formats event as an RFC-compliant Server-Sent Events (SSE) message chunk."""
        return f"id: {self.id}\nevent: message\ndata: {self.model_dump_json()}\n\n"

