"""
Comprehensive Acceptance Tests for Event Bus Activation, Taxonomy, and Telemetry.

Validates:
1. Minimum Event Taxonomy (8 events):
   - stage.finished
   - stage.failed
   - stage.quarantined
   - video.ingested
   - clip.approved
   - clip.published
   - channel.health
   - quota.low
2. Database persistence: Exactly 1 row in `event_logs` per emitted event with full payload.
3. Retention limit: 7-day pruning removes old logs and preserves recent logs.
4. Catch-up consumer: /api/events/recent enables polling and catch-up after disconnection.
5. End-to-end integration:
   - Watch folder ingestion emits `video.ingested`.
   - Stage transitions emit `stage.finished`, `stage.failed`, `stage.quarantined`.
   - Review approval emits `clip.approved`.
   - Publish worker emits `clip.published`, `quota.low`, `channel.health`.
   - SSE streaming (/api/events/stream) delivers live events without page refresh.
"""

import json
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    EventLog,
    prune_old_event_logs,
    SourceVideo,
    ClipCandidate,
    Clip,
    ClipStatus,
    Channel,
    ChannelCredential,
    PipelineJob,
)
from shorts_engine.services.events.bus import global_event_bus
from shorts_engine.services.events.taxonomy import (
    emit_platform_event,
    ALL_TAXONOMY_EVENTS,
    STAGE_FINISHED,
    STAGE_FAILED,
    STAGE_QUARANTINED,
    VIDEO_INGESTED,
    CLIP_APPROVED,
    CLIP_PUBLISHED,
    CHANNEL_HEALTH,
    QUOTA_LOW,
)
from shorts_engine.orchestration.telemetry import EventBusEmitter
from shorts_engine.dashboard.app import app
from shorts_engine.dashboard.routes.events import get_db
from shorts_engine.dashboard.routes.queue import get_db as get_queue_db
from shorts_engine.db.security import encrypt_secret


@pytest.fixture
def db_session(tmp_path):
    """Sets up an isolated SQLite database session."""
    db_file = tmp_path / "test_events.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    yield session

    session.close()
    engine.dispose()


@pytest.fixture
def api_client(db_session):
    """TestClient wired with database override and clean event bus."""
    from shorts_engine.dashboard.auth import require_operator, require_viewer, verify_csrf
    from shorts_engine.db.models import User, UserRole

    fake_operator = User(
        id="user-op-1",
        username="operator1",
        role=UserRole.OPERATOR,
        password_hash="hash",
    )

    def _get_test_db():
        yield db_session

    app.dependency_overrides[get_db] = _get_test_db
    app.dependency_overrides[get_queue_db] = _get_test_db
    app.dependency_overrides[require_operator] = lambda: fake_operator
    app.dependency_overrides[require_viewer] = lambda: fake_operator
    app.dependency_overrides[verify_csrf] = lambda: True
    client = TestClient(app)

    yield client

    app.dependency_overrides.clear()
    global_event_bus.clear()


def _create_test_clip_hierarchy(db, channel_yt_id="UC_EVENTS_CHAN", clip_id="clip_events_1"):
    """Helper creating full Channel -> SourceVideo -> ClipCandidate -> Clip hierarchy."""
    chan = Channel(
        id=f"chan_{clip_id}",
        youtube_channel_id=channel_yt_id,
        name="Events Test Channel",
        title="Events Test Channel",
    )
    db.add(chan)
    db.flush()

    cred = ChannelCredential(
        id=f"cred_{clip_id}",
        channel_id=chan.id,
        encrypted_refresh_token=encrypt_secret("test_refresh_token_xyz"),
        status="active",
    )
    db.add(cred)
    db.flush()

    vid = SourceVideo(
        id=f"vid_{clip_id}",
        channel_id=chan.id,
        youtube_video_id=f"yt_{clip_id}",
        title="Test Ingested Video",
    )
    db.add(vid)
    db.flush()

    cand = ClipCandidate(
        id=f"cand_{clip_id}",
        source_video_id=vid.id,
        start_ms=0,
        end_ms=25000,
        score=0.95,
        reason="Viral moment",
        hook_text="Check this out",
        selected=True,
    )
    db.add(cand)
    db.flush()

    clip = Clip(
        id=clip_id,
        clip_candidate_id=cand.id,
        rendered_path=None,
        status=ClipStatus.WAITING_APPROVAL,
        generated_title="Test Clip Generated Title",
    )
    db.add(clip)
    db.commit()
    return chan, vid, cand, clip


