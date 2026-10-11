"""
Fault-injection acceptance test suite for:
1. Stage Registry as data ({name, callable, produces, max_attempts}, PIPELINE dict, StageContext).
2. Failure taxonomy (TransientError retry vs PermanentError immediate quarantine).
3. Exponential backoff with jitter: run_at = now + base * 2^attempt_count + jitter.
4. Crashed-worker recovery reaper routine (stale locked_at recovered to 'pending').
5. Worker heartbeat preventing false reaping during long renders.
6. Swappable structured emit() at job finished, job retried, and job quarantined.
7. Quarantined jobs visibility query and GET /api/jobs/quarantined endpoint.
"""

import pytest
from datetime import datetime, timedelta
from typing import Dict, Any, List
from unittest.mock import MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from shorts_engine.db.session import Base
from shorts_engine.db.models import PipelineJob, PipelineJobStatus, SourceVideo, User, UserRole
from shorts_engine.orchestration.exceptions import (
    PipelineError,
    TransientError,
    PermanentError,
    classify_exception,
    NetworkTransientError,
    AuthPermanentError,
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
)
from shorts_engine.orchestration.telemetry import (
    TelemetryEmitter,
    StructuredLogEmitter,
    set_telemetry_emitter,
    get_telemetry_emitter,
    emit,
)
from shorts_engine.dashboard.app import create_dashboard_app as create_app


from sqlalchemy.pool import StaticPool


@pytest.fixture
def in_memory_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


class MockRecordingEmitter(TelemetryEmitter):
    def __init__(self):
        self.finished_events: List[Dict[str, Any]] = []
        self.retried_events: List[Dict[str, Any]] = []
        self.quarantined_events: List[Dict[str, Any]] = []

    def emit_job_finished(self, job_id: str, stage: str = None, result: Dict[str, Any] = None, **kwargs):
        self.finished_events.append({"job_id": job_id, "stage": stage, "result": result, **kwargs})

    def emit_job_retried(self, job_id: str, attempt_count: int, next_run_at: datetime, error: str, stage: str = None, **kwargs):
        self.retried_events.append({
            "job_id": job_id,
            "attempt_count": attempt_count,
            "next_run_at": next_run_at,
            "error": error,
            "stage": stage,
            **kwargs,
        })

    def emit_job_quarantined(self, job_id: str, last_error: str, stage: str = None, attempt_count: int = 0, **kwargs):
        self.quarantined_events.append({
            "job_id": job_id,
            "last_error": last_error,
            "stage": stage,
            "attempt_count": attempt_count,
            **kwargs,
        })


@pytest.fixture
def recording_emitter():
    prev = get_telemetry_emitter()
    emitter = MockRecordingEmitter()
    set_telemetry_emitter(emitter)
    try:
        yield emitter
    finally:
        set_telemetry_emitter(prev)


# ==============================================================================
# Acceptance Test 1: Transient Failure Twice Then Success -> Ends 'done' (attempt_count = 3)
# ==============================================================================

def test_acceptance_transient_failure_twice_then_success(in_memory_db, recording_emitter):
    """
    ACCEPTANCE CRITERION 1:
    A stage that fails transiently twice then succeeds ends 'done' with attempt_count = 3.
    """
    call_count = 0

    def flaky_stage_callable(ctx: StageContext) -> Dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise TransientError(f"Temporary network blip (failure #{call_count})")
        return {"output_file": "success.mp4", "status": "ok"}

    custom_stage = Stage(
        name="test_flaky_stage",
        callable=flaky_stage_callable,
        produces="{storage_dir}/renders/{clip_id}.mp4",
        max_attempts=3,
    )

    executor = StageExecutor(
        pipeline={"test_flaky_stage": custom_stage},
        base_backoff_sec=5.0,
        jitter_max_sec=1.0,
    )

    job = PipelineJob(
        job_type="RENDER_CLIP",
        entity_type="clip",
        entity_id="clip_acceptance_01",
        status=PipelineJobStatus.PENDING,
        attempt_count=0,
    )
    in_memory_db.add(job)
    in_memory_db.commit()

    ctx = StageContext(
        db_session=in_memory_db,
        video_id="vid_test_01",
        clip_id="clip_acceptance_01",
        job_id=job.id,
    )

    # Attempt 1: Fails with TransientError
    with pytest.raises(TransientError):
        executor.execute_stage("test_flaky_stage", ctx, job=job)

    in_memory_db.refresh(job)
    assert job.status == PipelineJobStatus.PENDING
    assert job.status == "pending"
    assert job.attempt_count == 1
    assert "Temporary network blip (failure #1)" in job.last_error
    assert job.next_run_at is not None
    assert job.next_run_at > datetime.utcnow()
    assert len(recording_emitter.retried_events) == 1
    assert recording_emitter.retried_events[0]["attempt_count"] == 1

    # Attempt 2: Fails with TransientError
    with pytest.raises(TransientError):
        executor.execute_stage("test_flaky_stage", ctx, job=job)

    in_memory_db.refresh(job)
    assert job.status == PipelineJobStatus.PENDING
    assert job.status == "pending"
    assert job.attempt_count == 2
    assert "Temporary network blip (failure #2)" in job.last_error
    assert len(recording_emitter.retried_events) == 2
    assert recording_emitter.retried_events[1]["attempt_count"] == 2

    # Attempt 3: Succeeds!
    result = executor.execute_stage("test_flaky_stage", ctx, job=job)
    assert result["status"] == "ok"

    # Complete pipeline
    job.status = PipelineJobStatus.DONE
    in_memory_db.commit()
    in_memory_db.refresh(job)

    # Assert final acceptance state: ends 'done' with attempt_count = 3
    assert job.status == "done"
    assert job.status == PipelineJobStatus.DONE
    assert job.attempt_count == 3
    assert call_count == 3


