"""
Acceptance Test Suite for Standalone Worker Process, Watch Folder Ingestion, and Fan-out.

ACCEPTANCE CRITERIA:
1. A file dropped in the watch folder completes the full pipeline with no dashboard involvement.
2. Dropping the same file twice produces one pipeline run (dedup via the unique constraint).
3. On SQLite, starting a second worker process refuses to start (single-worker enforced via lockfile/PID check).
4. On Postgres/multi-worker: while one video renders (render concurrency = 1), another video's transcription
   proceeds concurrently (demonstrated with overlapping execution logs).
5. Graceful shutdown: on SIGTERM/SIGINT, active in-flight job completes before worker exits.
"""

import os
import time
import json
import logging
import threading
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base, init_db
from shorts_engine.db.models import (
    SourceVideo,
    SourceVideoStatus,
    ClipCandidate,
    Clip,
    ClipStatus,
    PipelineJob,
    PipelineJobStatus,
    Transcript,
)
from shorts_engine.services.queue.durable_queue import DurableJobQueue
from shorts_engine.services.worker.daemon import WorkerDaemon, DEFAULT_GROUP_CONCURRENCY
from shorts_engine.services.ingestion.watch_folder import WatchFolderDaemon
from shorts_engine.orchestration.stage_registry import Stage, StageContext, StageExecutor

logger = logging.getLogger("test_worker_acceptance")


@pytest.fixture
def test_env(tmp_path):
    """Sets up an isolated SQLite test database and storage folders."""
    db_file = tmp_path / "test_pipeline.db"
    eng = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(target_engine=eng)
    SessionFactory = sessionmaker(bind=eng)

    watch_dir = tmp_path / "watch_folder"
    watch_dir.mkdir(parents=True, exist_ok=True)

    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / "transcripts").mkdir(parents=True, exist_ok=True)
    (storage_dir / "crops").mkdir(parents=True, exist_ok=True)
    (storage_dir / "subtitles").mkdir(parents=True, exist_ok=True)
    (storage_dir / "renders").mkdir(parents=True, exist_ok=True)

    return {
        "engine": eng,
        "SessionFactory": SessionFactory,
        "watch_dir": watch_dir,
        "storage_dir": storage_dir,
        "tmp_path": tmp_path,
    }


# ==============================================================================
# Acceptance Test 1: Watch Folder Full Pipeline Completion (Zero Dashboard Involvement)
# ==============================================================================

