"""
Unit and integration tests for Real-Time Event Bus and FastAPI SSE stream.
Verifies:
1. PlatformEvent model validation and SSE framing.
2. EventBus async and sync publication.
3. Multiple subscriber broadcast and slow-subscriber queue overflow handling.
4. History ring-buffer filtering (level, category, entity_id, limit).
5. FastAPI /api/events/stream SSE streaming responses.
6. FastAPI /api/events/history and /api/events/publish endpoints.
"""

import asyncio
import pytest
from fastapi.testclient import TestClient

from shorts_engine.services.events.models import PlatformEvent
from shorts_engine.services.events.bus import EventBus, global_event_bus
from shorts_engine.dashboard.app import app
from shorts_engine.dashboard.auth import require_operator, require_viewer
from shorts_engine.db.models import User, UserRole


@pytest.fixture(autouse=True)
def override_auth():
    fake_user = User(id="test_admin", username="admin", role=UserRole.ADMIN, is_active=True)
    app.dependency_overrides[require_operator] = lambda: fake_user
    app.dependency_overrides[require_viewer] = lambda: fake_user
    yield
    app.dependency_overrides.pop(require_operator, None)
    app.dependency_overrides.pop(require_viewer, None)


@pytest.fixture(autouse=True)
def clean_event_bus():
    """Ensure global event bus is clean for every test."""
    global_event_bus.clear()
    yield
    global_event_bus.clear()


def test_platform_event_model():
    """Verifies PlatformEvent validation and SSE serialization."""
    event = PlatformEvent(
        category="rendering",
        level="SUCCESS",
        title="Clip Render Completed",
        message="Rendered clip #123 successfully.",
        stage_progress_pct=100.0,
        entity_id="clip-123",
        entity_type="clip",
        action_url="/queue?clip=clip-123",
    )

    assert event.id is not None
    assert event.category == "rendering"
    assert event.level == "SUCCESS"
    assert event.stage_progress_pct == 100.0

    sse_msg = event.to_sse_message()
    assert f"id: {event.id}" in sse_msg
    assert "event: message" in sse_msg
    assert "data: {" in sse_msg
    assert '"entity_id":"clip-123"' in sse_msg or '"entity_id": "clip-123"' in sse_msg


def test_event_bus_async_publish_subscribe():
    """Verifies asynchronous pub/sub delivery across subscribers."""
    async def _run():
        bus = EventBus(history_size=10)
        q1 = bus.subscribe()
        q2 = bus.subscribe()

        assert bus.subscriber_count == 2

        event = PlatformEvent(
            category="ingestion",
            level="INFO",
            title="Download Started",
            message="yt-dlp started downloading source video.",
            entity_id="vid-999",
        )

        await bus.publish(event)

        ev1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        ev2 = await asyncio.wait_for(q2.get(), timeout=1.0)

        assert ev1.id == event.id
        assert ev2.id == event.id

        bus.unsubscribe(q1)
        assert bus.subscriber_count == 1
        bus.unsubscribe(q2)
        assert bus.subscriber_count == 0

    asyncio.run(_run())


def test_event_bus_sync_publish_from_thread():
    """Verifies synchronous thread publish updates history and subscriber queues."""
    bus = EventBus(history_size=10)
    q = bus.subscribe()

    event = PlatformEvent(
        category="transcription",
        level="INFO",
        title="Whisper Chunks",
        message="Transcribed 45 seconds.",
    )

    bus.publish_sync(event)

    assert len(bus.get_history()) == 1
    assert bus.get_history()[0].title == "Whisper Chunks"

    # Queue should have received the event synchronously
    item = q.get_nowait()
    assert item.id == event.id


def test_event_bus_queue_overflow_eviction():
    """Verifies that slow/blocked subscriber queues do not stall other subscribers."""
    async def _run():
        bus = EventBus(history_size=10)
        # Small queue that will overflow
        slow_q = bus.subscribe(max_queue_size=2)
        fast_q = bus.subscribe(max_queue_size=100)

        # Fill slow_q to capacity
        for i in range(2):
            await bus.publish(
                PlatformEvent(
                    category="system",
                    level="INFO",
                    title=f"Msg {i}",
                    message="test",
                )
            )

        assert bus.subscriber_count == 2

        # Third message should overflow slow_q and trigger eviction
        await bus.publish(
            PlatformEvent(
                category="system",
                level="WARNING",
                title="Overflow Msg",
                message="test",
            )
        )

        # slow_q should be evicted from subscriber set
        assert bus.subscriber_count == 1
        assert slow_q not in bus._subscribers
        assert fast_q in bus._subscribers

    asyncio.run(_run())


def test_event_bus_history_filtering():
    """Verifies history query filters by limit, level, and category."""
    bus = EventBus(history_size=50)

    for i in range(5):
        bus.publish_sync(PlatformEvent(category="rendering", level="INFO", title=f"R-{i}", message="ok"))
    for i in range(3):
        bus.publish_sync(PlatformEvent(category="quality_gate", level="ERROR", title=f"Q-{i}", message="bad"))
    for i in range(2):
        bus.publish_sync(PlatformEvent(category="publishing", level="SUCCESS", title=f"P-{i}", message="pub"))

    assert len(bus.get_history(limit=50)) == 10
    assert len(bus.get_history(limit=4)) == 4

    error_events = bus.get_history(level="ERROR")
    assert len(error_events) == 3
    assert all(e.level == "ERROR" for e in error_events)

    render_events = bus.get_history(category="rendering")
    assert len(render_events) == 5
    assert all(e.category == "rendering" for e in render_events)