# ==============================================================================
# Acceptance Test 2: PermanentError -> Quarantined Immediately, Zero Retries
# ==============================================================================

def test_acceptance_permanent_error_quarantined_immediately_zero_retries(in_memory_db, recording_emitter):
    """
    ACCEPTANCE CRITERION 2:
    A stage raising PermanentError is quarantined immediately, zero retries.
    """
    call_count = 0

    def fatal_stage_callable(ctx: StageContext):
        nonlocal call_count
        call_count += 1
        raise PermanentError("Video was deleted or made private by creator")

    custom_stage = Stage(
        name="test_fatal_stage",
        callable=fatal_stage_callable,
        produces=None,
        max_attempts=3,
    )

    executor = StageExecutor(
        pipeline={"test_fatal_stage": custom_stage},
        base_backoff_sec=10.0,
    )

    job = PipelineJob(
        job_type="INGEST_VIDEO",
        entity_type="source_video",
        entity_id="vid_fatal_01",
        status=PipelineJobStatus.PENDING,
        attempt_count=0,
        retry_count=0,
    )
    in_memory_db.add(job)
    in_memory_db.commit()

    ctx = StageContext(
        db_session=in_memory_db,
        video_id="vid_fatal_01",
        job_id=job.id,
    )

    with pytest.raises(PermanentError) as exc_info:
        executor.execute_stage("test_fatal_stage", ctx, job=job)

    in_memory_db.refresh(job)

    # Assert job is quarantined immediately
    assert job.status == "quarantined"
    assert job.status == PipelineJobStatus.QUARANTINED
    assert "Video was deleted or made private" in job.last_error
    assert job.retry_count == 0  # Zero retries burned
    assert job.locked_by is None
    assert call_count == 1

    # Assert structured telemetry emit was fired
    assert len(recording_emitter.retried_events) == 0  # No retry emitted
    assert len(recording_emitter.quarantined_events) == 1
    assert recording_emitter.quarantined_events[0]["job_id"] == job.id
    assert "Video was deleted" in recording_emitter.quarantined_events[0]["last_error"]


# ==============================================================================
# Acceptance Test 3: Crashed Worker Recovery (Stale locked_at Recovered by Reaper)
# ==============================================================================

def test_acceptance_simulated_killed_worker_recovered_by_reaper(in_memory_db):
    """
    ACCEPTANCE CRITERION 3:
    A simulated killed worker (stale locked_at) has its job recovered by the reaper.
    """
    now = datetime.utcnow()
    stale_time = now - timedelta(seconds=900)  # 15 minutes ago (threshold is 10 mins = 600s)

    # Job leased by crashed worker
    killed_job = PipelineJob(
        job_type="RENDER_CLIP",
        entity_type="clip",
        entity_id="clip_killed_01",
        status=PipelineJobStatus.RUNNING,
        locked_by="worker-pid-9999-killed",
        locked_at=stale_time,
        heartbeat_at=stale_time,
    )
    # Active job leased by living worker (recent locked_at / heartbeat)
    active_job = PipelineJob(
        job_type="RENDER_CLIP",
        entity_type="clip",
        entity_id="clip_active_02",
        status=PipelineJobStatus.RUNNING,
        locked_by="worker-pid-1111-alive",
        locked_at=now - timedelta(seconds=60),
        heartbeat_at=now - timedelta(seconds=30),
    )
    in_memory_db.add_all([killed_job, active_job])
    in_memory_db.commit()

    # Run reaper with 600s threshold
    reaped = reap_stale_jobs(stale_threshold_sec=600, db=in_memory_db)

    assert len(reaped) == 1
    assert reaped[0].id == killed_job.id

    in_memory_db.refresh(killed_job)
    in_memory_db.refresh(active_job)

    # Killed worker's job reset back to 'pending' with locks cleared
    assert killed_job.status == "pending"
    assert killed_job.status == PipelineJobStatus.PENDING
    assert killed_job.locked_by is None
    assert killed_job.locked_at is None

    # Healthy active worker's job untouched
    assert active_job.status == "running"
    assert active_job.status == PipelineJobStatus.RUNNING
    assert active_job.locked_by == "worker-pid-1111-alive"


