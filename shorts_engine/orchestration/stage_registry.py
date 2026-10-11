"""
Stage Registry and Thin Pipeline Executor.
Implements:
1. Stage dataclass with {name, callable, produces, max_attempts}
2. PIPELINE dict of stages
3. StageContext for unit-testable stage execution
4. Thin StageExecutor with exponential backoff + jitter and failure taxonomy
5. Structured emit() telemetry integration
6. Quarantined jobs visibility query
"""

import os
import json
import random
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Any, Dict, Optional, List
from sqlalchemy.orm import Session

from shorts_engine.db.models import PipelineJob, PipelineJobStatus, SourceVideo, Clip
from shorts_engine.orchestration.exceptions import (
    PipelineError,
    TransientError,
    PermanentError,
    classify_exception,
)
from shorts_engine.orchestration.telemetry import emit, get_telemetry_emitter

logger = logging.getLogger(__name__)


@dataclass
class StageContext:
    """
    Context object passed to each stage callable.
    Contains database session, paths, configuration, and shared artifacts.
    Makes stage callables independently unit-testable.
    """
    db_session: Session
    video_id: str
    clip_id: Optional[str] = None
    job_id: Optional[str] = None
    paths: Dict[str, Path] = field(default_factory=dict)
    config: Dict[str, Any] = field(default_factory=dict)
    payload: Dict[str, Any] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    attempt_count: int = 1
    heartbeat_callback: Optional[Callable[[], None]] = None

    def heartbeat(self) -> None:
        """Invokes worker heartbeat to update locked_at / heartbeat_at."""
        if self.heartbeat_callback:
            try:
                self.heartbeat_callback()
            except Exception as e:
                logger.debug("Heartbeat callback warning: %s", e)


@dataclass
class Stage:
    """Data representation of a single pipeline stage."""
    name: str
    callable: Callable[[StageContext], Any]
    produces: Optional[str] = None  # Artifact path template, e.g. "{storage_dir}/downloads/{video_id}.mp4"
    max_attempts: int = 3


def calculate_backoff_run_at(
    attempt_count: int,
    base: float = 10.0,
    jitter_max: float = 3.0,
    now: Optional[datetime] = None,
) -> datetime:
    """
    Computes scheduled retry time with exponential backoff and jitter:
    run_at = now + base * 2^attempt_count + jitter
    """
    current_time = now or datetime.utcnow()
    jitter = random.uniform(0.0, jitter_max) if jitter_max > 0 else 0.0
    delay_sec = base * (2 ** attempt_count) + jitter
    return current_time + timedelta(seconds=delay_sec)


# --- Default Pipeline Stage Callables ---

def download_stage(ctx: StageContext) -> Dict[str, Any]:
    """Stage 1: Download & Ingestion."""
    from shorts_engine.config.settings import settings
    from shorts_engine.services.ingestion.downloader import IngestionService

    ctx.heartbeat()
    ingestion = IngestionService(db_session=ctx.db_session)
    video = ctx.db_session.query(SourceVideo).filter_by(youtube_video_id=ctx.video_id).first()

    # Check for local file
    if video and video.downloaded_path and Path(video.downloaded_path).is_file():
        return {"video_path": video.downloaded_path, "audio_path": video.audio_path}

    try:
        res = ingestion.download_video(ctx.video_id)
        return {"video_path": str(res.video_path), "audio_path": str(res.audio_path)}
    except Exception as e:
        raise classify_exception(e)


def transcribe_stage(ctx: StageContext) -> Dict[str, Any]:
    """Stage 2: Audio Transcription."""
    from shorts_engine.services.transcription.engine import TranscriptionService

    ctx.heartbeat()
    video = ctx.db_session.query(SourceVideo).filter_by(youtube_video_id=ctx.video_id).first()
    audio_path = ctx.artifacts.get("download", {}).get("audio_path") or (video.audio_path if video else None)
    if not audio_path or not Path(audio_path).exists():
        raise PermanentError(f"Missing audio file for transcription: {audio_path}")

    try:
        service = TranscriptionService()
        transcript = service.transcribe(Path(audio_path), video_id=ctx.video_id)
        return {"transcript": transcript.to_dict(), "language": transcript.language}
    except Exception as e:
        raise classify_exception(e)