def test_watch_folder_completes_full_pipeline_no_dashboard(test_env):
    """
    ACCEPTANCE 1:
    A file dropped in the watch folder completes the full pipeline with no dashboard involvement.
    Watch folder stabilizes file -> enqueues 'ingest' -> worker processes ingest -> transcribe
    -> select (fans out to 2 clips) -> reframe & captions -> render -> waiting approval.
    """
    SessionFactory = test_env["SessionFactory"]
    watch_dir = test_env["watch_dir"]
    storage_dir = test_env["storage_dir"]

    # 1. Drop a video file in watch folder
    video_file = watch_dir / "podcast_ep1.mp4"
    video_file.write_bytes(b"\x00\x00\x00\x20ftypmp42" + b"A" * 1024)

    # Mock media probing and AI service calls for deterministic testing
    mock_metadata = {
        "duration_sec": 60.0,
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "codec": "h264",
        "has_audio": True,
    }
    mock_transcript_dict = {
        "text": "Welcome to the show. Today we discuss artificial intelligence and the future.",
        "language": "en",
        "words": [
            {"word": "Welcome", "start": 0.0, "end": 0.5, "score": 0.99},
            {"word": "to", "start": 0.5, "end": 0.7, "score": 0.99},
            {"word": "the", "start": 0.7, "end": 0.9, "score": 0.99},
            {"word": "show", "start": 0.9, "end": 1.5, "score": 0.99},
        ],
    }
    mock_candidates = [
        MagicMock(
            to_dict=lambda: {
                "start_ms": 0,
                "end_ms": 20000,
                "virality_score": 92.5,
                "reasoning": "Strong hook",
            }
        ),
        MagicMock(
            to_dict=lambda: {
                "start_ms": 20000,
                "end_ms": 45000,
                "virality_score": 88.0,
                "reasoning": "Engaging punchline",
            }
        ),
    ]

    with patch("shorts_engine.services.ingestion.local_loader.LocalVideoIngestionService.probe_video_metadata", return_value=mock_metadata), \
         patch("shorts_engine.services.transcription.engine.TranscriptionService.transcribe") as mock_trans, \
         patch("shorts_engine.services.clip_selection.selector.ClipSelectionService.select_clips", return_value=mock_candidates), \
         patch("shorts_engine.services.reframing.cropper.VideoReframer.compute_crop_plan", return_value={"mode": "center", "crop": [420, 0, 1080, 1920]}), \
         patch("shorts_engine.services.captioning.ass_generator.ASSSubtitleGenerator.generate_ass"), \
         patch("shorts_engine.services.rendering.compositor.VideoCompositor.render_short", return_value=storage_dir / "renders" / "clip.mp4"), \
         patch("shorts_engine.config.settings.settings.STORAGE_DIR", storage_dir), \
         patch("shorts_engine.services.queue.durable_queue.SessionLocal", SessionFactory):

        mock_transcript_obj = MagicMock()
        mock_transcript_obj.to_dict.return_value = mock_transcript_dict
        mock_transcript_obj.language = "en"
        mock_trans.return_value = mock_transcript_obj

        # 2. Watch folder detects stabilized file and strictly enqueues 'ingest'
        watch_daemon = WatchFolderDaemon(
            watch_dir=watch_dir,
            auto_ingest_db=True,
            stable_time_sec=0.01,
            poll_interval_sec=0.01,
            session_factory=SessionFactory,
        )
        watch_daemon.stable_time_sec = 0.01
        watch_daemon.scan_directory_candidates()
        watch_daemon.poll_and_process_stable_files()
        time.sleep(0.05)
        ready = watch_daemon.poll_and_process_stable_files()
        assert len(ready) == 1
        assert ready[0].name == "podcast_ep1.mp4"

        # Verify initial state: SourceVideo is created, and exactly 1 pending ingest job is enqueued
        with SessionFactory() as db:
            video = db.query(SourceVideo).first()
            assert video is not None
            assert video.status in (SourceVideoStatus.DETECTED, SourceVideoStatus.DOWNLOADING)

            pending_jobs = db.query(PipelineJob).filter_by(entity_id=video.youtube_video_id).all()
            assert len(pending_jobs) == 1
            assert pending_jobs[0].stage == "ingest"
            assert pending_jobs[0].status == PipelineJobStatus.PENDING

        # 3. Worker Daemon runs and executes full pipeline autonomously
        worker = WorkerDaemon(
            worker_id="test-worker-01",
            session_factory=SessionFactory,
            poll_interval_sec=0.05,
            lock_file_path=test_env["tmp_path"] / ".worker.lock",
        )

        # Worker processes all pending pipeline jobs through completion
        jobs_processed = worker.run_until_idle(max_jobs=20)
        assert jobs_processed > 0

        # Verify final state:
        with SessionFactory() as db:
            # Video is DONE
            video = db.query(SourceVideo).first()
            assert video.status == SourceVideoStatus.DONE

            # Exactly 2 clips were created from fan-out
            clips = db.query(Clip).all()
            assert len(clips) == 2
            for clip in clips:
                assert clip.status == ClipStatus.WAITING_APPROVAL
                assert clip.rendered_path is not None

            # Verify every stage job succeeded
            completed_jobs = db.query(PipelineJob).filter(PipelineJob.status == PipelineJobStatus.DONE).all()
            stages_completed = [j.stage for j in completed_jobs]
            assert "ingest" in stages_completed
            assert "transcribe" in stages_completed
            assert "select" in stages_completed
            assert "reframe" in stages_completed
            assert "captions" in stages_completed
            assert "render" in stages_completed