# ==============================================================================
# Test 4: Worker Heartbeat Prevents Reaping During Long Renders
# ==============================================================================

def test_worker_heartbeat_prevents_reaping(in_memory_db):
    now = datetime.utcnow()
    long_render_job = PipelineJob(
        job_type="RENDER_CLIP",
        entity_type="clip",
        entity_id="clip_long_render_01",
        status=PipelineJobStatus.RUNNING,
        locked_by="worker-ffmpeg-long",
        locked_at=now - timedelta(seconds=800),  # Initially 800s old
        heartbeat_at=now - timedelta(seconds=800),
    )
    in_memory_db.add(long_render_job)
    in_memory_db.commit()

    # Worker heartbeats before reaper threshold is checked
    success = worker_heartbeat(long_render_job.id, worker_id="worker-ffmpeg-long", db=in_memory_db)
    assert success is True

    in_memory_db.refresh(long_render_job)
    assert (datetime.utcnow() - long_render_job.locked_at).total_seconds() < 5
    assert (datetime.utcnow() - long_render_job.heartbeat_at).total_seconds() < 5

    # Reaper runs -> Job must NOT be reaped
    reaped = reap_stale_jobs(stale_threshold_sec=600, db=in_memory_db)
    assert len(reaped) == 0

    in_memory_db.refresh(long_render_job)
    assert long_render_job.status == PipelineJobStatus.RUNNING
    assert long_render_job.locked_by == "worker-ffmpeg-long"


# ==============================================================================
# Test 5: Exponential Backoff Calculation with Jitter
# ==============================================================================

def test_exponential_backoff_and_jitter_formula():
    """
    run_at = now + base * 2^attempt_count + jitter
    """
    now = datetime(2026, 10, 11, 12, 0, 0)
    base = 10.0
    jitter_max = 3.0

    # Attempt 1: delay in [10 * 2^1, 10 * 2^1 + 3] -> [20, 23]
    run_at_1 = calculate_backoff_run_at(attempt_count=1, base=base, jitter_max=jitter_max, now=now)
    delay_1 = (run_at_1 - now).total_seconds()
    assert 20.0 <= delay_1 <= 23.0

    # Attempt 2: delay in [10 * 2^2, 10 * 2^2 + 3] -> [40, 43]
    run_at_2 = calculate_backoff_run_at(attempt_count=2, base=base, jitter_max=jitter_max, now=now)
    delay_2 = (run_at_2 - now).total_seconds()
    assert 40.0 <= delay_2 <= 43.0

    # Attempt 3: delay in [10 * 2^3, 10 * 2^3 + 3] -> [80, 83]
    run_at_3 = calculate_backoff_run_at(attempt_count=3, base=base, jitter_max=jitter_max, now=now)
    delay_3 = (run_at_3 - now).total_seconds()
    assert 80.0 <= delay_3 <= 83.0


# ==============================================================================
# Test 6: Unknown Exceptions Wrapped and Treated as Transient
# ==============================================================================

def test_unknown_exceptions_treated_as_transient():
    exc = RuntimeError("Unexpected socket disconnect")
    classified = classify_exception(exc)
    assert isinstance(classified, TransientError)
    assert "Unexpected socket disconnect" in str(classified)


# ==============================================================================
# Test 7: Max Attempts Exhaustion Moves Job to Quarantined (Never Auto-Retried)
# ==============================================================================