def test_api_events_history_and_publish_endpoints():
    """Verifies GET /api/events/history and POST /api/events/publish."""
    client = TestClient(app)

    # Publish an event via POST API
    payload = {
        "category": "quota",
        "level": "WARNING",
        "title": "API Key Rate Limited",
        "message": "Gemini key cooldown triggered.",
        "entity_id": "key-001",
        "entity_type": "key",
    }
    post_res = client.post("/api/events/publish", json=payload)
    assert post_res.status_code == 200
    res_data = post_res.json()
    assert res_data["status"] == "published"
    assert "id" in res_data

    # Query history via GET API
    get_res = client.get("/api/events/history?category=quota")
    assert get_res.status_code == 200
    history = get_res.json()
    assert len(history) >= 1
    assert history[-1]["title"] == "API Key Rate Limited"
    assert history[-1]["category"] == "quota"
    assert history[-1]["level"] == "WARNING"


def test_api_events_stream_initial_greeting():
    """Verifies GET /api/events/stream returns text/event-stream with initial connected event."""
    client = TestClient(app)

    with client.stream("GET", "/api/events/stream?max_ticks=1") as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        assert "no-cache" in response.headers["cache-control"]

        lines = []
        for line in response.iter_lines():
            if line:
                lines.append(line)

        full_output = "\n".join(lines)
        assert "event: ping" in full_output
        assert "connected" in full_output


def test_stage_event_emission_transcription():
    """Verifies that transcription stage events are properly published and tracked."""
    global_event_bus.clear()
    
    # Simulate transcription events
    global_event_bus.publish_sync(PlatformEvent(
        category="transcription",
        level="INFO",
        title="Transcription Started",
        message="Extracting audio and starting Whisper...",
        stage_progress_pct=5.0,
        entity_id="test_vid_1",
        entity_type="video",
    ))
    global_event_bus.publish_sync(PlatformEvent(
        category="transcription",
        level="SUCCESS",
        title="Transcription Completed",
        message="Transcribed 42 words.",
        stage_progress_pct=100.0,
        entity_id="test_vid_1",
        entity_type="video",
    ))

    events = global_event_bus.get_history(category="transcription", entity_id="test_vid_1")
    assert len(events) == 2
    assert events[0].title == "Transcription Started"
    assert events[0].stage_progress_pct == 5.0
    assert events[1].title == "Transcription Completed"
    assert events[1].level == "SUCCESS"
    assert events[1].stage_progress_pct == 100.0


def test_stage_event_emission_rendering():
    """Verifies that rendering stage events are properly published and tracked."""
    global_event_bus.clear()

    global_event_bus.publish_sync(PlatformEvent(
        category="rendering",
        level="INFO",
        title="FFmpeg Encoding Started",
        message="Encoding clip_abc (30.0s) with libx264...",
        stage_progress_pct=10.0,
        entity_id="clip_abc",
        entity_type="clip",
    ))
    global_event_bus.publish_sync(PlatformEvent(
        category="rendering",
        level="SUCCESS",
        title="FFmpeg Encoding Completed",
        message="Master render completed.",
        stage_progress_pct=100.0,
        entity_id="clip_abc",
        entity_type="clip",
    ))

    events = global_event_bus.get_history(category="rendering", entity_id="clip_abc")
    assert len(events) == 2
    assert events[0].title == "FFmpeg Encoding Started"
    assert events[1].title == "FFmpeg Encoding Completed"
    assert events[1].level == "SUCCESS"


def test_stage_event_emission_publishing():
    """Verifies that publishing stage events are properly published and tracked."""
    global_event_bus.clear()

    global_event_bus.publish_sync(PlatformEvent(
        category="publishing",
        level="INFO",
        title="Publishing Clip Started",
        message="Starting YouTube upload for clip clip_xyz...",
        stage_progress_pct=20.0,
        entity_id="clip_xyz",
        entity_type="clip",
    ))
    global_event_bus.publish_sync(PlatformEvent(
        category="publishing",
        level="SUCCESS",
        title="Clip Published",
        message="Clip clip_xyz successfully published! YouTube ID: yt_12345",
        stage_progress_pct=100.0,
        entity_id="clip_xyz",
        entity_type="clip",
        action_url="https://youtube.com/shorts/yt_12345",
    ))

    events = global_event_bus.get_history(category="publishing", entity_id="clip_xyz")
    assert len(events) == 2
    assert events[0].title == "Publishing Clip Started"
    assert events[0].stage_progress_pct == 20.0
    assert events[1].title == "Clip Published"
    assert events[1].level == "SUCCESS"
    assert events[1].action_url == "https://youtube.com/shorts/yt_12345"