# ==============================================================================
# Acceptance Test 2: Duplicate File Drop Deduplication (Unique Constraint)
# ==============================================================================

def test_dropping_same_file_twice_produces_one_pipeline_run(test_env):
    """
    ACCEPTANCE 2:
    Dropping the same file twice produces one pipeline run (dedup via UNIQUE(entity_id, stage)).
    """
    SessionFactory = test_env["SessionFactory"]
    watch_dir = test_env["watch_dir"]
    storage_dir = test_env["storage_dir"]

    video_file = watch_dir / "duplicate_test.mp4"
    video_file.write_bytes(b"\x00\x00\x00\x20ftypmp42" + b"B" * 1024)

    mock_metadata = {
        "duration_sec": 30.0,
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "codec": "h264",
        "has_audio": True,
    }

    with patch("shorts_engine.services.ingestion.local_loader.LocalVideoIngestionService.probe_video_metadata", return_value=mock_metadata), \
         patch("shorts_engine.config.settings.settings.STORAGE_DIR", storage_dir), \
         patch("shorts_engine.services.queue.durable_queue.SessionLocal", SessionFactory):

        watch_daemon = WatchFolderDaemon(
            watch_dir=watch_dir,
            auto_ingest_db=True,
            stable_time_sec=0.01,
            poll_interval_sec=0.01,
            session_factory=SessionFactory,
        )
        watch_daemon.stable_time_sec = 0.01

        # Drop 1: Process file
        watch_daemon.scan_directory_candidates()
        watch_daemon.poll_and_process_stable_files()
        time.sleep(0.05)
        ready1 = watch_daemon.poll_and_process_stable_files()
        assert len(ready1) == 1

        with SessionFactory() as db:
            video = db.query(SourceVideo).first()
            assert video is not None
            jobs_count1 = db.query(PipelineJob).filter_by(entity_id=video.youtube_video_id, stage="ingest").count()
            assert jobs_count1 == 1

        # Drop 2: Simulate re-dropping the exact same file in watch folder
        watch_daemon._processed_files.clear()  # reset in-memory cache to simulate fresh poller iteration
        watch_daemon.scan_directory_candidates()
        watch_daemon.poll_and_process_stable_files()
        time.sleep(0.05)
        ready2 = watch_daemon.poll_and_process_stable_files()

        # Database state: Only 1 SourceVideo and exactly 1 'ingest' PipelineJob must exist
        with SessionFactory() as db:
            videos = db.query(SourceVideo).all()
            assert len(videos) == 1

            jobs = db.query(PipelineJob).filter_by(entity_id=video.youtube_video_id, stage="ingest").all()
            assert len(jobs) == 1, "Expected exactly 1 ingest job; duplicate was rejected via unique constraint."

            # Manually calling enqueue for the same (entity_id, stage) returns existing job
            queue = DurableJobQueue(session_factory=SessionFactory)
            dup_job = queue.enqueue("ingest", video.youtube_video_id, stage="ingest", db=db)
            assert dup_job.id == jobs[0].id

            # Verify total count remains exactly 1
            total_ingest = db.query(PipelineJob).filter_by(entity_id=video.youtube_video_id, stage="ingest").count()
            assert total_ingest == 1


# ==============================================================================
# Acceptance Test 3: SQLite Parallel Worker Refusal via Lockfile
# ==============================================================================