def analyze_candidates_stage(ctx: StageContext) -> Dict[str, Any]:
    """Stage 3: LLM Candidate Discovery & Virality Scoring."""
    from shorts_engine.services.clip_selection.selector import ClipSelectionService
    from shorts_engine.services.transcription.formatter import FormattedTranscript

    ctx.heartbeat()
    trans_data = ctx.artifacts.get("transcribe", {}).get("transcript")
    if not trans_data:
        raise PermanentError("Cannot analyze candidates without transcript data.")

    try:
        service = ClipSelectionService()
        transcript = FormattedTranscript.from_dict(trans_data)
        candidates = service.select_candidates(transcript, video_id=ctx.video_id)
        return {"candidates": [c.to_dict() if hasattr(c, "to_dict") else dict(c) for c in candidates]}
    except Exception as e:
        raise classify_exception(e)


def crop_plan_stage(ctx: StageContext) -> Dict[str, Any]:
    """Stage 4: Reframing & Crop Plan."""
    from shorts_engine.services.reframing.cropper import VideoReframer

    ctx.heartbeat()
    video = ctx.db_session.query(SourceVideo).filter_by(youtube_video_id=ctx.video_id).first()
    video_path = ctx.artifacts.get("download", {}).get("video_path") or (video.downloaded_path if video else None)
    if not video_path or not Path(video_path).exists():
        raise PermanentError(f"Master source video missing for crop planning: {video_path}")

    try:
        reframer = VideoReframer()
        cand = ctx.payload.get("candidate", {})
        start_ms = cand.get("start_ms", 0)
        end_ms = cand.get("end_ms", 30000)
        plan = reframer.compute_crop_plan(Path(video_path), start_ms=start_ms, end_ms=end_ms)
        return {"crop_plan": plan}
    except Exception as e:
        raise classify_exception(e)


def caption_stage(ctx: StageContext) -> Dict[str, Any]:
    """Stage 5: Subtitle & Kinetic Caption Generation."""
    from shorts_engine.services.captioning.ass_generator import ASSSubtitleGenerator
    from shorts_engine.services.storage.path_manager import PathManager

    ctx.heartbeat()
    cand = ctx.payload.get("candidate", {})
    start_ms = cand.get("start_ms", 0)
    end_ms = cand.get("end_ms", 30000)
    sub_path = PathManager.get_subtitle_path(ctx.video_id, start_ms, end_ms)

    trans_data = ctx.artifacts.get("transcribe", {}).get("transcript", {})
    words = trans_data.get("words", [])
    if not words:
        raise PermanentError("No words found in transcript for caption generation.")

    try:
        generator = ASSSubtitleGenerator()
        generator.generate_ass(words=words, segment_start_ms=start_ms, segment_end_ms=end_ms, output_path=sub_path)
        return {"subtitle_path": str(sub_path)}
    except Exception as e:
        raise classify_exception(e)


def render_stage(ctx: StageContext) -> Dict[str, Any]:
    """Stage 6: Video Composition & Mastering."""
    from shorts_engine.services.rendering.compositor import VideoCompositor

    ctx.heartbeat()
    video = ctx.db_session.query(SourceVideo).filter_by(youtube_video_id=ctx.video_id).first()
    video_path = ctx.artifacts.get("download", {}).get("video_path") or (video.downloaded_path if video else None)
    if not video_path or not Path(video_path).exists():
        raise PermanentError(f"Master video missing for render stage: {video_path}")

    try:
        compositor = VideoCompositor()
        cand = ctx.payload.get("candidate", {})
        crop_plan = ctx.artifacts.get("crop_plan", {}).get("crop_plan")
        sub_path = ctx.artifacts.get("caption", {}).get("subtitle_path")

        out_name = f"renders/{ctx.video_id}_{cand.get('start_ms', 0)}.mp4"
        rendered = compositor.render_short(
            source_video_path=Path(video_path),
            start_ms=cand.get("start_ms", 0),
            end_ms=cand.get("end_ms", 30000),
            crop_plan=crop_plan,
            subtitle_ass_path=Path(sub_path) if sub_path else None,
            output_filename=out_name,
        )
        return {"rendered_path": str(rendered)}
    except Exception as e:
        raise classify_exception(e)