# ==============================================================================
# 1. Event Taxonomy & Persistence Guarantee Tests
# ==============================================================================


def test_event_taxonomy_complete_set():
    """Verifies that all 8 minimum taxonomy events are defined and accounted for."""
    expected = {
        "stage.finished",
        "stage.failed",
        "stage.quarantined",
        "video.ingested",
        "clip.approved",
        "clip.published",
        "channel.health",
        "quota.low",
        "budget.exceeded",
        "storage.cleanup",
    }
    assert set(ALL_TAXONOMY_EVENTS) == expected


def test_emit_platform_event_persists_exactly_one_row_per_event(db_session):
    """
    Acceptance Criterion:
    The event-log table contains one row per emitted event.
    """
    initial_count = db_session.query(EventLog).count()
    assert initial_count == 0

    emitted_entities = []
    for idx, event_name in enumerate(ALL_TAXONOMY_EVENTS):
        entity_id = f"entity-{idx}"
        payload = {"index": idx, "test_marker": True, "event": event_name}

        log_row = emit_platform_event(
            event_name=event_name,
            payload=payload,
            entity_id=entity_id,
            entity_type="test_entity",
            level="INFO",
            title=f"Test {event_name}",
            message=f"Telemetry test for {event_name}",
            db=db_session,
        )

        assert log_row is not None
        assert log_row.event_name == event_name
        assert log_row.entity_id == entity_id
        assert log_row.payload == payload
        emitted_entities.append((event_name, entity_id))

    # Exactly 8 rows in event_logs table
    rows = db_session.query(EventLog).all()
    assert len(rows) == len(ALL_TAXONOMY_EVENTS)

    # Validate row by row
    for event_name, entity_id in emitted_entities:
        match = db_session.query(EventLog).filter_by(event_name=event_name, entity_id=entity_id).first()
        assert match is not None
        assert match.payload.get("test_marker") is True
        assert match.payload.get("event") == event_name


# ==============================================================================
# 2. Retention Pruning Tests (7-Day Limit)
# ==============================================================================


def test_event_log_7_day_retention_pruning(db_session):
    """
    Acceptance Criterion:
    Persist events to an event-log table with a retention limit (7 days).
    """
    now = datetime.utcnow()

    # Create 3 old events (older than 7 days)
    old_dates = [now - timedelta(days=8), now - timedelta(days=15), now - timedelta(days=30)]
    for idx, dt in enumerate(old_dates):
        old_log = EventLog(
            event_name="stage.finished",
            entity_id=f"old-{idx}",
            entity_type="job",
            payload_json=json.dumps({"old": True, "idx": idx}),
            created_at=dt,
        )
        db_session.add(old_log)

    # Create 3 recent events (within 7 days)
    recent_dates = [now - timedelta(days=6), now - timedelta(days=1), now]
    for idx, dt in enumerate(recent_dates):
        recent_log = EventLog(
            event_name="video.ingested",
            entity_id=f"recent-{idx}",
            entity_type="video",
            payload_json=json.dumps({"recent": True, "idx": idx}),
            created_at=dt,
        )
        db_session.add(recent_log)

    db_session.commit()
    assert db_session.query(EventLog).count() == 6

    # Prune with 7-day retention limit
    pruned_count = prune_old_event_logs(db=db_session, retention_days=7)
    assert pruned_count == 3

    # Verify only recent records remain
    remaining = db_session.query(EventLog).all()
    assert len(remaining) == 3
    for r in remaining:
        assert r.payload.get("recent") is True


