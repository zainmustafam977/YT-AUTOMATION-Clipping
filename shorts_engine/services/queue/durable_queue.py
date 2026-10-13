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
from sqlalchemy.exc import IntegrityError

from shorts_engine.db.session import SessionLocal, is_postgres
from shorts_engine.db.models import PipelineJob, PipelineJobStatus
from shorts_engine.services.worker.runner import generate_worker_id, is_claim_stale
from shorts_engine.services.telemetry.progress_tracker import job_progress_tracker
from shorts_engine.services.events.bus import global_event_bus
from shorts_engine.services.events.models import PlatformEvent

logger = logging.getLogger(__name__)


# Single Table of Truth for Stage Aliases & Canonical Mapping
CANONICAL_STAGE_MAP: Dict[str, str] = {
    "download": "ingest",
    "ingest": "ingest",
    "transcribe": "transcribe",
    "analyze_candidates": "select",
    "select": "select",
    "crop_plan": "reframe",
    "reframe": "reframe",
    "caption": "captions",
    "captions": "captions",
    "render": "render",
    "publish": "publish",
    "metadata_update": "metadata_update",
    "daily_health": "daily_health",
    "analytics_poll": "analytics_poll",
    "daily_cleanup": "daily_cleanup",
    "cleanup": "cleanup",
}

STAGE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "ingest": ("ingest", "download"),
    "transcribe": ("transcribe",),
    "select": ("select", "analyze_candidates"),
    "reframe": ("reframe", "crop_plan"),
    "captions": ("captions", "caption"),
    "render": ("render",),
    "publish": ("publish",),
}


def canonicalize_stage_name(stage: Optional[str]) -> str:
    """Normalizes any stage alias or raw name into its canonical identifier."""
    if not stage:
        return "unknown"
    s = stage.strip().lower()
    return CANONICAL_STAGE_MAP.get(s, s)


