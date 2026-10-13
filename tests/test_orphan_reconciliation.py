"""
Tests for Downstream Orphan Reconciliation (W3-1).
Verifies:
T1. Crash-after-commit-1 on ingest -> reaper creates transcribe job.
T2. Crash-after-commit-1 on transcribe -> reaper creates select job.
T3. Dual-dependency join race -> reframe + captions both DONE -> reaper creates render job.
T4. Idempotency -> consecutive reaper sweeps produce exactly one downstream job.
T5. Partial select fan-out -> reframe DONE but captions missing -> reaper creates captions, then render after captions completes.
T6. Negative: reframe DONE, captions QUARANTINED -> reaper enqueues NOTHING for render.
Entity guards: terminal/rejected entities are never resurrected.
Visibility: job.reconciled platform event emitted with db session.
"""

import uuid
from datetime import datetime, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.db.session import Base, init_db
from shorts_engine.db.models import (
    Channel,
    SourceVideo,
    SourceVideoStatus,
    ClipCandidate,
    Clip,
    ClipStatus,
    PipelineJob,
    PipelineJobStatus,
    EventLog,
)
from shorts_engine.services.queue.durable_queue import DurableJobQueue


@pytest.fixture
def test_env(tmp_path):
    """Provides an isolated database environment and DurableJobQueue instance."""
    db_file = tmp_path / "test_reconciliation.db"
    eng = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    init_db(target_engine=eng)
    SessionFactory = sessionmaker(bind=eng)
    queue = DurableJobQueue(session_factory=SessionFactory)

    session = SessionFactory()
    try:
        channel = Channel(
            id=str(uuid.uuid4()),
            youtube_channel_id="UC_TEST_RECON",
            name="Test Recon Channel",
        )
        session.add(channel)
        session.commit()
        channel_id = channel.id
    finally:
        session.close()

    return queue, SessionFactory, channel_id


def test_reaper_reconciles_crash_after_ingest_commit_1(test_env):
    """
    T1: Simulate crash-after-commit-1 on ingest:
    Job is marked DONE in DB, but worker crashed before enqueuing transcribe.
    Reaper sweep detects the orphan and creates transcribe job.
    """
    queue, SessionFactory, channel_id = test_env
    vid_id = f"vid_t1_{uuid.uuid4().hex[:8]}"

    with SessionFactory() as session:
        video = SourceVideo(
            channel_id=channel_id,
            youtube_video_id=vid_id,
            title="Video T1",
            status=SourceVideoStatus.DETECTED,
            downloaded_path="/tmp/vid_t1.mp4",
            duration_sec=30,
        )
        session.add(video)
        session.commit()

        # Simulate commit-1: ingest job marked DONE
        ingest_job = PipelineJob(
            job_type="ingest",
            entity_type="source_video",
            entity_id=vid_id,
            stage="ingest",
            status=PipelineJobStatus.DONE,
            completed_at=datetime.utcnow(),
            payload_json='{"file_path": "/tmp/vid_t1.mp4"}',
        )
        session.add(ingest_job)
        session.commit()

    # Verify transcribe does not exist yet
    with SessionFactory() as session:
        transcribe = session.query(PipelineJob).filter_by(entity_id=vid_id, stage="transcribe").first()
        assert transcribe is None, "Transcribe job must not exist before reconciliation"

    # Run downstream orphan sweep
    with SessionFactory() as session:
        reconciled = queue.reconcile_downstream_orphans(db=session)
        assert reconciled == 1, "Exactly one job must be reconciled"

    # Verify transcribe job now exists in PENDING
    with SessionFactory() as session:
        transcribe = session.query(PipelineJob).filter_by(entity_id=vid_id, stage="transcribe").first()
        assert transcribe is not None, "Transcribe job must be created by sweep"
        assert transcribe.status == PipelineJobStatus.PENDING

        # Visibility event check (A5)
        ev = session.query(EventLog).filter_by(entity_id=transcribe.id, event_name="job.reconciled").first()
        assert ev is not None, "job.reconciled event must be emitted"