# ==============================================================================
# 3. Catch-Up Consumer Endpoints (/api/events/recent & /api/events/prune)
# ==============================================================================


def test_api_events_recent_polling_and_catchup(api_client, db_session):
    """
    Acceptance Criterion:
    A dashboard endpoint streaming recent events or 2-second polling allows catching up.
    """
    event1 = emit_platform_event(
        event_name=VIDEO_INGESTED,
        payload={"step": 1, "video_id": "v-1"},
        entity_id="v-1",
        entity_type="video",
        db=db_session,
    )
    event2 = emit_platform_event(
        event_name=STAGE_FINISHED,
        payload={"step": 2, "stage": "transcribe"},
        entity_id="v-1",
        entity_type="stage",
        db=db_session,
    )
    event3 = emit_platform_event(
        event_name=CLIP_APPROVED,
        payload={"step": 3, "clip_id": "c-1"},
        entity_id="c-1",
        entity_type="clip",
        db=db_session,
    )

    from datetime import datetime, timedelta
    t0 = datetime.utcnow()
    event1.created_at = t0
    event2.created_at = t0 + timedelta(seconds=1)
    event3.created_at = t0 + timedelta(seconds=2)
    db_session.commit()

    # 1. Base polling returns all recent events
    res = api_client.get("/api/events/recent?limit=10")
    assert res.status_code == 200
    events = res.json()
    assert len(events) == 3
    assert events[0]["event_name"] == VIDEO_INGESTED
    assert events[1]["event_name"] == STAGE_FINISHED
    assert events[2]["event_name"] == CLIP_APPROVED

    # 2. Catch-up using since_id (fetch events occurring after event1)
    res_since = api_client.get(f"/api/events/recent?since_id={event1.id}")
    assert res_since.status_code == 200
    catchup_events = res_since.json()
    assert len(catchup_events) == 2
    assert catchup_events[0]["event_name"] == STAGE_FINISHED
    assert catchup_events[1]["event_name"] == CLIP_APPROVED

    # 3. Filter by event_name
    res_filter = api_client.get(f"/api/events/recent?event_name={CLIP_APPROVED}")
    assert res_filter.status_code == 200
    filtered = res_filter.json()
    assert len(filtered) == 1
    assert filtered[0]["entity_id"] == "c-1"

    # 4. Pruning endpoint
    prune_res = api_client.post("/api/events/prune?retention_days=7")
    assert prune_res.status_code == 200
    assert prune_res.json()["status"] == "ok"


# ==============================================================================
# 4. Pipeline Emitter Wiring Tests
# ==============================================================================


def test_queue_telemetry_emitters_record_to_database_and_bus(db_session):
    """
    Verifies that EventBusEmitter transforms job finish, retry, and quarantine
    into stage.finished, stage.failed, and stage.quarantined, writing to db and bus.
    """
    bus_emitter = EventBusEmitter()
    q = global_event_bus.subscribe()

    try:
        # Job Finished -> stage.finished
        bus_emitter.emit_job_finished(
            job_id="job-101",
            stage="transcribe",
            duration_sec=3.5,
            extra={"subject_id": "vid-1"},
            db=db_session,
        )

        # Job Retried -> stage.failed
        bus_emitter.emit_job_retried(
            job_id="job-102",
            attempt_count=1,
            next_run_at=datetime.utcnow() + timedelta(seconds=5),
            error="TransientError: connection reset",
            stage="reframe",
            extra={"subject_id": "clip-2"},
            db=db_session,
        )

        # Job Quarantined -> stage.quarantined
        bus_emitter.emit_job_quarantined(
            job_id="job-103",
            last_error="CorruptVideoError: moov atom missing",
            stage="render",
            attempt_count=3,
            extra={"subject_id": "clip-3"},
            db=db_session,
        )

        # Check EventLog rows
        rows = db_session.query(EventLog).order_by(EventLog.created_at.asc()).all()
        assert len(rows) == 3
        assert rows[0].event_name == STAGE_FINISHED
        assert rows[0].payload["job_id"] == "job-101"
        assert rows[0].payload["stage"] == "transcribe"

        assert rows[1].event_name == STAGE_FAILED
        assert rows[1].payload["job_id"] == "job-102"
        assert rows[1].payload["stage"] == "reframe"

        assert rows[2].event_name == STAGE_QUARANTINED
        assert rows[2].payload["job_id"] == "job-103"
        assert rows[2].payload["stage"] == "render"

        # Check bus queue received all 3
        e1 = q.get_nowait()
        assert e1.event_name == STAGE_FINISHED
        e2 = q.get_nowait()
        assert e2.event_name == STAGE_FAILED
        e3 = q.get_nowait()
        assert e3.event_name == STAGE_QUARANTINED

    finally:
        global_event_bus.unsubscribe(q)