def test_sqlite_parallel_worker_refusal(test_env):
    """
    ACCEPTANCE 3:
    On SQLite, do NOT attempt parallel workers: enforce a single worker with a lockfile or PID check
    so a second worker refuses to start.
    """
    SessionFactory = test_env["SessionFactory"]
    lock_path = test_env["tmp_path"] / ".worker.lock"

    # Worker 1 starts on SQLite
    worker1 = WorkerDaemon(
        worker_id="worker-primary",
        session_factory=SessionFactory,
        lock_file_path=lock_path,
    )
    worker1.acquire_sqlite_lock()
    assert lock_path.exists()
    assert worker1._lock_acquired is True

    # Worker 2 attempts to start with the same lockfile
    worker2 = WorkerDaemon(
        worker_id="worker-secondary",
        session_factory=SessionFactory,
        lock_file_path=lock_path,
    )

    with pytest.raises(RuntimeError, match="Parallel workers on SQLite are prohibited"):
        worker2.acquire_sqlite_lock()

    # Worker 1 shuts down and cleans up lockfile
    worker1.release_sqlite_lock()
    assert not lock_path.exists()

    # Worker 2 can now acquire lock cleanly
    worker2.acquire_sqlite_lock()
    assert lock_path.exists()
    worker2.release_sqlite_lock()


# ==============================================================================
# Acceptance Test 4: Concurrency Overlap (Render = 1 While Transcribe Overlaps)
# ==============================================================================

def test_postgres_render_concurrency_and_transcribe_overlap(test_env):
    """
    ACCEPTANCE 4:
    On Postgres: while one video renders (render concurrency = 1), another video's transcription
    proceeds concurrently (demonstrated with overlapping execution timestamps and logs).
    """
    SessionFactory = test_env["SessionFactory"]
    queue = DurableJobQueue(session_factory=SessionFactory)

    # Video 1 has a render job; Video 2 has a transcribe job
    job_render_1 = queue.enqueue("render", "vid_01", entity_type="clip", stage="render")
    job_transcribe_2 = queue.enqueue("transcribe", "vid_02", entity_type="source_video", stage="transcribe")
    job_render_2 = queue.enqueue("render", "vid_03", entity_type="clip", stage="render")

    timeline: List[Tuple[str, str, float]] = []
    timeline_lock = threading.Lock()
    # Signalled once worker 1 has actually claimed and entered the render
    # job. The test used to sleep 0.05s and hope, which lost the race on
    # slower runners (observed: Windows CI) - worker 2 then started before
    # worker 1 held the render slot, no render event was ever recorded, and
    # the assertion failed with KeyError: 'render_vid_01_start'. Waiting on
    # this event makes the ordering explicit instead of time-dependent.
    render_started = threading.Event()

    def mock_render_call(ctx):
        with timeline_lock:
            timeline.append(("render_vid_01", "start", time.time()))
        render_started.set()
        logger.info("[TIMELINE] Render vid_01 started")
        time.sleep(0.3)
        with timeline_lock:
            timeline.append(("render_vid_01", "end", time.time()))
        logger.info("[TIMELINE] Render vid_01 ended")
        return {"status": "rendered"}

    def mock_transcribe_call(ctx):
        with timeline_lock:
            timeline.append(("transcribe_vid_02", "start", time.time()))
        logger.info("[TIMELINE] Transcribe vid_02 started")
        time.sleep(0.15)
        with timeline_lock:
            timeline.append(("transcribe_vid_02", "end", time.time()))
        logger.info("[TIMELINE] Transcribe vid_02 ended")
        return {"status": "transcribed"}

    custom_pipeline = {
        "render": Stage(name="render", callable=mock_render_call),
        "transcribe": Stage(name="transcribe", callable=mock_transcribe_call),
    }

    executor = StageExecutor(pipeline=custom_pipeline)

    # Worker 1: claims render job (render concurrency limit = 1)
    worker1 = WorkerDaemon(
        worker_id="worker-render-1",
        session_factory=SessionFactory,
        group_concurrency={"render": 1},
        stage_executor=executor,
    )
    # Worker 2: claims transcribe job concurrently
    worker2 = WorkerDaemon(
        worker_id="worker-transcribe-2",
        session_factory=SessionFactory,
        group_concurrency={"render": 1},
        stage_executor=executor,
    )

    # Simulate Postgres dialect allowing concurrent workers
    with patch("shorts_engine.services.queue.durable_queue.is_postgres", return_value=True), \
         patch("shorts_engine.services.worker.daemon.is_postgres", return_value=True):

        t1 = threading.Thread(target=worker1.run_once)
        t2 = threading.Thread(target=worker2.run_once)

        # Launch Worker 1 (starts render)
        t1.start()

        # Wait until the render job is genuinely RUNNING and holding the
        # render slot, rather than guessing with a fixed sleep.
        assert render_started.wait(timeout=10), (
            "worker 1 did not claim the render job within 10s - the "
            "concurrency scenario under test never started"
        )

        # Launch Worker 2 concurrently (picks transcribe because render concurrency = 1)
        t2.start()

        t1.join()
        t2.join()

    # Verify execution timeline demonstrates overlap
    event_dict = {}
    for name, evt, ts in timeline:
        event_dict[f"{name}_{evt}"] = ts

    render_start = event_dict["render_vid_01_start"]
    render_end = event_dict["render_vid_01_end"]
    transcribe_start = event_dict["transcribe_vid_02_start"]
    transcribe_end = event_dict["transcribe_vid_02_end"]

    # Log demonstrating concurrency overlap
    logger.info(
        "CONCURRENCY VERIFICATION: Render active from %.3f to %.3f (duration: %.3fs). "
        "Transcribe active from %.3f to %.3f (duration: %.3fs). "
        "Overlap confirmed: transcribe started during render!",
        render_start, render_end, render_end - render_start,
        transcribe_start, transcribe_end, transcribe_end - transcribe_start,
    )

    # Assert that transcribe executed WHILE render was actively running
    assert transcribe_start > render_start, "Transcribe started after render began"
    assert transcribe_start < render_end, "Transcribe started BEFORE render finished (concurrency overlap!)"
    assert transcribe_end < render_end, "Transcribe finished before render finished"