def publish_stage(ctx: StageContext) -> Dict[str, Any]:
    """Stage 7: YouTube Publishing."""
    from shorts_engine.services.publishing.client import YouTubePublisher

    ctx.heartbeat()
    rendered_path = ctx.artifacts.get("render", {}).get("rendered_path")
    if not rendered_path or not Path(rendered_path).exists():
        raise PermanentError(f"Rendered video missing for publishing: {rendered_path}")

    try:
        publisher = YouTubePublisher()
        res = publisher.publish_short(
            video_path=Path(rendered_path),
            title=ctx.payload.get("title", f"Short {ctx.video_id}"),
            description=ctx.payload.get("description", ""),
        )
        return {"published_id": res.get("id"), "url": res.get("url")}
    except Exception as e:
        raise classify_exception(e)


# The canonical PIPELINE registry mapping
PIPELINE: Dict[str, Stage] = {
    "download": Stage(
        name="download",
        callable=download_stage,
        produces="{storage_dir}/downloads/{video_id}.mp4",
        max_attempts=3,
    ),
    "transcribe": Stage(
        name="transcribe",
        callable=transcribe_stage,
        produces="{storage_dir}/transcripts/{video_id}.json",
        max_attempts=3,
    ),
    "analyze_candidates": Stage(
        name="analyze_candidates",
        callable=analyze_candidates_stage,
        produces="{storage_dir}/analysis/{video_id}_candidates.json",
        max_attempts=3,
    ),
    "crop_plan": Stage(
        name="crop_plan",
        callable=crop_plan_stage,
        produces="{storage_dir}/crops/{clip_id}_crop_plan.json",
        max_attempts=3,
    ),
    "caption": Stage(
        name="caption",
        callable=caption_stage,
        produces="{storage_dir}/subtitles/{clip_id}.ass",
        max_attempts=3,
    ),
    "render": Stage(
        name="render",
        callable=render_stage,
        produces="{storage_dir}/renders/{clip_id}.mp4",
        max_attempts=3,
    ),
    "publish": Stage(
        name="publish",
        callable=publish_stage,
        produces=None,
        max_attempts=3,
    ),
}