def test_watch_folder_ingestion_emits_video_ingested(tmp_path, db_session):
    """
    Acceptance Criterion:
    Dropping a file into the watch folder produces visible stage transitions
    in the dashboard (video.ingested).
    """
    from shorts_engine.services.ingestion.watch_folder import WatchFolderDaemon

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()

    # Create dummy default channel so ingestion can associate video
    chan = Channel(
        id="chan_default",
        youtube_channel_id="UC_WATCH_DEFAULT",
        name="Default Channel",
        title="Default Channel",
    )
    db_session.add(chan)
    db_session.commit()

    daemon = WatchFolderDaemon(
        watch_dir=incoming_dir,
        stable_time_sec=0.01,
        auto_ingest_db=True,
        session_factory=lambda: db_session,
    )

    test_file = incoming_dir / "sample_test_drop.mp4"
    test_file.write_bytes(b"Simulated MP4 Header Content for Telemetry Acceptance")

    daemon.scan_directory_candidates()
    daemon.poll_and_process_stable_files()  # First poll establishes initial baseline size
    time.sleep(0.02)
    processed = daemon.poll_and_process_stable_files()  # Second poll verifies stability
    assert len(processed) == 1

    # Verify video.ingested event was logged to database
    ingested_event = db_session.query(EventLog).filter_by(
        event_name=VIDEO_INGESTED,
    ).first()

    assert ingested_event is not None
    assert "sample_test_drop" in ingested_event.payload.get("title", "")
    assert ingested_event.payload.get("source_type") == "watch_folder"

    # Verify pipeline job is enqueued
    job = db_session.query(PipelineJob).filter_by(stage="ingest").first()
    assert job is not None


def test_review_approval_emits_clip_approved(tmp_path, api_client, db_session, monkeypatch):
    """
    Acceptance Criterion:
    Review approval emits clip.approved to the event bus and event log.
    """
    chan, vid, cand, clip = _create_test_clip_hierarchy(
        db=db_session,
        clip_id="clip_review_approved_1",
    )
    dummy_video = tmp_path / "rendered_dummy.mp4"
    dummy_video.write_bytes(b"\x00" * 1024)
    clip.rendered_path = str(dummy_video)
    db_session.commit()
    monkeypatch.setattr("shorts_engine.config.settings.settings.DRY_RUN_PUBLISHING", True)

    # Call approval route
    response = api_client.post(
        f"/queue/{clip.id}/approve",
        data={"auto_publish": "false"},
        follow_redirects=False,
    )
    assert response.status_code in (200, 202, 303)

    # Verify clip.approved was persisted to event_logs
    event = db_session.query(EventLog).filter_by(
        event_name=CLIP_APPROVED,
        entity_id=clip.id,
    ).first()

    assert event is not None
    assert event.payload.get("clip_id") == clip.id
    assert event.payload.get("title") == "Test Clip Generated Title"