class DurableJobQueue:
    """
    Production-grade durable job queue backed by SQLite / PostgreSQL.
    Guarantees persistence, crash-safety, exponential backoff, and DLQ handling.
    """

    def __init__(self, session_factory=SessionLocal):
        self.session_factory = session_factory
        self.reconciled_jobs_count: int = 0

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
        stage: Optional[str] = None,
        commit: bool = True,
    ) -> PipelineJob:
        """
        Enqueues a new job or returns the existing job for the same (entity_id, stage).
        Idempotent and deduplicated at both application and unique constraint level.
        Supports commit=False for enclosing callers managing multi-operation atomic transactions.
        """
        session, should_close = self._get_session(db)
        known_stages = {
            "ingest", "download", "transcribe", "select", "analyze_candidates",
            "reframe", "crop_plan", "captions", "caption", "render", "publish",
            "metadata_update", "daily_health", "analytics_poll",
            "daily_cleanup", "cleanup",
        }
        if stage:
            eff_stage = stage.lower()
        elif job_type.lower() in known_stages:
            eff_stage = job_type.lower()
        else:
            eff_stage = "QUEUED"

        try:
            # Check for existing job (idempotent deduplication via UNIQUE(entity_id, stage))
            if eff_stage != "QUEUED":
                existing_job = (
                    session.query(PipelineJob)
                    .filter(
                        PipelineJob.entity_id == entity_id,
                        PipelineJob.stage == eff_stage,
                    )
                    .first()
                )
            else:
                existing_job = (
                    session.query(PipelineJob)
                    .filter(
                        PipelineJob.job_type == job_type,
                        PipelineJob.entity_id == entity_id,
                        PipelineJob.status.in_([PipelineJobStatus.PENDING, PipelineJobStatus.RUNNING]),
                    )
                    .first()
                )

            if existing_job:
                logger.info("Found existing job %s for %s:%s; skipping duplicate enqueue.", existing_job.id, entity_id, eff_stage)
                return existing_job

            job = PipelineJob(
                job_type=job_type,
                entity_type=entity_type,
                entity_id=entity_id,
                status=PipelineJobStatus.PENDING,
                stage=eff_stage,
                progress_pct=0.0,
                progress_detail=f"Job enqueued for stage '{eff_stage}', awaiting worker pickup.",
                payload_json=json.dumps(payload or {}),
                max_retries=max_retries,
                backoff_multiplier_sec=backoff_multiplier_sec,
                next_run_at=next_run_at,
            )
            session.add(job)
            if commit:
                try:
                    session.commit()
                    session.refresh(job)
                except IntegrityError:
                    session.rollback()
                    conflict_job = (
                        session.query(PipelineJob)
                        .filter(
                            PipelineJob.entity_id == entity_id,
                            PipelineJob.stage == eff_stage,
                        )
                        .first()
                    )
                    if conflict_job:
                        return conflict_job
                    raise
            else:
                session.flush()
                session.refresh(job)

            logger.info("Enqueued %s job %s (stage=%s) for entity %s", job_type, job.id, eff_stage, entity_id)

            try:
                global_event_bus.publish_sync(PlatformEvent(
                    category="queue",
                    level="INFO",
                    title="Job Enqueued",
                    message=f"Job {job.id[:8]} ({job_type}/{eff_stage}) enqueued for {entity_type} {entity_id}.",
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
        allowed_stages: Optional[List[str]] = None,
        stage_concurrency_limits: Optional[Dict[str, int]] = None,
    ) -> Optional[PipelineJob]:
        """
        Atomically claims the next pending job whose next_run_at is now or in the past.
        Supports stage pinning (allowed_stages) and stage group concurrency limits.
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
            if allowed_stages:
                query = query.filter(or_(PipelineJob.stage.in_(allowed_stages), PipelineJob.job_type.in_(allowed_stages)))

            # Check stage group concurrency limits
            if stage_concurrency_limits:
                for limit_stage, max_concur in stage_concurrency_limits.items():
                    active_count = (
                        session.query(PipelineJob)
                        .filter(
                            PipelineJob.stage == limit_stage,
                            PipelineJob.status == PipelineJobStatus.RUNNING,
                        )
                        .count()
                    )
                    if active_count >= max_concur:
                        query = query.filter(PipelineJob.stage != limit_stage)

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
            job.locked_at = now

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
            job.attempt_count = max(getattr(job, "attempt_count", 0) + 1, job.retry_count)
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

    def quarantine_job(
        self,
        job_id: str,
        error_message: str,
        db: Optional[Session] = None,
    ) -> PipelineJob:
        """
        Immediately quarantines a job (0 retries burned or unrecoverable error).
        The job is NEVER retried automatically.
        """
        session, should_close = self._get_session(db)
        try:
            job = session.query(PipelineJob).filter(PipelineJob.id == job_id).first()
            if not job:
                raise ValueError(f"Job {job_id} not found.")

            now = datetime.utcnow()
            job.status = PipelineJobStatus.QUARANTINED
            job.error_message = str(error_message)[:2000]
            job.locked_by = None
            job.locked_at = None
            job.completed_at = now
            job.stage = "QUARANTINED"

            session.commit()
            session.refresh(job)
            logger.error("Job %s immediately QUARANTINED: %s", job.id, error_message)

            try:
                global_event_bus.publish_sync(PlatformEvent(
                    category="queue",
                    level="ERROR",
                    title="Job Quarantined",
                    message=f"Job {job.id[:8]} ({job.job_type}) quarantined: {str(error_message)[:200]}",
                    entity_id=job.id,
                    entity_type="pipeline_job",
                ))
            except Exception:
                pass

            return job
        finally:
            if should_close:
                session.close()

    def get_quarantined_jobs(
        self,
        limit: int = 50,
        offset: int = 0,
        db: Optional[Session] = None,
    ) -> List[PipelineJob]:
        """Queries all quarantined / dead-letter jobs with their error details."""
        session, should_close = self._get_session(db)
        try:
            return (
                session.query(PipelineJob)
                .filter(PipelineJob.status.in_([PipelineJobStatus.QUARANTINED, PipelineJobStatus.DEAD_LETTER]))
                .order_by(PipelineJob.updated_at.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
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

            # Sweep downstream orphans
            try:
                self.reconcile_downstream_orphans(db=session)
            except Exception as sweep_err:
                logger.warning("Downstream orphan reconciliation sweep failed: %s", sweep_err)

            return reclaimed_count
        finally:
            if should_close:
                session.close()

    def _emit_reconciled_event(
        self,
        job: PipelineJob,
        downstream_stage: str,
        upstream_stage: str,
        session: Session,
    ) -> None:
        """Emits job.reconciled event via taxonomy platform event emitter with provided db session."""
        try:
            from shorts_engine.services.events.taxonomy import emit_platform_event, JOB_RECONCILED
            emit_platform_event(
                event_name=JOB_RECONCILED,
                payload={
                    "job_id": job.id,
                    "stage": downstream_stage,
                    "entity_id": job.entity_id,
                    "upstream_stage": upstream_stage,
                },
                entity_id=job.id,
                entity_type="pipeline_job",
                level="INFO",
                title=f"Downstream Job Reconciled: {downstream_stage}",
                message=f"Reconciled missing downstream {downstream_stage} job for {job.entity_id} following {upstream_stage}.",
                db=session,
            )
        except Exception as e:
            logger.debug("Failed emitting job.reconciled event: %s", e)

    def reconcile_downstream_orphans(
        self,
        window_days: int = 7,
        db: Optional[Session] = None,
    ) -> int:
        """
        Sweeps completed jobs in non-terminal entities to reconcile missing downstream jobs.
        Guarantees self-healing across worker crashes, power loss, and concurrent join races.
        """
        from shorts_engine.db.models import (
            SourceVideo,
            SourceVideoStatus,
            ClipCandidate,
            Clip,
            ClipStatus,
        )

        TERMINAL_VIDEO_STATUSES = {
            SourceVideoStatus.DONE,
            SourceVideoStatus.FAILED,
            SourceVideoStatus.PURGED,
            SourceVideoStatus.POLICY_RESTRICTED,
            SourceVideoStatus.PENDING_POLICY_REVIEW,
        }

        TERMINAL_CLIP_STATUSES = {
            ClipStatus.REJECTED,
            ClipStatus.PURGED,
            ClipStatus.FAILED,
            ClipStatus.FAILED_NEEDS_REVIEW,
            ClipStatus.WAITING_APPROVAL,
            ClipStatus.APPROVED,
            ClipStatus.SCHEDULED,
            ClipStatus.PUBLISHING,
            ClipStatus.PUBLISHED,
        }

        session, should_close = self._get_session(db)
        reconciled_count = 0
        cutoff = datetime.utcnow() - timedelta(days=window_days)

        try:
            done_jobs = (
                session.query(PipelineJob)
                .filter(
                    PipelineJob.status == PipelineJobStatus.DONE,
                    or_(PipelineJob.completed_at >= cutoff, PipelineJob.completed_at.is_(None)),
                )
                .order_by(PipelineJob.completed_at.asc())
                .all()
            )

            seen_transitions: set = set()

            for job in done_jobs:
                canonical_stage = canonicalize_stage_name(job.stage)
                transition_key = (job.entity_id, canonical_stage)
                if transition_key in seen_transitions:
                    continue
                seen_transitions.add(transition_key)

                # --- 1. Ingest -> Transcribe ---
                if canonical_stage == "ingest":
                    video = (
                        session.query(SourceVideo)
                        .filter(
                            or_(
                                SourceVideo.youtube_video_id == job.entity_id,
                                SourceVideo.id == job.entity_id,
                            )
                        )
                        .first()
                    )
                    if not video or video.status in TERMINAL_VIDEO_STATUSES:
                        continue

                    transcribe_job = (
                        session.query(PipelineJob)
                        .filter(
                            PipelineJob.entity_id == job.entity_id,
                            PipelineJob.stage.in_(STAGE_ALIASES["transcribe"]),
                        )
                        .first()
                    )
                    if not transcribe_job:
                        payload = {}
                        if job.payload_json:
                            try:
                                payload = json.loads(job.payload_json)
                            except Exception:
                                pass
                        if "file_path" not in payload and video.downloaded_path:
                            payload["file_path"] = str(video.downloaded_path)
                        try:
                            new_job = self.enqueue(
                                job_type="transcribe",
                                entity_id=job.entity_id,
                                entity_type="source_video",
                                stage="transcribe",
                                payload=payload,
                                db=session,
                            )
                            reconciled_count += 1
                            self.reconciled_jobs_count += 1
                            self._emit_reconciled_event(new_job, "transcribe", "ingest", session)
                        except IntegrityError:
                            session.rollback()

                # --- 2. Transcribe -> Select ---
                elif canonical_stage == "transcribe":
                    video = (
                        session.query(SourceVideo)
                        .filter(
                            or_(
                                SourceVideo.youtube_video_id == job.entity_id,
                                SourceVideo.id == job.entity_id,
                            )
                        )
                        .first()
                    )
                    if not video or video.status in TERMINAL_VIDEO_STATUSES:
                        continue

                    select_job = (
                        session.query(PipelineJob)
                        .filter(
                            PipelineJob.entity_id == job.entity_id,
                            PipelineJob.stage.in_(STAGE_ALIASES["select"]),
                        )
                        .first()
                    )
                    if not select_job:
                        payload = {}
                        if job.payload_json:
                            try:
                                payload = json.loads(job.payload_json)
                            except Exception:
                                pass
                        try:
                            new_job = self.enqueue(
                                job_type="select",
                                entity_id=job.entity_id,
                                entity_type="source_video",
                                stage="select",
                                payload=payload,
                                db=session,
                            )
                            reconciled_count += 1
                            self.reconciled_jobs_count += 1
                            self._emit_reconciled_event(new_job, "select", "transcribe", session)
                        except IntegrityError:
                            session.rollback()

                # --- 3. Select -> Reframe & Captions Fan-Out ---
                elif canonical_stage == "select":
                    video = (
                        session.query(SourceVideo)
                        .filter(
                            or_(
                                SourceVideo.youtube_video_id == job.entity_id,
                                SourceVideo.id == job.entity_id,
                            )
                        )
                        .first()
                    )
                    if not video or video.status in TERMINAL_VIDEO_STATUSES:
                        continue

                    clips = (
                        session.query(Clip)
                        .join(ClipCandidate, Clip.clip_candidate_id == ClipCandidate.id)
                        .filter(ClipCandidate.source_video_id == video.id)
                        .all()
                    )
                    for clip in clips:
                        if clip.status in TERMINAL_CLIP_STATUSES:
                            continue

                        reframe_job = (
                            session.query(PipelineJob)
                            .filter(
                                PipelineJob.entity_id == clip.id,
                                PipelineJob.stage.in_(STAGE_ALIASES["reframe"]),
                            )
                            .first()
                        )
                        if not reframe_job:
                            try:
                                new_job = self.enqueue(
                                    job_type="reframe",
                                    entity_id=clip.id,
                                    entity_type="clip",
                                    stage="reframe",
                                    payload={"clip_id": clip.id, "video_id": video.youtube_video_id},
                                    db=session,
                                )
                                reconciled_count += 1
                                self.reconciled_jobs_count += 1
                                self._emit_reconciled_event(new_job, "reframe", "select", session)
                            except IntegrityError:
                                session.rollback()

                        captions_job = (
                            session.query(PipelineJob)
                            .filter(
                                PipelineJob.entity_id == clip.id,
                                PipelineJob.stage.in_(STAGE_ALIASES["captions"]),
                            )
                            .first()
                        )
                        if not captions_job:
                            try:
                                new_job = self.enqueue(
                                    job_type="captions",
                                    entity_id=clip.id,
                                    entity_type="clip",
                                    stage="captions",
                                    payload={"clip_id": clip.id, "video_id": video.youtube_video_id},
                                    db=session,
                                )
                                reconciled_count += 1
                                self.reconciled_jobs_count += 1
                                self._emit_reconciled_event(new_job, "captions", "select", session)
                            except IntegrityError:
                                session.rollback()

                # --- 4. Reframe / Captions Dual-Dependency Join -> Render ---
                elif canonical_stage in ("reframe", "captions"):
                    clip_id = job.entity_id
                    clip = session.query(Clip).filter_by(id=clip_id).first()
                    if not clip or clip.status in TERMINAL_CLIP_STATUSES:
                        continue

                    if clip.clip_candidate and clip.clip_candidate.source_video:
                        if clip.clip_candidate.source_video.status in TERMINAL_VIDEO_STATUSES:
                            continue

                    reframe_jobs = (
                        session.query(PipelineJob)
                        .filter(
                            PipelineJob.entity_id == clip_id,
                            PipelineJob.stage.in_(STAGE_ALIASES["reframe"]),
                        )
                        .all()
                    )
                    captions_jobs = (
                        session.query(PipelineJob)
                        .filter(
                            PipelineJob.entity_id == clip_id,
                            PipelineJob.stage.in_(STAGE_ALIASES["captions"]),
                        )
                        .all()
                    )

                    video_id = None
                    if clip.clip_candidate and clip.clip_candidate.source_video:
                        video_id = clip.clip_candidate.source_video.youtube_video_id

                    # T5: Partial fanout recovery if sibling was completely missing
                    if not reframe_jobs:
                        try:
                            new_job = self.enqueue(
                                job_type="reframe",
                                entity_id=clip.id,
                                entity_type="clip",
                                stage="reframe",
                                payload={"clip_id": clip.id, "video_id": video_id},
                                db=session,
                            )
                            reconciled_count += 1
                            self.reconciled_jobs_count += 1
                            self._emit_reconciled_event(new_job, "reframe", "partial_fanout", session)
                        except IntegrityError:
                            session.rollback()
                        continue

                    if not captions_jobs:
                        try:
                            new_job = self.enqueue(
                                job_type="captions",
                                entity_id=clip.id,
                                entity_type="clip",
                                stage="captions",
                                payload={"clip_id": clip.id, "video_id": video_id},
                                db=session,
                            )
                            reconciled_count += 1
                            self.reconciled_jobs_count += 1
                            self._emit_reconciled_event(new_job, "captions", "partial_fanout", session)
                        except IntegrityError:
                            session.rollback()
                        continue

                    # A3: DONE-ONLY SATISFACTION:
                    reframe_done = any(j.status == PipelineJobStatus.DONE for j in reframe_jobs)
                    captions_done = any(j.status == PipelineJobStatus.DONE for j in captions_jobs)

                    reframe_has_bad = any(
                        j.status in (PipelineJobStatus.FAILED, PipelineJobStatus.DEAD_LETTER, PipelineJobStatus.QUARANTINED)
                        for j in reframe_jobs
                    )
                    captions_has_bad = any(
                        j.status in (PipelineJobStatus.FAILED, PipelineJobStatus.DEAD_LETTER, PipelineJobStatus.QUARANTINED)
                        for j in captions_jobs
                    )

                    if reframe_done and captions_done and not reframe_has_bad and not captions_has_bad:
                        render_job = (
                            session.query(PipelineJob)
                            .filter(
                                PipelineJob.entity_id == clip_id,
                                PipelineJob.stage.in_(STAGE_ALIASES["render"]),
                            )
                            .first()
                        )
                        if not render_job:
                            try:
                                new_job = self.enqueue(
                                    job_type="render",
                                    entity_id=clip_id,
                                    entity_type="clip",
                                    stage="render",
                                    payload={"clip_id": clip.id, "video_id": video_id},
                                    db=session,
                                )
                                reconciled_count += 1
                                self.reconciled_jobs_count += 1
                                self._emit_reconciled_event(new_job, "render", "reframe+captions", session)
                            except IntegrityError:
                                session.rollback()

            return reconciled_count
        finally:
            if should_close:
                session.close()

    def ensure_daily_cleanup_scheduled(self, db: Optional[Session] = None) -> PipelineJob:
        """
        Ensures a pending daily_cleanup job exists in the durable queue.
        If none exists, enqueues a new daily_cleanup job.
        """
        session, should_close = self._get_session(db)
        try:
            existing = (
                session.query(PipelineJob)
                .filter(
                    PipelineJob.stage == "daily_cleanup",
                    PipelineJob.status.in_([PipelineJobStatus.PENDING, PipelineJobStatus.RUNNING]),
                )
                .first()
            )
            if existing:
                return existing

            import uuid
            cleanup_id = f"cleanup_{uuid.uuid4().hex[:12]}"
            return self.enqueue(
                job_type="daily_cleanup",
                entity_id=cleanup_id,
                entity_type="system",
                stage="daily_cleanup",
                payload={"action": "retention_cleanup"},
                db=session,
            )
        finally:
            if should_close:
                session.close()


# Global singleton instance
durable_job_queue = DurableJobQueue()