# ==============================================================================
# Acceptance Test 5: Graceful Shutdown on SIGTERM (Finish Active Job, Then Exit)
# ==============================================================================

def test_graceful_shutdown_finishes_active_job(test_env):
    """
    ACCEPTANCE 5:
    Graceful shutdown: on SIGTERM/SIGINT, finish the current in-flight job, then exit cleanly.
    """
    SessionFactory = test_env["SessionFactory"]
    queue = DurableJobQueue(session_factory=SessionFactory)
    job = queue.enqueue("ingest", "vid_graceful_01", stage="ingest")

    job_completed = threading.Event()
    worker_exited = threading.Event()

    def mock_slow_ingest_call(ctx):
        # Simulate worker receiving SIGTERM during active processing
        time.sleep(0.2)
        job_completed.set()
        return {"video_path": "done.mp4"}

    custom_pipeline = {
        "ingest": Stage(name="ingest", callable=mock_slow_ingest_call),
    }
    executor = StageExecutor(pipeline=custom_pipeline)

    worker = WorkerDaemon(
        worker_id="worker-graceful",
        session_factory=SessionFactory,
        poll_interval_sec=0.05,
        lock_file_path=test_env["tmp_path"] / ".worker.lock",
        stage_executor=executor,
    )

    def run_worker():
        worker.run()
        worker_exited.set()

    t = threading.Thread(target=run_worker)
    t.start()

    deadline = time.time() + 3.0
    while time.time() < deadline and worker._active_job_id != job.id:
        time.sleep(0.01)
    assert worker._active_job_id == job.id

    # Send shutdown signal while job is running
    worker.shutdown_requested = True
    worker._stop_event.set()

    t.join(timeout=3.0)
    assert not t.is_alive()
    assert worker_exited.is_set()
    assert job_completed.is_set(), "In-flight job was allowed to complete before worker exited."

    # Verify job ended 'DONE' in database
    with SessionFactory() as db:
        final_job = db.query(PipelineJob).filter_by(id=job.id).first()
        assert final_job.status == PipelineJobStatus.DONE
        assert final_job.completed_at is not None
        assert final_job.locked_by is None
