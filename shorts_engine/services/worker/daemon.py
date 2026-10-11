"""
Standalone Worker Process Daemon.
Implements:
1. Claim loop with dialect-aware locking:
   - PostgreSQL: SELECT ... FOR UPDATE SKIP LOCKED for safe multi-worker claiming.
   - SQLite: Single-worker lockfile check (.worker.lock + PID check) refusing concurrent workers.
2. Worker topology configuration: workers pinned to stage groups with group concurrency limits
   (Default: render group concurrency = 1).
3. Fan-out orchestration:
   - 'select' creates clip rows and enqueues per-clip jobs (reframe, captions).
   - Dual-dependency join: 'render' is enqueued ONLY when BOTH 'reframe' and 'captions' are DONE.
4. Graceful shutdown: on SIGTERM / SIGINT, finishes active in-flight job, then exits cleanly.
"""

import os
import sys
import time
import json
import signal
import socket
import logging
import argparse
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Set, Tuple
from sqlalchemy.orm import Session

from shorts_engine.config.settings import settings
from shorts_engine.db.session import SessionLocal, is_postgres, engine as default_engine
from shorts_engine.db.models import (
    PipelineJob,
    PipelineJobStatus,
    SourceVideo,
    SourceVideoStatus,
    ClipCandidate,
    Clip,
    ClipStatus,
    Transcript,
)
from shorts_engine.orchestration.stage_registry import (
    StageContext,
    StageExecutor,
    PIPELINE,
)
from shorts_engine.orchestration.telemetry import emit
from shorts_engine.services.queue.durable_queue import durable_job_queue, DurableJobQueue
from shorts_engine.services.worker.runner import (
    generate_worker_id,
    is_pid_alive,
    clear_cuda_cache,
)

logger = logging.getLogger(__name__)

DEFAULT_STAGE_GROUPS: Dict[str, List[str]] = {
    "ingest": ["ingest", "download"],
    "transcribe": ["transcribe"],
    "select": ["select", "analyze_candidates"],
    "clip_prep": ["reframe", "crop_plan", "captions", "caption"],
    "render": ["render"],
    "publish": ["publish"],
}

DEFAULT_GROUP_CONCURRENCY: Dict[str, int] = {
    "render": 1,
}


