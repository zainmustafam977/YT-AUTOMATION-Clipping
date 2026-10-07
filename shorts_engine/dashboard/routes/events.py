"""
Server-Sent Events (SSE) and Event Telemetry API Routes.
Provides real-time streaming endpoint /api/events/stream, historical query endpoint,
and manual publication interface.
"""

import asyncio
import logging
from typing import Optional, List
from fastapi import APIRouter, Request, Query
from starlette.responses import StreamingResponse

from shorts_engine.services.events.bus import global_event_bus
from shorts_engine.services.events.models import PlatformEvent

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/api/events/stream",
    summary="Real-time Server-Sent Events (SSE) stream for live UI activity bubbles and progress updates",
)
async def event_stream(request: Request, max_ticks: Optional[int] = None):
    """
    Subscribes the client to live platform events.
    Yields events in standard text/event-stream format with automatic 15-second heartbeat pings.
    Supports max_ticks for testing and controlled streaming.
    """
    async def event_generator():
        q = global_event_bus.subscribe()
        ticks = 0
        try:
            # Send initial connected greeting ping
            yield "event: ping\ndata: {\"status\": \"connected\"}\n\n"
            ticks += 1
            if max_ticks and ticks >= max_ticks:
                return

            while True:
                if await request.is_disconnected():
                    break

                try:
                    event: PlatformEvent = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield event.to_sse_message()
                    ticks += 1
                    if max_ticks and ticks >= max_ticks:
                        break
                except asyncio.TimeoutError:
                    # Keep-alive heartbeat ping to prevent proxy/browser timeout
                    yield "event: ping\ndata: {}\n\n"
                    ticks += 1
                    if max_ticks and ticks >= max_ticks:
                        break
                except Exception as e:
                    logger.debug("Error in event stream generator: %s", e)
                    break
        finally:
            global_event_bus.unsubscribe(q)

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=headers,
    )


@router.get(
    "/api/events/history",
    response_model=List[PlatformEvent],
    summary="Get recent platform events from the in-memory telemetry buffer",
)
def get_event_history(
    limit: int = Query(default=50, ge=1, le=200),
    level: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    entity_id: Optional[str] = Query(default=None),
):
    """Returns a slice of recent events matching optional filter criteria."""
    return global_event_bus.get_history(
        limit=limit,
        level=level,
        category=category,
        entity_id=entity_id,
    )


@router.post(
    "/api/events/publish",
    summary="Publish a platform event to the event bus",
)
async def publish_event(event: PlatformEvent):
    """Accepts and broadcasts an event to all active SSE subscribers."""
    await global_event_bus.publish(event)
    return {"status": "published", "id": event.id}
