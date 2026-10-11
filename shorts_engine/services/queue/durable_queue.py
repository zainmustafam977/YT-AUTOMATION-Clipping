"""
Durable, Transactional Job Queue Engine.
Manages persistent jobs with dialect-aware locking, exponential backoff retries,
Dead-Letter Queue (DLQ), stage checkpoint resumption, and worker lease reconciliation.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import or_

from shorts_engine.db.session import SessionLocal, is_postgres
from shorts_engine.db.models import PipelineJob, PipelineJobStatus
from shorts_engine.services.worker.runner import generate_worker_id, is_claim_stale
from shorts_engine.services.telemetry.progress_tracker import job_progress_tracker
from shorts_engine.services.events.bus import global_event_bus
from shorts_engine.services.events.models import PlatformEvent

logger = logging.getLogger(__name__)


class DurableJobQueue:
    """
    Production-grade durable job queue backed by SQLite / PostgreSQL.
    Guarantees persistence, crash-safety, exponential backoff, and DLQ handling.
    """

    def __init__(self, session_factory=SessionLocal):
        self.session_factory = session_factory

    def _get_session(self, db: Optional[Session] = None) -> Tuple[Session, bool]:
        if db is not None:
            return db, False
        return self.session_factory(), True

    def enqueue(
        self,
        job_type: str,
        entity_id: str,
        entity_type: str = "source_video",
        payload: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
        backoff_multiplier_sec: float = 10.0,
        next_run_at: Optional[datetime] = None,
        db: Optional[Session] = None,
    ) -> PipelineJob:
        """
        Enqueues a new job or returns the active existing job for the same (job_type, entity_id).
        """
        session, should_close = self._get_session(db)
        try:
            # Check for existing active job (idempotent deduplication)
            active_job = (
                session.query(PipelineJob)
                .filter(
                    PipelineJob.job_type == job_type,
                    PipelineJob.entity_id == entity_id,
                    PipelineJob.status.in_([PipelineJobStatus.PENDING, PipelineJobStatus.RUNNING]),
                )
                .first()
            )
            if active_job:
                logger.info("Found active existing job %s for %s:%s; skipping duplicate enqueue.", active_job.id, job_type, entity_id)
                return active_job

            job = PipelineJob(
                job_type=job_type,
                entity_type=entity_type,
                entity_id=entity_id,
                status=PipelineJobStatus.PENDING,
                stage="QUEUED",
                progress_pct=0.0,
                progress_detail="Job enqueued, awaiting worker pickup.",
                payload_json=json.dumps(payload or {}),
                max_retries=max_retries,
                backoff_multiplier_sec=backoff_multiplier_sec,
                next_run_at=next_run_at,
            )
            session.add(job)
            session.commit()
            session.refresh(job)

            logger.info("Enqueued %s job %s for entity %s", job_type, job.id, entity_id)

            try:
                global_event_bus.publish_sync(PlatformEvent(
                    category="queue",
                    level="INFO",
                    title="Job Enqueued",
                    message=f"Job {job.id[:8]} ({job_type}) enqueued for {entity_type} {entity_id}.",
                    entity_id=job.id,
                    entity_type="pipeline_job",
                ))
            except Exception:
                pass

            return job
        finally:
            if should_close:
                session.close()

    def claim_next_job(
        self,
        worker_id: Optional[str] = None,
        job_type: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> Optional[PipelineJob]:
        """
        Atomically claims the next pending job whose next_run_at is now or in the past.
        - PostgreSQL: Uses SELECT ... FOR UPDATE SKIP LOCKED.
        - SQLite: Uses atomic Compare-And-Swap (CAS).
        """
        wid = worker_id or generate_worker_id()
        now = datetime.utcnow()
        session, should_close = self._get_session(db)

        try:
            query = session.query(PipelineJob).filter(
                PipelineJob.status == PipelineJobStatus.PENDING,
                or_(PipelineJob.next_run_at.is_(None), PipelineJob.next_run_at <= now),
            )
            if job_type:
                query = query.filter(PipelineJob.job_type == job_type)

            query = query.order_by(PipelineJob.created_at.asc())

            if is_postgres(session.get_bind()):
                # PostgreSQL: row-level skip-locked claim
                job = query.with_for_update(skip_locked=True).first()
                if job:
                    job.status = PipelineJobStatus.RUNNING
                    job.locked_by = wid
                    job.locked_at = now
                    job.heartbeat_at = now
                    session.commit()
                    session.refresh(job)
                    logger.info("Worker %s claimed job %s (%s)", wid, job.id, job.job_type)
                    return job
                return None
            else:
                # SQLite: atomic CAS update
                candidate = query.first()
                if not candidate:
                    return None

                rows_updated = (
                    session.query(PipelineJob)
                    .filter(
                        PipelineJob.id == candidate.id,
                        PipelineJob.status == PipelineJobStatus.PENDING,
                    )
                    .update(
                        {
                            PipelineJob.status: PipelineJobStatus.RUNNING,
                            PipelineJob.locked_by: wid,
                            PipelineJob.locked_at: now,
                            PipelineJob.heartbeat_at: now,
                        },
                        synchronize_session=False,
                    )
                )
                session.commit()

                if rows_updated > 0:
                    session.refresh(candidate)
                    logger.info("Worker %s claimed job %s (%s)", wid, candidate.id, candidate.job_type)
                    return candidate
                return None
        finally:
            if should_close:
                session.close()

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        progress_pct: Optional[float] = None,
        progress_detail: Optional[str] = None,
        stage: Optional[str] = None,
        speed: Optional[str] = None,
        eta_seconds: Optional[float] = None,
        db: Optional[Session] = None,
    ) -> bool:
        """
        Refreshes worker heartbeat timestamp and persists granular progress in the database.
        Also mirrors telemetry to JobProgressTracker and EventBus.
        """
        session, should_close = self._get_session(db)
        try:
            job = session.query(PipelineJob).filter(PipelineJob.id == job_id).first()
            if not job or job.status != PipelineJobStatus.RUNNING:
                return False

            now = datetime.utcnow()
            job.heartbeat_at = now

            if progress_pct is not None:
                job.progress_pct = max(0.0, min(100.0, round(float(progress_pct), 1)))
            if progress_detail is not None:
                job.progress_detail = progress_detail
            if stage is not None:
                job.stage = stage

            session.commit()

            # Mirror to in-memory progress tracker for live web sockets
            if job.entity_type == "source_video":
                job_progress_tracker.update_progress(
                    video_id=job.entity_id,
                    stage=job.stage,
                    percent=job.progress_pct,
                    detail=job.progress_detail or "",
                    speed=speed,
                    eta_seconds=eta_seconds,
                )

            return True
        finally:
            if should_close:
                session.close()

    def complete_job(
        self,
        job_id: str,
        result: Optional[Dict[str, Any]] = None,
        db: Optional[Session] = None,
    ) -> bool:
        """Marks a job as COMPLETED, clears locks, and records outputs."""
        session, should_close = self._get_session(db)
        try:
            job = session.query(PipelineJob).filter(PipelineJob.id == job_id).first()
            if not job:
                return False

            now = datetime.utcnow()
            job.status = PipelineJobStatus.COMPLETED
            job.stage = "COMPLETED"
            job.progress_pct = 100.0
            job.completed_at = now
            job.locked_by = None
            if result is not None:
                job.result_json = json.dumps(result)

            session.commit()
            logger.info("Job %s (%s) marked COMPLETED.", job.id, job.job_type)

            try:
                global_event_bus.publish_sync(PlatformEvent(
                    category="queue",
                    level="INFO",
                    title="Job Completed",
                    message=f"Job {job.id[:8]} ({job.job_type}) completed successfully.",
                    entity_id=job.id,
                    entity_type="pipeline_job",
                ))
            except Exception:
                pass

            return True
        finally:
            if should_close:
                session.close()

    def fail_job(
        self,
        job_id: str,
        error_message: str,
        db: Optional[Session] = None,
    ) -> PipelineJob:
        """
        Handles job failure with exponential backoff or Dead-Letter Queue (DLQ) transition.
        """
        session, should_close = self._get_session(db)
        try:
            job = session.query(PipelineJob).filter(PipelineJob.id == job_id).first()
            if not job:
                raise ValueError(f"Job {job_id} not found.")

            now = datetime.utcnow()
            job.retry_count += 1
            job.error_message = str(error_message)[:2000]

            if job.retry_count < job.max_retries:
                # Exponential backoff: delay = multiplier * 2^(retry_count - 1)
                delay_sec = job.backoff_multiplier_sec * (2 ** (job.retry_count - 1))
                job.status = PipelineJobStatus.PENDING
                job.next_run_at = now + timedelta(seconds=delay_sec)
                job.locked_by = None
                job.locked_at = None
                job.stage = f"RETRY_BACKOFF_{job.retry_count}"
                logger.warning(
                    "Job %s failed (attempt %d/%d). Scheduled for retry in %.1fs at %s: %s",
                    job.id, job.retry_count, job.max_retries, delay_sec, job.next_run_at, error_message,
                )
                level = "WARNING"
                event_title = f"Job Retry Scheduled ({job.retry_count}/{job.max_retries})"
            else:
                # Retries exhausted -> Dead-Letter Queue (DLQ)
                job.status = PipelineJobStatus.DEAD_LETTER
                job.locked_by = None
                job.completed_at = now
                job.stage = "DEAD_LETTER"
                logger.error(
                    "Job %s exhausted all %d retries -> Moved to DEAD_LETTER queue. Reason: %s",
                    job.id, job.max_retries, error_message,
                )
                level = "ERROR"
                event_title = "Job Moved to Dead-Letter Queue (DLQ)"

            session.commit()
            session.refresh(job)

            try:
                global_event_bus.publish_sync(PlatformEvent(
                    category="queue",
                    level=level,
                    title=event_title,
                    message=f"Job {job.id[:8]} ({job.job_type}): {str(error_message)[:200]}",
                    entity_id=job.id,
                    entity_type="pipeline_job",
                ))
            except Exception:
                pass

            return job
        finally:
            if should_close:
                session.close()

    def requeue_dead_letter_job(self, job_id: str, db: Optional[Session] = None) -> bool:
        """Operator action: manually resets a DLQ job back to PENDING with retry counter reset."""
        session, should_close = self._get_session(db)
        try:
            job = session.query(PipelineJob).filter(PipelineJob.id == job_id).first()
            if not job or job.status != PipelineJobStatus.DEAD_LETTER:
                return False

            job.status = PipelineJobStatus.PENDING
            job.retry_count = 0
            job.next_run_at = datetime.utcnow()
            job.locked_by = None
            job.locked_at = None
            job.error_message = None
            job.stage = "REQUEUED"
            session.commit()
            logger.info("Operator requeued Dead-Letter job %s.", job_id)
            return True
        finally:
            if should_close:
                session.close()

    def reconcile_orphaned_jobs(self, stale_timeout_sec: int = 600, db: Optional[Session] = None) -> int:
        """
        Finds jobs in RUNNING status whose worker lease has expired or whose worker is dead.
        Reclaims them via fail_job so they are automatically rescheduled via backoff or moved to DLQ.
        """
        now = datetime.utcnow()
        session, should_close = self._get_session(db)
        reclaimed_count = 0

        try:
            running_jobs = (
                session.query(PipelineJob)
                .filter(PipelineJob.status == PipelineJobStatus.RUNNING)
                .all()
            )

            for job in running_jobs:
                is_stale = False
                last_time = job.heartbeat_at or job.locked_at or job.updated_at

                if job.locked_by:
                    if is_claim_stale(job.locked_by, last_time, now, stale_timeout_sec=stale_timeout_sec):
                        logger.info("Job %s lease (held by %s) declared stale.", job.id, job.locked_by)
                        is_stale = True
                elif last_time:
                    age = (now - last_time).total_seconds()
                    if age > stale_timeout_sec:
                        is_stale = True

                if is_stale:
                    self.fail_job(job.id, f"Worker lease expired (inactive > {stale_timeout_sec}s)", db=session)
                    reclaimed_count += 1

            return reclaimed_count
        finally:
            if should_close:
                session.close()


# Global singleton instance
durable_job_queue = DurableJobQueue()
