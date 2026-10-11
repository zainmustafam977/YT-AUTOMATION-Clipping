"""
Crashed-Worker Recovery Reaper and Heartbeat System.
Resets 'running' jobs whose locked_at / heartbeat_at is stale back to 'pending'.
Workers update heartbeat_at and locked_at periodically so long-running operations are not reaped.
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from sqlalchemy.orm import Session

from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import PipelineJob, PipelineJobStatus

logger = logging.getLogger(__name__)


def reap_stale_jobs(
    stale_threshold_sec: int = 600,
    db: Optional[Session] = None,
    session_factory = SessionLocal,
) -> List[PipelineJob]:
    """
    Periodic reaper routine: resets 'running' rows whose locked_at or heartbeat_at
    is older than stale_threshold_sec back to 'pending'.
    Workers heartbeat (updating locked_at/heartbeat_at) every few minutes so long
    renders are not falsely reaped.
    """
    now = datetime.utcnow()
    should_close = False
    if db is None:
        db = session_factory()
        should_close = True

    try:
        running_jobs = (
            db.query(PipelineJob)
            .filter(PipelineJob.status == PipelineJobStatus.RUNNING)
            .all()
        )

        reaped_jobs: List[PipelineJob] = []

        for job in running_jobs:
            # Check last recorded activity timestamp
            last_active = job.heartbeat_at or job.locked_at or job.updated_at

            is_stale = False
            if last_active is None:
                is_stale = True
            else:
                elapsed_sec = (now - last_active).total_seconds()
                if elapsed_sec >= stale_threshold_sec:
                    is_stale = True

            if is_stale:
                previous_worker = job.locked_by or "unknown"
                logger.warning(
                    "Reaper recovering crashed worker lease for job %s (worker: %s, inactive for >= %ds). Resetting to 'pending'.",
                    job.id, previous_worker, stale_threshold_sec,
                )
                job.status = PipelineJobStatus.PENDING
                job.locked_by = None
                job.locked_at = None
                job.next_run_at = now  # Eligible for immediate pickup by a healthy worker
                reaped_jobs.append(job)

        if reaped_jobs:
            db.commit()
            for r_job in reaped_jobs:
                db.refresh(r_job)

        return reaped_jobs
    finally:
        if should_close:
            db.close()


def worker_heartbeat(
    job_id: str,
    worker_id: Optional[str] = None,
    db: Optional[Session] = None,
    session_factory = SessionLocal,
) -> bool:
    """
    Updates heartbeat_at and locked_at for a running job to prevent the reaper from resetting it.
    """
    should_close = False
    if db is None:
        db = session_factory()
        should_close = True

    try:
        job = db.query(PipelineJob).filter_by(id=job_id).first()
        if not job or job.status != PipelineJobStatus.RUNNING:
            return False

        if worker_id and job.locked_by and job.locked_by != worker_id:
            logger.warning("Heartbeat rejected for job %s: owned by %s, not %s", job_id, job.locked_by, worker_id)
            return False

        now = datetime.utcnow()
        job.heartbeat_at = now
        job.locked_at = now
        db.commit()
        return True
    finally:
        if should_close:
            db.close()


class ReaperDaemon:
    """Background thread running the reaper routine periodically."""

    def __init__(
        self,
        interval_sec: float = 60.0,
        stale_threshold_sec: int = 600,
        session_factory = SessionLocal,
    ):
        self.interval_sec = interval_sec
        self.stale_threshold_sec = stale_threshold_sec
        self.session_factory = session_factory
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="ReaperDaemon")
        self._thread.start()
        logger.info("ReaperDaemon started (interval=%ds, threshold=%ds).", self.interval_sec, self.stale_threshold_sec)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info("ReaperDaemon stopped.")

    def run_once(self) -> List[PipelineJob]:
        return reap_stale_jobs(
            stale_threshold_sec=self.stale_threshold_sec,
            session_factory=self.session_factory,
        )

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as e:
                logger.error("Error during reaper execution: %s", e)
            self._stop_event.wait(self.interval_sec)