def test_publish_worker_emits_clip_published_and_channel_health(tmp_path, db_session, monkeypatch):
    """
    Acceptance Criterion:
    Publish worker emits clip.published and channel.health with full payload.
    """
    from shorts_engine.services.publishing.worker import YouTubePublishWorker
    from shorts_engine.services.publishing.slots import publish_slot_service

    monkeypatch.setenv("ALLOW_IN_PROCESS_PREVIEW", "1")

    chan, vid, cand, clip = _create_test_clip_hierarchy(
        db=db_session,
        clip_id="clip_worker_pub_1",
    )
    dummy_video = tmp_path / "rendered_dummy.mp4"
    dummy_video.write_bytes(b"\x00" * 1024)
    clip.rendered_path = str(dummy_video)
    clip.status = ClipStatus.APPROVED
    slot = publish_slot_service.allocate_slot(channel_id=chan.id, clip_id=clip.id, db=db_session)
    db_session.commit()

    worker = YouTubePublishWorker(dry_run=True)

    # 1. Execute Publish Intent (dry run)
    result = worker.execute_publish_intent(
        clip_id=clip.id,
        payload={"dry_run": True, "slot_id": slot.id},
        db=db_session,
    )
    assert result.get("success") is True

    pub_event = db_session.query(EventLog).filter_by(
        event_name=CLIP_PUBLISHED,
        entity_id=clip.id,
    ).first()

    assert pub_event is not None
    assert pub_event.payload.get("clip_id") == clip.id
    assert pub_event.payload.get("dry_run") is True

    # 2. Execute Daily Health Intent (dry run)
    health_result = worker.execute_daily_health_intent(
        channel_id=chan.id,
        payload={"dry_run": True},
        db=db_session,
    )
    assert health_result.get("healthy") is True

    health_event = db_session.query(EventLog).filter_by(
        event_name=CHANNEL_HEALTH,
        entity_id=chan.id,
    ).first()

    assert health_event is not None
    assert health_event.payload.get("healthy") is True
    assert health_event.payload.get("channel_id") == chan.id


def test_publish_worker_emits_quota_low_on_quota_exceeded(db_session):
    """
    Acceptance Criterion:
    Simulated 403 quotaExceeded emits quota.low event.
    """
    from shorts_engine.services.publishing.worker import YouTubePublishWorker

    worker = YouTubePublishWorker()
    job = PipelineJob(
        id="job-quota-test",
        job_type="publish",
        entity_type="clip",
        entity_id="clip-quota-1",
        stage="publish",
        status="RUNNING",
    )
    db_session.add(job)
    db_session.commit()

    worker._handle_quota_exceeded(
        job=job,
        db=db_session,
        err_msg="The user has exceeded the uploadLimitExceeded quota limit.",
    )

    quota_event = db_session.query(EventLog).filter_by(
        event_name=QUOTA_LOW,
        entity_id="job-quota-test",
    ).first()

    assert quota_event is not None
    assert quota_event.payload.get("job_id") == "job-quota-test"
    assert "uploadLimitExceeded" in quota_event.payload.get("error", "")


# ==============================================================================
# 5. Live SSE Streaming Consumer Loop
# ==============================================================================


def test_sse_stream_receives_live_events_without_page_refresh(api_client, db_session):
    """
    Acceptance Criterion:
    SSE stream (/api/events/stream) delivers live event messages without page refresh.
    """
    def _emit_delayed():
        time.sleep(0.1)
        emit_platform_event(
            event_name=STAGE_FINISHED,
            payload={"video_id": "vid-live-1", "stage": "render"},
            entity_id="vid-live-1",
            entity_type="video",
            title="Video Rendered",
            message="Rendering completed live.",
            db=db_session,
        )

    t = threading.Thread(target=_emit_delayed, daemon=True)
    t.start()

    # Open SSE stream with max_ticks=2 (tick 1 is ping greeting, tick 2 is emitted event)
    with api_client.stream("GET", "/api/events/stream?max_ticks=2") as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

        lines = [line for line in response.iter_lines() if line]
        assert len(lines) >= 2
        assert any("status" in line and "connected" in line for line in lines)
        assert any("stage.finished" in line or "Video Rendered" in line for line in lines)

    t.join(timeout=2.0)