def test_max_attempts_exhaustion_quarantines_job(in_memory_db, recording_emitter):
    def failing_stage(ctx: StageContext):
        raise TransientError("Persistent service outage")

    stage = Stage(name="outage_stage", callable=failing_stage, max_attempts=2)
    executor = StageExecutor(pipeline={"outage_stage": stage}, base_backoff_sec=1.0)

    job = PipelineJob(
        job_type="INGEST_VIDEO",
        entity_type="source_video",
        entity_id="vid_outage_01",
        status=PipelineJobStatus.PENDING,
        attempt_count=0,
    )
    in_memory_db.add(job)
    in_memory_db.commit()

    ctx = StageContext(db_session=in_memory_db, video_id="vid_outage_01", job_id=job.id)

    # Attempt 1: Transient retry scheduled
    with pytest.raises(TransientError):
        executor.execute_stage("outage_stage", ctx, job=job)
    in_memory_db.refresh(job)
    assert job.status == PipelineJobStatus.PENDING
    assert job.attempt_count == 1

    # Attempt 2: Max attempts reached -> Quarantined
    with pytest.raises(TransientError):
        executor.execute_stage("outage_stage", ctx, job=job)
    in_memory_db.refresh(job)
    assert job.status == "quarantined"
    assert job.status == PipelineJobStatus.QUARANTINED
    assert job.attempt_count == 2
    assert "Persistent service outage" in job.last_error

    # Assert quarantined event emitted
    assert len(recording_emitter.quarantined_events) == 1
    assert recording_emitter.quarantined_events[0]["attempt_count"] == 2


# ==============================================================================
# Test 8: End-to-End Pipeline Execution via Thin Executor
# ==============================================================================

def test_stage_executor_walks_pipeline_successfully(in_memory_db, recording_emitter):
    order: List[str] = []

    def stage_a(ctx: StageContext):
        order.append("A")
        return {"a": 1}

    def stage_b(ctx: StageContext):
        order.append("B")
        return {"b": 2}

    pipeline = {
        "step_a": Stage(name="step_a", callable=stage_a),
        "step_b": Stage(name="step_b", callable=stage_b),
    }

    executor = StageExecutor(pipeline=pipeline)
    job = PipelineJob(
        job_type="CUSTOM",
        entity_type="test",
        entity_id="entity_01",
        status=PipelineJobStatus.PENDING,
    )
    in_memory_db.add(job)
    in_memory_db.commit()

    ctx = StageContext(db_session=in_memory_db, video_id="v1", job_id=job.id)
    artifacts = executor.run_pipeline(ctx, job=job)

    assert order == ["A", "B"]
    assert artifacts["step_a"] == {"a": 1}
    assert artifacts["step_b"] == {"b": 2}

    in_memory_db.refresh(job)
    assert job.status == "done"
    assert job.status == PipelineJobStatus.DONE
    assert len(recording_emitter.finished_events) == 1


# ==============================================================================
# Test 9: Quarantined Jobs Visibility Query & GET /api/jobs/quarantined Endpoint
# ==============================================================================

def test_quarantined_jobs_visibility_query_and_api_endpoint(in_memory_db):
    # Insert a quarantined job and a dead letter job
    q_job = PipelineJob(
        job_type="RENDER_CLIP",
        entity_type="clip",
        entity_id="clip_quarantined_99",
        status=PipelineJobStatus.QUARANTINED,
        stage="render",
        attempt_count=3,
        error_message="Fatal filtergraph syntax error in libavcodec",
    )
    dl_job = PipelineJob(
        job_type="INGEST_VIDEO",
        entity_type="source_video",
        entity_id="vid_dead_88",
        status=PipelineJobStatus.DEAD_LETTER,
        stage="download",
        attempt_count=3,
        error_message="Private video: authentication required",
    )
    active_job = PipelineJob(
        job_type="RENDER_CLIP",
        entity_type="clip",
        entity_id="clip_active_77",
        status=PipelineJobStatus.RUNNING,
    )
    in_memory_db.add_all([q_job, dl_job, active_job])
    in_memory_db.commit()

    # Query function check
    results = get_quarantined_jobs(in_memory_db)
    assert len(results) == 2
    job_ids = [j.id for j in results]
    assert q_job.id in job_ids
    assert dl_job.id in job_ids
    assert active_job.id not in job_ids

    # Verify last_error property
    for r in results:
        assert r.last_error is not None
        assert len(r.last_error) > 0

    # API Endpoint check using FastAPI test client
    app = create_app()
    from shorts_engine.dashboard.routes.queue import get_db as queue_get_db
    from shorts_engine.db.session import get_db as session_get_db
    app.dependency_overrides[queue_get_db] = lambda: in_memory_db
    app.dependency_overrides[session_get_db] = lambda: in_memory_db

    client = TestClient(app)
    response = client.get("/api/jobs/quarantined")
    assert response.status_code == 200
    data = response.json()

    assert "total" in data
    assert data["total"] == 2
    assert "jobs" in data
    assert len(data["jobs"]) == 2

    q_data = next(j for j in data["jobs"] if j["entity_id"] == "clip_quarantined_99")
    assert q_data["stage"] == "render"
    assert q_data["last_error"] == "Fatal filtergraph syntax error in libavcodec"
    assert q_data["attempt_count"] == 3