class StageExecutor:
    """
    Thin pipeline executor that walks the stage registry.
    Handles:
    - Independent unit-testable stage callables
    - TransientError retry with exponential backoff and jitter
    - PermanentError immediate quarantine (zero retries)
    - Exhausted attempts quarantine
    - Structured emit() notifications
    """

    def __init__(
        self,
        pipeline: Optional[Dict[str, Stage]] = None,
        base_backoff_sec: float = 10.0,
        jitter_max_sec: float = 3.0,
    ):
        self.pipeline = pipeline if pipeline is not None else PIPELINE
        self.base_backoff_sec = base_backoff_sec
        self.jitter_max_sec = jitter_max_sec

    def execute_stage(
        self,
        stage_name: str,
        ctx: StageContext,
        job: Optional[PipelineJob] = None,
    ) -> Any:
        """
        Executes a single named stage with full taxonomy handling.
        """
        if stage_name not in self.pipeline:
            raise PermanentError(f"Stage '{stage_name}' not registered in PIPELINE.")

        stage = self.pipeline[stage_name]
        stage_max_attempts = stage.max_attempts or 3

        # Update heartbeat and job state if job attached
        now = datetime.utcnow()
        if job:
            job.stage = stage.name
            job.heartbeat_at = now
            job.locked_at = now
            # Increment attempt counter on each execution run
            job.attempt_count += 1
            ctx.attempt_count = job.attempt_count

        try:
            # Execute callable
            result = stage.callable(ctx)
            ctx.artifacts[stage.name] = result

            if job:
                job.last_error = None
                ctx.db_session.commit()

            return result

        except PermanentError as perm_err:
            error_msg = str(perm_err)
            logger.error("Permanent error in stage '%s': %s (quarantining immediately)", stage.name, error_msg)

            if job:
                job.status = PipelineJobStatus.QUARANTINED
                job.last_error = error_msg
                job.completed_at = datetime.utcnow()
                job.locked_by = None
                ctx.db_session.commit()

            emit(
                "job_quarantined",
                job_id=job.id if job else (ctx.job_id or "job"),
                last_error=error_msg,
                stage=stage.name,
                attempt_count=job.attempt_count if job else ctx.attempt_count,
            )
            raise perm_err

        except Exception as exc:
            # Unknown exceptions treated as TransientError
            trans_err = classify_exception(exc)
            if not isinstance(trans_err, TransientError):
                # If classify found permanent signature
                if job:
                    job.status = PipelineJobStatus.QUARANTINED
                    job.last_error = str(trans_err)
                    job.completed_at = datetime.utcnow()
                    job.locked_by = None
                    ctx.db_session.commit()
                emit(
                    "job_quarantined",
                    job_id=job.id if job else (ctx.job_id or "job"),
                    last_error=str(trans_err),
                    stage=stage.name,
                    attempt_count=job.attempt_count if job else ctx.attempt_count,
                )
                raise trans_err

            error_msg = str(trans_err)
            current_attempts = job.attempt_count if job else ctx.attempt_count

            if current_attempts >= stage_max_attempts:
                # Retries exhausted -> Quarantine
                logger.error(
                    "Stage '%s' exhausted max attempts (%d/%d): quarantining job. Reason: %s",
                    stage.name, current_attempts, stage_max_attempts, error_msg,
                )
                if job:
                    job.status = PipelineJobStatus.QUARANTINED
                    job.last_error = error_msg
                    job.completed_at = datetime.utcnow()
                    job.locked_by = None
                    ctx.db_session.commit()

                emit(
                    "job_quarantined",
                    job_id=job.id if job else (ctx.job_id or "job"),
                    last_error=error_msg,
                    stage=stage.name,
                    attempt_count=current_attempts,
                )
            else:
                # Schedule retry with exponential backoff and jitter
                next_run = calculate_backoff_run_at(
                    attempt_count=current_attempts,
                    base=self.base_backoff_sec,
                    jitter_max=self.jitter_max_sec,
                )
                logger.warning(
                    "Transient error in stage '%s' (attempt %d/%d). Scheduled retry at %s: %s",
                    stage.name, current_attempts, stage_max_attempts, next_run, error_msg,
                )

                if job:
                    job.status = PipelineJobStatus.PENDING
                    job.next_run_at = next_run
                    job.last_error = error_msg
                    job.locked_by = None
                    job.locked_at = None
                    ctx.db_session.commit()

                emit(
                    "job_retried",
                    job_id=job.id if job else (ctx.job_id or "job"),
                    attempt_count=current_attempts,
                    next_run_at=next_run,
                    error=error_msg,
                    stage=stage.name,
                )

            raise trans_err

    def run_pipeline(
        self,
        ctx: StageContext,
        stage_names: Optional[List[str]] = None,
        job: Optional[PipelineJob] = None,
    ) -> Dict[str, Any]:
        """
        Walks the registry stages in sequence.
        Transitions job to 'done' when all stages succeed.
        """
        stages_to_run = stage_names if stage_names is not None else list(self.pipeline.keys())

        if job:
            job.status = PipelineJobStatus.RUNNING
            job.locked_at = datetime.utcnow()
            ctx.db_session.commit()

        for st_name in stages_to_run:
            self.execute_stage(st_name, ctx, job=job)

        # All stages finished successfully
        if job:
            job.status = PipelineJobStatus.DONE
            job.stage = "COMPLETED"
            job.completed_at = datetime.utcnow()
            job.locked_by = None
            job.locked_at = None
            ctx.db_session.commit()

        emit(
            "job_finished",
            job_id=job.id if job else (ctx.job_id or "job"),
            stage="DONE",
            result=ctx.artifacts,
        )

        return ctx.artifacts


def get_quarantined_jobs(db: Session, limit: int = 50, offset: int = 0) -> List[PipelineJob]:
    """
    Queries quarantined jobs from the database, ordered by latest updated_at.
    """
    return (
        db.query(PipelineJob)
        .filter(PipelineJob.status.in_([PipelineJobStatus.QUARANTINED, PipelineJobStatus.DEAD_LETTER]))
        .order_by(PipelineJob.updated_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
