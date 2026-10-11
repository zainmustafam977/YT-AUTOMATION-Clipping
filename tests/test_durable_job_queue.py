"""
Unit and integration tests for DurableJobQueue.
Verifies:
1. Atomic enqueue and claim roundtrip (preventing race conditions).
2. Idempotent deduplication (preventing duplicate active jobs for same entity).
3. Heartbeat and granular progress telemetry persistence.
4. Job completion lifecycle.
5. Exponential backoff retry policies and Dead-Letter Queue (DLQ) transition.
6. Operator manual Dead-Letter Queue requeueing.
7. Orphaned worker lease reclamation.
"""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.db.session import Base, init_db
from shorts_engine.db.models import PipelineJob, PipelineJobStatus
from shorts_engine.services.queue.durable_queue import DurableJobQueue
from shorts_engine.services.telemetry.progress_tracker import job_progress_tracker


@pytest.fixture
def test_queue(tmp_path):
    """Creates an isolated in-memory or SQLite database with DurableJobQueue."""
    db_file = tmp_path / "test_queue.db"
    eng = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    init_db(target_engine=eng)
    SessionFactory = sessionmaker(bind=eng)
    queue = DurableJobQueue(session_factory=SessionFactory)
    return queue, SessionFactory


def test_enqueue_and_claim_atomic(test_queue):
    """Verifies that jobs can be enqueued and atomically claimed by a single worker."""
    queue, SessionFactory = test_queue

    job = queue.enqueue(
        job_type="INGEST_VIDEO",
        entity_id="test_vid_001",
        entity_type="source_video",
        payload={"quality": "1080p"},
    )
    assert job.id is not None
    assert job.status == PipelineJobStatus.PENDING

    # Worker A claims job
    claimed = queue.claim_next_job(worker_id="worker-hostA-100-abc")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == PipelineJobStatus.RUNNING
    assert claimed.locked_by == "worker-hostA-100-abc"
    assert claimed.locked_at is not None

    # Worker B tries to claim -> None available
    claimed_b = queue.claim_next_job(worker_id="worker-hostB-200-xyz")
    assert claimed_b is None


def test_idempotent_enqueue_deduplication(test_queue):
    """Verifies that enqueuing the same active entity returns the existing job."""
    queue, SessionFactory = test_queue

    job1 = queue.enqueue(job_type="INGEST_VIDEO", entity_id="vid_dedup_01")
    job2 = queue.enqueue(job_type="INGEST_VIDEO", entity_id="vid_dedup_01")

    assert job1.id == job2.id

    # Count total rows in database -> exactly 1
    with SessionFactory() as session:
        cnt = session.query(PipelineJob).filter_by(entity_id="vid_dedup_01").count()
    assert cnt == 1


def test_heartbeat_and_progress_telemetry(test_queue):
    """Verifies that heartbeat updates database timestamps and mirrors to telemetry tracker."""
    queue, SessionFactory = test_queue

    job = queue.enqueue(job_type="INGEST_VIDEO", entity_id="vid_telemetry_01")
    claimed = queue.claim_next_job(worker_id="worker-node1-111-aaa")

    success = queue.heartbeat(
        job_id=claimed.id,
        worker_id="worker-node1-111-aaa",
        progress_pct=42.5,
        progress_detail="Extracting audio and transcribing...",
        stage="TRANSCRIBING",
        speed="2.4x",
    )
    assert success is True

    with SessionFactory() as session:
        updated = session.query(PipelineJob).filter_by(id=claimed.id).first()
        assert updated.progress_pct == 42.5
        assert updated.stage == "TRANSCRIBING"
        assert updated.progress_detail == "Extracting audio and transcribing..."
        assert updated.heartbeat_at is not None

    # Check mirror in in-memory JobProgressTracker
    live_progress = job_progress_tracker.get_progress("vid_telemetry_01")
    assert live_progress is not None
    assert live_progress["percent"] == 42.5
    assert live_progress["stage"] == "TRANSCRIBING"


def test_complete_job(test_queue):
    """Verifies that completing a job marks status COMPLETED and records outputs."""
    queue, SessionFactory = test_queue

    job = queue.enqueue(job_type="RENDER_CLIP", entity_id="clip_001")
    claimed = queue.claim_next_job(worker_id="worker-node1-111-aaa")

    success = queue.complete_job(claimed.id, result={"output_path": "/storage/outputs/clip_001.mp4"})
    assert success is True

    with SessionFactory() as session:
        completed = session.query(PipelineJob).filter_by(id=claimed.id).first()
        assert completed.status == PipelineJobStatus.COMPLETED
        assert completed.progress_pct == 100.0
        assert completed.completed_at is not None
        assert completed.locked_by is None
        assert '"output_path": "/storage/outputs/clip_001.mp4"' in completed.result_json