def test_reaper_reconciles_crash_after_transcribe_commit_1(test_env):
    """
    T2: Simulate crash-after-commit-1 on transcribe:
    Transcribe job is DONE, but select was not enqueued.
    Reaper sweep creates select job.
    """
    queue, SessionFactory, channel_id = test_env
    vid_id = f"vid_t2_{uuid.uuid4().hex[:8]}"

    with SessionFactory() as session:
        video = SourceVideo(
            channel_id=channel_id,
            youtube_video_id=vid_id,
            title="Video T2",
            status=SourceVideoStatus.TRANSCRIBING,
            downloaded_path="/tmp/vid_t2.mp4",
            duration_sec=30,
        )
        session.add(video)

        transcribe_job = PipelineJob(
            job_type="transcribe",
            entity_type="source_video",
            entity_id=vid_id,
            stage="transcribe",
            status=PipelineJobStatus.DONE,
            completed_at=datetime.utcnow(),
            payload_json="{}",
        )
        session.add(transcribe_job)
        session.commit()

    with SessionFactory() as session:
        reconciled = queue.reconcile_downstream_orphans(db=session)
        assert reconciled == 1

        select_job = session.query(PipelineJob).filter_by(entity_id=vid_id, stage="select").first()
        assert select_job is not None
        assert select_job.status == PipelineJobStatus.PENDING


def test_reaper_resolves_dual_join_race_for_render(test_env):
    """
    T3: Dual-dependency join race resolution:
    Both reframe and captions are DONE for a clip, but neither enqueued render
    (simulating concurrent worker completion race or crash before join enqueue).
    Reaper sweep detects both dependencies satisfied and enqueues render.
    """
    queue, SessionFactory, channel_id = test_env
    vid_id = f"vid_t3_{uuid.uuid4().hex[:8]}"

    with SessionFactory() as session:
        video = SourceVideo(
            channel_id=channel_id,
            youtube_video_id=vid_id,
            title="Video T3",
            status=SourceVideoStatus.SEGMENTS_SELECTED,
        )
        session.add(video)
        session.flush()

        cand = ClipCandidate(
            source_video_id=video.id,
            start_ms=0,
            end_ms=15000,
            score=0.9,
            reason="Test hook reason",
        )
        session.add(cand)
        session.flush()

        clip = Clip(
            clip_candidate_id=cand.id,
            status=ClipStatus.RENDERING,
        )
        session.add(clip)
        session.flush()
        clip_id = clip.id

        # Both reframe and captions marked DONE
        reframe_job = PipelineJob(
            job_type="reframe",
            entity_type="clip",
            entity_id=clip_id,
            stage="reframe",
            status=PipelineJobStatus.DONE,
            completed_at=datetime.utcnow(),
        )
        captions_job = PipelineJob(
            job_type="captions",
            entity_type="clip",
            entity_id=clip_id,
            stage="captions",
            status=PipelineJobStatus.DONE,
            completed_at=datetime.utcnow(),
        )
        session.add_all([reframe_job, captions_job])
        session.commit()

    with SessionFactory() as session:
        render_job = session.query(PipelineJob).filter_by(entity_id=clip_id, stage="render").first()
        assert render_job is None, "Render job must not exist initially"

        reconciled = queue.reconcile_downstream_orphans(db=session)
        assert reconciled == 1

        render_job = session.query(PipelineJob).filter_by(entity_id=clip_id, stage="render").first()
        assert render_job is not None, "Render job must be enqueued by dual-join sweep"
        assert render_job.status == PipelineJobStatus.PENDING


