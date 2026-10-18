import uuid
import pytest
from pathlib import Path
from sqlalchemy.orm.session import Session

from shorts_engine.db.session import SessionLocal, Base, engine
from shorts_engine.db.models import (
    SourceVideo,
    SourceVideoStatus,
    PipelineJob,
    PipelineJobStatus,
    EventLog,
)
from shorts_engine.orchestration.telemetry import emit
from shorts_engine.services.worker.daemon import WorkerDaemon


@pytest.fixture(scope="module", autouse=True)
def _ensure_schema():
    """These tests use the process-wide SessionLocal/engine.

    They previously depended on whichever module happened to run first having
    created the tables, so they failed with "no such table" whenever collection
    order changed. Create the schema explicitly instead.
    """
    Base.metadata.create_all(engine)
    yield


def test_emit_with_db_parameter_does_not_close_session(tmp_path):
    """
    Unit test: emit() with db parameter uses the provided session
    and does NOT close it or detach ORM objects after returning.
    """
    vid_id = f"test_vid_telemetry_{uuid.uuid4().hex[:8]}"
    session = SessionLocal()
    try:
        job = PipelineJob(
            job_type="ingest",
            entity_type="source_video",
            entity_id=vid_id,
            stage="ingest",
            status=PipelineJobStatus.DONE,
        )
        session.add(job)
        session.commit()

        # Emit with db=session passed
        emit(
            "job_finished",
            job_id=job.id,
            stage="ingest",
            entity_id=job.entity_id,
            db=session,
        )

        # Assert session is still active and open (not closed by emit)
        assert session.is_active is True

        # Assert accessing attributes on the job does NOT raise DetachedInstanceError
        assert job.stage == "ingest"
        assert job.status == PipelineJobStatus.DONE

        # Assert EventLog row was persisted in the same session
        ev = session.query(EventLog).filter_by(entity_id=job.id).first()
        assert ev is not None
        assert ev.event_name == "stage.finished"
    finally:
        session.close()


def test_worker_execute_job_with_real_emit_and_sessionlocal_enqueues_transcribe(tmp_path):
    """
    Integration test: WorkerDaemon.execute_job with REAL emit() (not mocked)
    and REAL SessionLocal, asserting downstream transcribe stage is enqueued
    after ingest completes without DetachedInstanceError.
    """
    vid_id = f"test_vid_lifecycle_{uuid.uuid4().hex[:8]}"
    dummy_video_file = tmp_path / "test_ingest_lifecycle.mp4"
    dummy_video_file.write_bytes(b"\x00" * 1024)

    session = SessionLocal()
    try:
        from shorts_engine.services.ingestion.local_loader import LocalVideoIngestionService
        channel = LocalVideoIngestionService.get_or_create_local_channel(session)

        video = SourceVideo(
            channel_id=channel.id,
            youtube_video_id=vid_id,
            title="test_ingest_lifecycle",
            status=SourceVideoStatus.DETECTED,
            downloaded_path=str(dummy_video_file),
            duration_sec=10,
        )
        session.add(video)
        session.commit()

        job = PipelineJob(
            job_type="ingest",
            entity_type="source_video",
            entity_id=vid_id,
            stage="ingest",
            status=PipelineJobStatus.PENDING,
            payload_json="{}",
        )
        session.add(job)
        session.commit()
        job_id = job.id
    finally:
        session.close()

    daemon = WorkerDaemon(session_factory=SessionLocal)

    # Claim & execute the job using real unmocked emit and real SessionLocal
    success = daemon.execute_job(job)
    assert success is True, "WorkerDaemon.execute_job must succeed"

    # Verify state in fresh session
    verify_session = SessionLocal()
    try:
        ingest_job = verify_session.query(PipelineJob).filter_by(id=job_id).first()
        assert ingest_job.status == PipelineJobStatus.DONE

        # Verify downstream transcribe job was enqueued
        transcribe_job = (
            verify_session.query(PipelineJob)
            .filter(
                PipelineJob.entity_id == vid_id,
                PipelineJob.stage == "transcribe",
            )
            .first()
        )
        assert transcribe_job is not None, "Transcribe job must be enqueued after ingest completes"
        assert transcribe_job.status == PipelineJobStatus.PENDING
    finally:
        verify_session.close()