def test_fail_job_exponential_backoff_and_dlq(test_queue):
    """
    Verifies exponential backoff retries:
    - Attempt 1: backoff delay scheduled, status PENDING.
    - Attempt 2: backoff delay doubles, status PENDING.
    - Attempt 3 (max_retries=3): moved to DEAD_LETTER (DLQ).
    """
    queue, SessionFactory = test_queue

    job = queue.enqueue(
        job_type="INGEST_VIDEO",
        entity_id="vid_failing_01",
        max_retries=3,
        backoff_multiplier_sec=10.0,
    )
    claimed = queue.claim_next_job(worker_id="worker-node1-111-aaa")

    # Attempt 1 failure -> delay = 10 * 2^0 = 10s
    f1 = queue.fail_job(claimed.id, "YouTube download timeout (attempt 1)")
    assert f1.retry_count == 1
    assert f1.status == PipelineJobStatus.PENDING
    assert f1.next_run_at is not None
    assert f1.next_run_at > datetime.utcnow()

    # Attempt to claim immediately -> None (next_run_at is in future)
    assert queue.claim_next_job(worker_id="worker-node1-111-aaa") is None

    # Simulate arrival of next_run_at by shifting next_run_at to past
    with SessionFactory() as session:
        session.query(PipelineJob).filter_by(id=claimed.id).update(
            {"next_run_at": datetime.utcnow() - timedelta(seconds=1)}
        )
        session.commit()

    # Attempt 2 claim & failure -> delay = 10 * 2^1 = 20s
    claimed_2 = queue.claim_next_job(worker_id="worker-node1-111-aaa")
    assert claimed_2 is not None
    f2 = queue.fail_job(claimed_2.id, "Whisper OOM error (attempt 2)")
    assert f2.retry_count == 2
    assert f2.status == PipelineJobStatus.PENDING

    # Fast forward next_run_at again
    with SessionFactory() as session:
        session.query(PipelineJob).filter_by(id=claimed.id).update(
            {"next_run_at": datetime.utcnow() - timedelta(seconds=1)}
        )
        session.commit()

    # Attempt 3 claim & failure -> max_retries reached -> DEAD_LETTER
    claimed_3 = queue.claim_next_job(worker_id="worker-node1-111-aaa")
    assert claimed_3 is not None
    f3 = queue.fail_job(claimed_3.id, "LLM rate limit permanent (attempt 3)")
    assert f3.retry_count == 3
    assert f3.status == PipelineJobStatus.DEAD_LETTER
    assert f3.stage == "DEAD_LETTER"
    assert f3.completed_at is not None


def test_requeue_dead_letter_job(test_queue):
    """Verifies that an operator can manually requeue a dead-letter job."""
    queue, SessionFactory = test_queue

    job = queue.enqueue(job_type="INGEST_VIDEO", entity_id="vid_dlq_01", max_retries=1)
    claimed = queue.claim_next_job(worker_id="worker-node1-111-aaa")
    f = queue.fail_job(claimed.id, "Hard failure")
    assert f.status == PipelineJobStatus.DEAD_LETTER

    # Operator unblocks DLQ job
    success = queue.requeue_dead_letter_job(f.id)
    assert success is True

    with SessionFactory() as session:
        requeued = session.query(PipelineJob).filter_by(id=f.id).first()
        assert requeued.status == PipelineJobStatus.PENDING
        assert requeued.retry_count == 0
        assert requeued.error_message is None

    # Verify job is immediately claimable again
    reclaimed = queue.claim_next_job(worker_id="worker-node2-222-bbb")
    assert reclaimed is not None
    assert reclaimed.id == f.id


def test_reconcile_orphaned_jobs(test_queue):
    """Verifies that jobs abandoned by a crashed or dead worker are recovered."""
    queue, SessionFactory = test_queue

    job = queue.enqueue(job_type="INGEST_VIDEO", entity_id="vid_orphaned_01")
    claimed = queue.claim_next_job(worker_id="worker-local-999999-dead01")

    # Simulate that heartbeat was 15 minutes ago
    with SessionFactory() as session:
        session.query(PipelineJob).filter_by(id=claimed.id).update({
            "heartbeat_at": datetime.utcnow() - timedelta(seconds=900),
            "locked_at": datetime.utcnow() - timedelta(seconds=900),
        })
        session.commit()

    with patch("shorts_engine.services.worker.runner.is_pid_alive", return_value=False):
        reclaimed_count = queue.reconcile_orphaned_jobs(stale_timeout_sec=600)

    assert reclaimed_count == 1

    with SessionFactory() as session:
        recovered = session.query(PipelineJob).filter_by(id=claimed.id).first()
        # Should be scheduled for retry via fail_job backoff
        assert recovered.status == PipelineJobStatus.PENDING
        assert recovered.retry_count == 1
        assert "Worker lease expired" in recovered.error_message