def test_reaper_idempotency_no_duplicate_enqueues(test_env):
    """
    T4: Running the reaper multiple times is strictly idempotent:
    No duplicate jobs are created and existing jobs remain undisturbed.
    """
    queue, SessionFactory, channel_id = test_env
    vid_id = f"vid_t4_{uuid.uuid4().hex[:8]}"

    with SessionFactory() as session:
        video = SourceVideo(
            channel_id=channel_id,
            youtube_video_id=vid_id,
            title="Video T4",
            status=SourceVideoStatus.DETECTED,
            downloaded_path="/tmp/vid_t4.mp4",
        )
        session.add(video)
        ingest_job = PipelineJob(
            job_type="ingest",
            entity_type="source_video",
            entity_id=vid_id,
            stage="ingest",
            status=PipelineJobStatus.DONE,
            completed_at=datetime.utcnow(),
        )
        session.add(ingest_job)
        session.commit()

    # Pass 1: creates transcribe
    with SessionFactory() as session:
        r1 = queue.reconcile_downstream_orphans(db=session)
        assert r1 == 1

    # Pass 2: no new jobs created
    with SessionFactory() as session:
        r2 = queue.reconcile_downstream_orphans(db=session)
        assert r2 == 0

        jobs = session.query(PipelineJob).filter_by(entity_id=vid_id, stage="transcribe").all()
        assert len(jobs) == 1, "Exactly one transcribe job must exist"

    # Pass 3: via reconcile_orphaned_jobs wrapper
    with SessionFactory() as session:
        queue.reconcile_orphaned_jobs(db=session)
        jobs = session.query(PipelineJob).filter_by(entity_id=vid_id, stage="transcribe").all()
        assert len(jobs) == 1


def test_reaper_partial_select_fanout_missing_captions(test_env):
    """
    T5: Partial select fan-out:
    Clip exists with reframe DONE, but NO captions job at all.
    1. Sweep creates missing captions job (PENDING).
    2. Render is NOT enqueued yet (since captions is still in flight).
    3. When captions completes (DONE), subsequent sweep enqueues render.
    """
    queue, SessionFactory, channel_id = test_env
    vid_id = f"vid_t5_{uuid.uuid4().hex[:8]}"

    with SessionFactory() as session:
        video = SourceVideo(
            channel_id=channel_id,
            youtube_video_id=vid_id,
            title="Video T5",
            status=SourceVideoStatus.SEGMENTS_SELECTED,
        )
        session.add(video)
        session.flush()

        cand = ClipCandidate(source_video_id=video.id, start_ms=0, end_ms=10000, score=0.9, reason="Test hook reason")
        session.add(cand)
        session.flush()

        clip = Clip(clip_candidate_id=cand.id, status=ClipStatus.RENDERING)
        session.add(clip)
        session.flush()
        clip_id = clip.id

        # Only reframe is inserted and marked DONE. Captions job is missing entirely.
        reframe_job = PipelineJob(
            job_type="reframe",
            entity_type="clip",
            entity_id=clip_id,
            stage="reframe",
            status=PipelineJobStatus.DONE,
            completed_at=datetime.utcnow(),
        )
        session.add(reframe_job)
        session.commit()

    # Run sweep: must heal missing captions job
    with SessionFactory() as session:
        r1 = queue.reconcile_downstream_orphans(db=session)
        assert r1 == 1

        captions_job = session.query(PipelineJob).filter_by(entity_id=clip_id, stage="captions").first()
        assert captions_job is not None, "Captions job must be created by sweep"
        assert captions_job.status == PipelineJobStatus.PENDING

        render_job = session.query(PipelineJob).filter_by(entity_id=clip_id, stage="render").first()
        assert render_job is None, "Render job must NOT be enqueued while captions is PENDING"

    # Simulate captions completing
    with SessionFactory() as session:
        captions_job = session.query(PipelineJob).filter_by(entity_id=clip_id, stage="captions").first()
        captions_job.status = PipelineJobStatus.DONE
        captions_job.completed_at = datetime.utcnow()
        session.commit()

    # Run sweep again: dual-join satisfied -> enqueues render
    with SessionFactory() as session:
        r2 = queue.reconcile_downstream_orphans(db=session)
        assert r2 == 1

        render_job = session.query(PipelineJob).filter_by(entity_id=clip_id, stage="render").first()
        assert render_job is not None, "Render job must now be enqueued"
        assert render_job.status == PipelineJobStatus.PENDING