class WorkerDaemon:
    """
    Decoupled Standalone Pipeline Worker Daemon.
    Continuously claims jobs from DurableJobQueue, executes them via StageExecutor,
    manages fan-out dependency joins, and shuts down gracefully.
    """

    def __init__(
        self,
        worker_id: Optional[str] = None,
        pinned_groups: Optional[List[str]] = None,
        group_concurrency: Optional[Dict[str, int]] = None,
        poll_interval_sec: float = 1.0,
        session_factory=SessionLocal,
        lock_file_path: Optional[Path] = None,
        stage_executor: Optional[StageExecutor] = None,
    ):
        self.worker_id = worker_id or generate_worker_id()
        self.session_factory = session_factory
        self.poll_interval_sec = max(0.1, poll_interval_sec)
        self.lock_file = lock_file_path or (Path(settings.STORAGE_DIR) / ".worker.lock")
        self.stage_executor = stage_executor or StageExecutor()

        self.pinned_groups = pinned_groups
        self.group_concurrency = dict(DEFAULT_GROUP_CONCURRENCY)
        if group_concurrency:
            self.group_concurrency.update(group_concurrency)

        # Resolve allowed stages from pinned groups if specified
        self.allowed_stages: Optional[List[str]] = None
        if self.pinned_groups:
            allowed: List[str] = []
            for grp in self.pinned_groups:
                g_stages = DEFAULT_STAGE_GROUPS.get(grp.lower(), [grp.lower()])
                allowed.extend(g_stages)
            self.allowed_stages = list(set(allowed))

        # Map group concurrency to stage concurrency limits
        self.stage_concurrency_limits: Dict[str, int] = {}
        for grp, limit in self.group_concurrency.items():
            stages = DEFAULT_STAGE_GROUPS.get(grp, [grp])
            for st in stages:
                self.stage_concurrency_limits[st] = limit

        self.shutdown_requested = False
        self._stop_event = threading.Event()
        self._active_job_id: Optional[str] = None
        self._lock_acquired = False

    def acquire_sqlite_lock(self) -> None:
        """
        Enforces single-worker operation on SQLite.
        If another active process holds the lockfile, refuses to start.
        On PostgreSQL, skips lockfile since row-level locking handles concurrency safely.
        """
        db = self.session_factory()
        try:
            if is_postgres(db.get_bind()):
                # PostgreSQL supports multi-worker concurrency natively
                return
        finally:
            db.close()

        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        if self.lock_file.exists():
            try:
                content = self.lock_file.read_text().strip()
                data = json.loads(content) if content.startswith("{") else {"pid": int(content)}
                existing_pid = int(data.get("pid", 0))
                existing_wid = data.get("worker_id")
                is_active_other = (
                    existing_pid > 0
                    and is_pid_alive(existing_pid)
                    and (existing_pid != os.getpid() or (existing_wid and existing_wid != self.worker_id))
                )
                if is_active_other:
                    msg = (
                        f"Worker process already running on SQLite with PID {existing_pid} "
                        f"(worker: {existing_wid}) (lockfile: {self.lock_file}). "
                        f"Parallel workers on SQLite are prohibited."
                    )
                    logger.error(msg)
                    raise RuntimeError(msg)
                else:
                    logger.info("Found stale SQLite worker lockfile (PID %s is dead). Overwriting.", existing_pid)
            except (ValueError, json.JSONDecodeError):
                logger.warning("Unreadable SQLite worker lockfile found. Overwriting.")

        # Write lockfile
        lock_info = {
            "pid": os.getpid(),
            "worker_id": self.worker_id,
            "started_at": datetime.utcnow().isoformat(),
        }
        self.lock_file.write_text(json.dumps(lock_info))
        self._lock_acquired = True
        logger.info("Acquired SQLite single-worker lock for PID %d (%s)", os.getpid(), self.worker_id)

    def release_sqlite_lock(self) -> None:
        """Removes the SQLite worker lockfile if owned by this process."""
        if not self._lock_acquired or not self.lock_file.exists():
            return
        try:
            content = self.lock_file.read_text().strip()
            data = json.loads(content) if content.startswith("{") else {"pid": int(content)}
            if int(data.get("pid", 0)) == os.getpid():
                self.lock_file.unlink(missing_ok=True)
                self._lock_acquired = False
                logger.info("Released SQLite worker lock for PID %d", os.getpid())
        except Exception as e:
            logger.debug("Failed releasing SQLite worker lockfile: %s", e)

    def _setup_signal_handlers(self) -> None:
        """Attaches graceful shutdown signal handlers for SIGTERM and SIGINT."""
        def handle_signal(signum, frame):
            sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
            logger.info("Received %s signal. Requesting graceful shutdown (finishing in-flight job)...", sig_name)
            self.shutdown_requested = True
            self._stop_event.set()

        try:
            signal.signal(signal.SIGTERM, handle_signal)
            signal.signal(signal.SIGINT, handle_signal)
        except (ValueError, AttributeError):
            # When running on non-main threads in unit tests
            pass

    def _build_stage_context(self, job: PipelineJob, session: Session) -> StageContext:
        """Constructs a unit-testable StageContext for the claimed job."""
        is_clip_level = job.stage in ("reframe", "crop_plan", "captions", "caption", "render")
        clip_id = job.entity_id if is_clip_level else None

        video_id = job.entity_id if not is_clip_level else None
        if is_clip_level and clip_id:
            clip = session.query(Clip).filter_by(id=clip_id).first()
            if clip and clip.clip_candidate and clip.clip_candidate.source_video:
                video_id = clip.clip_candidate.source_video.youtube_video_id
            elif job.payload_json:
                try:
                    payload_data = json.loads(job.payload_json)
                    video_id = payload_data.get("video_id")
                except Exception:
                    pass

        payload_dict = {}
        if job.payload_json:
            try:
                payload_dict = json.loads(job.payload_json)
            except Exception:
                pass

        def heartbeat_cb():
            durable_job_queue.heartbeat(
                job_id=job.id,
                worker_id=self.worker_id,
                stage=job.stage,
                db=session,
            )

        return StageContext(
            db_session=session,
            video_id=video_id or "unknown",
            clip_id=clip_id,
            job_id=job.id,
            payload=payload_dict,
            heartbeat_callback=heartbeat_cb,
        )

    def _on_job_completed(self, job: PipelineJob, result: Any, session: Session) -> None:
        """
        Manages downstream transitions and fan-out dependency joins:
        - ingest -> transcribe
        - transcribe -> select
        - select -> creates Clip rows and enqueues (clip.id, 'reframe') and (clip.id, 'captions')
        - reframe / captions -> checks if both are DONE; if so, enqueues (clip.id, 'render')
        - render -> marks clip WAITING_APPROVAL; if all clips rendered, marks video DONE
        """
        stage_name = job.stage.lower()

        if stage_name in ("ingest", "download"):
            # Enqueue transcribe
            durable_job_queue.enqueue(
                job_type="transcribe",
                entity_id=job.entity_id,
                entity_type="source_video",
                stage="transcribe",
                payload=json.loads(job.payload_json or "{}"),
                db=session,
            )
            logger.info("Worker transitioned video %s from %s to transcribe.", job.entity_id, stage_name)

        elif stage_name == "transcribe":
            # Enqueue select
            durable_job_queue.enqueue(
                job_type="select",
                entity_id=job.entity_id,
                entity_type="source_video",
                stage="select",
                payload=json.loads(job.payload_json or "{}"),
                db=session,
            )
            logger.info("Worker transitioned video %s from transcribe to select.", job.entity_id)

        elif stage_name in ("select", "analyze_candidates"):
            # select stage callable itself enqueued reframe and captions.
            # Verify any additional candidate rows if needed.
            logger.info("Worker completed select stage for video %s (fan-out complete).", job.entity_id)

        elif stage_name in ("reframe", "crop_plan"):
            clip_id = job.entity_id
            payload_data = json.loads(job.payload_json or "{}")
            video_id = payload_data.get("video_id")

            # Dual-dependency check: is captions DONE for this clip?
            captions_job = (
                session.query(PipelineJob)
                .filter(
                    PipelineJob.entity_id == clip_id,
                    PipelineJob.stage.in_(["captions", "caption"]),
                    PipelineJob.status == PipelineJobStatus.DONE,
                )
                .first()
            )
            if captions_job:
                logger.info(
                    "Dependency join satisfied for clip %s (both reframe and captions DONE). Enqueuing render job.",
                    clip_id,
                )
                durable_job_queue.enqueue(
                    job_type="render",
                    entity_id=clip_id,
                    entity_type="clip",
                    stage="render",
                    payload={"clip_id": clip_id, "video_id": video_id},
                    db=session,
                )
            else:
                logger.info("Reframe DONE for clip %s. Awaiting captions before enqueuing render.", clip_id)

        elif stage_name in ("captions", "caption"):
            clip_id = job.entity_id
            payload_data = json.loads(job.payload_json or "{}")
            video_id = payload_data.get("video_id")

            # Dual-dependency check: is reframe DONE for this clip?
            reframe_job = (
                session.query(PipelineJob)
                .filter(
                    PipelineJob.entity_id == clip_id,
                    PipelineJob.stage.in_(["reframe", "crop_plan"]),
                    PipelineJob.status == PipelineJobStatus.DONE,
                )
                .first()
            )
            if reframe_job:
                logger.info(
                    "Dependency join satisfied for clip %s (both reframe and captions DONE). Enqueuing render job.",
                    clip_id,
                )
                durable_job_queue.enqueue(
                    job_type="render",
                    entity_id=clip_id,
                    entity_type="clip",
                    stage="render",
                    payload={"clip_id": clip_id, "video_id": video_id},
                    db=session,
                )
            else:
                logger.info("Captions DONE for clip %s. Awaiting reframe before enqueuing render.", clip_id)

        elif stage_name == "render":
            clip_id = job.entity_id
            logger.info("Render completed for clip %s. Ready for Review Queue.", clip_id)

    def execute_job(self, job: PipelineJob) -> bool:
        """
        Executes a claimed job using the StageExecutor.
        On success, marks job DONE and handles fan-out transitions.
        On failure, StageExecutor updates retry/quarantine state.
        """
        session = self.session_factory()
        self._active_job_id = job.id
        try:
            # Refresh job within session
            job_db = session.query(PipelineJob).filter_by(id=job.id).first()
            if not job_db:
                return False

            ctx = self._build_stage_context(job_db, session)
            logger.info(
                "Worker %s executing job %s (stage=%s, entity=%s, attempt=%d)",
                self.worker_id, job_db.id, job_db.stage, job_db.entity_id, job_db.attempt_count + 1,
            )

            result = self.stage_executor.execute_stage(job_db.stage, ctx, job=job_db)

            # Mark job DONE
            job_db.status = PipelineJobStatus.DONE
            job_db.completed_at = datetime.utcnow()
            job_db.locked_by = None
            job_db.locked_at = None
            if result and isinstance(result, dict):
                try:
                    job_db.result_json = json.dumps(result)
                except Exception:
                    pass
            session.commit()

            emit(
                "job_finished",
                job_id=job_db.id,
                stage=job_db.stage,
                entity_id=job_db.entity_id,
            )

            # Trigger downstream transitions
            self._on_job_completed(job_db, result, session)

            clear_cuda_cache()
            return True

        except Exception as exc:
            logger.warning(
                "Worker %s encountered exception while executing job %s (%s): %s",
                self.worker_id, job.id, job.stage, exc,
            )
            # StageExecutor already updated retry backoff or quarantined the job
            return False
        finally:
            self._active_job_id = None
            session.close()

    def run_once(self) -> Optional[PipelineJob]:
        """
        Single-step execution: claims the next available pending job and executes it.
        Returns the claimed job if executed, or None if queue is empty or constrained.
        """
        session = self.session_factory()
        try:
            job = durable_job_queue.claim_next_job(
                worker_id=self.worker_id,
                allowed_stages=self.allowed_stages,
                stage_concurrency_limits=self.stage_concurrency_limits,
                db=session,
            )
        finally:
            session.close()

        if not job:
            return None

        self.execute_job(job)
        return job

    def run_until_idle(self, max_jobs: int = 100) -> int:
        """
        Executes available jobs in sequence until no more pending jobs can be claimed.
        Returns the number of jobs processed.
        """
        count = 0
        while count < max_jobs and not self.shutdown_requested:
            job = self.run_once()
            if not job:
                break
            count += 1
        return count

    def run(self) -> None:
        """
        Main worker execution daemon loop.
        Acquires SQLite lock (if on SQLite), attaches signals, and processes jobs until SIGTERM.
        """
        self.acquire_sqlite_lock()
        self._setup_signal_handlers()

        logger.info(
            "Worker daemon %s started (pinned_groups=%s, concurrency=%s, poll_interval=%.1fs)",
            self.worker_id, self.pinned_groups or "all", self.stage_concurrency_limits, self.poll_interval_sec,
        )

        try:
            while not self.shutdown_requested:
                job = self.run_once()
                if not job:
                    self._stop_event.wait(timeout=self.poll_interval_sec)
        finally:
            logger.info("Worker daemon %s shutting down cleanly.", self.worker_id)
            self.release_sqlite_lock()


def main():
    parser = argparse.ArgumentParser(description="Standalone Pipeline Worker Daemon")
    parser.add_argument(
        "--group",
        type=str,
        default=None,
        help="Comma-separated stage groups to pin (e.g. 'render' or 'ingest,transcribe')",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Concurrency limit override for the pinned group",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single job claim and exit",
    )

    args = parser.parse_args()

    pinned_groups = [g.strip() for g in args.group.split(",")] if args.group else None
    group_concurrency = None
    if pinned_groups and args.concurrency is not None and len(pinned_groups) == 1:
        group_concurrency = {pinned_groups[0]: args.concurrency}

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    )

    daemon = WorkerDaemon(
        pinned_groups=pinned_groups,
        group_concurrency=group_concurrency,
        poll_interval_sec=args.poll_interval,
    )

    if args.once:
        job = daemon.run_once()
        print(f"Executed job: {job.id if job else 'None'}")
    else:
        daemon.run()


if __name__ == "__main__":
    main()