def test_reaper_negative_quarantined_dependency_suppresses_render(test_env):
    """
    T6 (Negative): reframe is DONE, but captions is QUARANTINED.
    Sweep must enqueue NOTHING for render (zero render jobs created).
    """
    queue, SessionFactory, channel_id = test_env
    vid_id = f"vid_t6_{uuid.uuid4().hex[:8]}"

    with SessionFactory() as session:
        video = SourceVideo(
            channel_id=channel_id,
            youtube_video_id=vid_id,
            title="Video T6",
            status=SourceVideoStatus.SEGMENTS_SELECTED,
        )
        session.add(video)
        session.flush()

        cand = ClipCandidate(source_video_id=video.id, start_ms=0, end_ms=10000, score=0.9, reason="Test hook reason")
        session.add(cand)
        session.flush()

        clip = Clip(clip_candidate_id=cand.id, status=ClipStatus.RENDERING)
        session.add(clip)
        session.flush()
        clip_id = clip.id

        reframe_job = PipelineJob(
            job_type="reframe",
            entity_type="clip",
            entity_id=clip_id,
            stage="reframe",
            status=PipelineJobStatus.DONE,
            completed_at=datetime.utcnow(),
        )
        captions_job = PipelineJob(
            job_type="captions",
            entity_type="clip",
            entity_id=clip_id,
            stage="captions",
            status=PipelineJobStatus.QUARANTINED,
            error_message="Whisper model corrupted",
            completed_at=datetime.utcnow(),
        )
        session.add_all([reframe_job, captions_job])
        session.commit()

    with SessionFactory() as session:
        reconciled = queue.reconcile_downstream_orphans(db=session)
        assert reconciled == 0, "No downstream jobs should be created when a dependency is QUARANTINED"

        render_job = session.query(PipelineJob).filter_by(entity_id=clip_id, stage="render").first()
        assert render_job is None, "Render job must NOT exist"


def test_reaper_guards_terminal_and_rejected_entities(test_env):
    """
    Entity-state guard (A2):
    - Terminal SourceVideo (status=DONE or FAILED) does NOT get downstream jobs enqueued.
    - Terminal Clip (status=REJECTED or WAITING_APPROVAL) does NOT get render enqueued.
    """
    queue, SessionFactory, channel_id = test_env
    vid_id = f"vid_guard_{uuid.uuid4().hex[:8]}"

    with SessionFactory() as session:
        # 1. Video already marked DONE
        video = SourceVideo(
            channel_id=channel_id,
            youtube_video_id=vid_id,
            title="Video Guard",
            status=SourceVideoStatus.DONE,
        )
        session.add(video)
        session.flush()

        ingest_job = PipelineJob(
            job_type="ingest",
            entity_type="source_video",
            entity_id=vid_id,
            stage="ingest",
            status=PipelineJobStatus.DONE,
            completed_at=datetime.utcnow(),
        )
        session.add(ingest_job)

        # 2. Clip rejected by user
        cand = ClipCandidate(source_video_id=video.id, start_ms=0, end_ms=10000, score=0.9, reason="Test hook reason")
        session.add(cand)
        session.flush()

        clip = Clip(clip_candidate_id=cand.id, status=ClipStatus.REJECTED)
        session.add(clip)
        session.flush()
        clip_id = clip.id

        reframe_job = PipelineJob(
            job_type="reframe",
            entity_type="clip",
            entity_id=clip_id,
            stage="reframe",
            status=PipelineJobStatus.DONE,
            completed_at=datetime.utcnow(),
        )
        captions_job = PipelineJob(
            job_type="captions",
            entity_type="clip",
            entity_id=clip_id,
            stage="captions",
            status=PipelineJobStatus.DONE,
            completed_at=datetime.utcnow(),
        )
        session.add_all([reframe_job, captions_job])
        session.commit()

    with SessionFactory() as session:
        reconciled = queue.reconcile_downstream_orphans(db=session)
        assert reconciled == 0, "No jobs should be reconciled for terminal or rejected entities"

        transcribe = session.query(PipelineJob).filter_by(entity_id=vid_id, stage="transcribe").first()
        assert transcribe is None

        render = session.query(PipelineJob).filter_by(entity_id=clip_id, stage="render").first()
        assert render is None
