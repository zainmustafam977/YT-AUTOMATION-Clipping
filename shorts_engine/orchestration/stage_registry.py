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
from shorts_engine.config.settings import settings
from shorts_engine.orchestration.exceptions import (
    PipelineError,
    TransientError,
    PermanentError,
    DailyBudgetExceededError,
    JobCancelledError,
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
    video_id: Optional[str] = None
    clip_id: Optional[str] = None
    job_id: Optional[str] = None
    paths: Dict[str, Path] = field(default_factory=dict)
    config: Dict[str, Any] = field(default_factory=dict)
    payload: Dict[str, Any] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    attempt_count: int = 1
    heartbeat_callback: Optional[Callable[[], None]] = None

    @property
    def db(self) -> Session:
        """Alias for db_session."""
        return self.db_session

    @property
    def entity_id(self) -> Optional[str]:
        """Resolves target entity ID from clip_id or video_id."""
        return self.clip_id or self.video_id

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
    from shorts_engine.services.ingestion.downloader import IngestionService

    ctx.heartbeat()
    video = ctx.db_session.query(SourceVideo).filter_by(youtube_video_id=ctx.video_id).first()

    # Check for local file
    if video and video.downloaded_path and Path(video.downloaded_path).is_file():
        if video.status != SourceVideoStatus.DONE:
            video.status = SourceVideoStatus.TRANSCRIBING
            ctx.db_session.commit()
        return {"video_path": video.downloaded_path, "audio_path": video.audio_path or video.downloaded_path}

    try:
        ingestion = IngestionService()
        res = ingestion.download_video(ctx.video_id)
        if video:
            video.status = SourceVideoStatus.TRANSCRIBING
            ctx.db_session.commit()
        return {"video_path": str(res.video_path), "audio_path": str(res.audio_path)}
    except Exception as e:
        raise classify_exception(e)


def transcribe_stage(ctx: StageContext) -> Dict[str, Any]:
    """Stage 2: Audio Transcription."""
    from shorts_engine.services.transcription.engine import TranscriptionService

    ctx.heartbeat()
    video = ctx.db_session.query(SourceVideo).filter_by(youtube_video_id=ctx.video_id).first()
    audio_path = (
        ctx.artifacts.get("download", {}).get("audio_path")
        or ctx.artifacts.get("ingest", {}).get("audio_path")
        or (video.audio_path if video else None)
        or (video.downloaded_path if video else None)
    )
    if not audio_path or not Path(audio_path).exists():
        raise PermanentError(f"Missing audio file for transcription: {audio_path}")

    try:
        service = TranscriptionService()
        transcript = service.transcribe(Path(audio_path), video_id=ctx.video_id)
        trans_dict = transcript.to_dict()

        if video:
            existing_t = ctx.db_session.query(Transcript).filter_by(source_video_id=video.id).first()
            if not existing_t:
                new_t = Transcript(
                    source_video_id=video.id,
                    word_level_json=json.dumps(trans_dict),
                    language=transcript.language,
                )
                ctx.db_session.add(new_t)
            video.status = SourceVideoStatus.ANALYZING
            ctx.db_session.commit()

        return {"transcript": trans_dict, "language": transcript.language}
    except Exception as e:
        raise classify_exception(e)


def analyze_candidates_stage(ctx: StageContext) -> Dict[str, Any]:
    """Stage 3: LLM Candidate Discovery & Virality Scoring with Clip Fan-out."""
    from shorts_engine.services.clip_selection.selector import ClipSelectionService
    from shorts_engine.services.transcription.formatter import FormattedTranscript
    from shorts_engine.services.queue.durable_queue import durable_job_queue

    ctx.heartbeat()
    trans_data = (
        ctx.artifacts.get("transcribe", {}).get("transcript")
        or ctx.artifacts.get("transcript")
    )
    video = ctx.db_session.query(SourceVideo).filter_by(youtube_video_id=ctx.video_id).first()
    if not trans_data and video:
        t_row = ctx.db_session.query(Transcript).filter_by(source_video_id=video.id).first()
        if t_row and t_row.raw_transcript:
            try:
                trans_data = json.loads(t_row.raw_transcript)
            except Exception:
                pass

    if not trans_data:
        t_file = Path(settings.STORAGE_DIR) / "transcripts" / f"{ctx.video_id}.json"
        if t_file.exists():
            try:
                trans_data = json.loads(t_file.read_text())
            except Exception:
                pass

    if not trans_data:
        raise PermanentError("Cannot analyze candidates without transcript data.")

    if isinstance(trans_data, FormattedTranscript):
        transcript_obj = trans_data
    elif isinstance(trans_data, (dict, list)):
        try:
            transcript_obj = FormattedTranscript.from_dict(trans_data)
        except Exception:
            transcript_obj = trans_data
    else:
        transcript_obj = trans_data

    try:
        service = ClipSelectionService()
        video_dur = None
        if video and video.duration_sec and video.duration_sec > 0:
            video_dur = float(video.duration_sec)
        elif ctx.artifacts.get("download", {}).get("duration_sec"):
            video_dur = float(ctx.artifacts["download"]["duration_sec"])
        elif video and video.downloaded_path and Path(video.downloaded_path).exists():
            from shorts_engine.services.ingestion.downloader import Downloader
            try:
                video_dur = float(Downloader().probe_duration_sec(Path(video.downloaded_path)))
            except Exception:
                video_dur = None

        if hasattr(service, "select_clips"):
            candidates = service.select_clips(
                transcript_obj,
                video_duration_sec=video_dur,
                video_id=ctx.video_id,
            )
        elif hasattr(service, "select_candidates"):
            candidates = service.select_candidates(
                transcript_obj,
                video_id=ctx.video_id,
                video_duration_sec=video_dur,
            )
        else:
            candidates = []

        if not candidates:
            logger.warning(
                "Clip candidate discovery completed with 0 candidates for video %s.",
                ctx.video_id,
            )
        candidates_out = []

        for cand in candidates:
            c_dict = cand.to_dict() if hasattr(cand, "to_dict") else dict(cand)
            candidates_out.append(c_dict)

            if video:
                start_ms = c_dict.get("start_ms", 0)
                end_ms = c_dict.get("end_ms", 30000)
                cand_db = ctx.db_session.query(ClipCandidate).filter_by(
                    source_video_id=video.id,
                    start_ms=start_ms,
                    end_ms=end_ms,
                ).first()
                if not cand_db:
                    score_val = float(c_dict.get("virality_score", c_dict.get("score", 0.0)) or 0.0)
                    reason_val = str(c_dict.get("reasoning", c_dict.get("reason", "")) or "")
                    cand_db = ClipCandidate(
                        source_video_id=video.id,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        score=score_val,
                        reason=reason_val,
                        hook_text=c_dict.get("hook_text"),
                        payoff_text=c_dict.get("payoff_text"),
                    )
                    ctx.db_session.add(cand_db)
                    ctx.db_session.commit()
                    ctx.db_session.refresh(cand_db)

                clip = ctx.db_session.query(Clip).filter_by(clip_candidate_id=cand_db.id).first()
                if not clip:
                    clip = Clip(
                        clip_candidate_id=cand_db.id,
                        status=ClipStatus.RENDERING,
                        is_sensitive=bool(getattr(video, "is_sensitive", False)),
                    )
                    ctx.db_session.add(clip)
                    ctx.db_session.commit()
                    ctx.db_session.refresh(clip)

                # Fan-out: Enqueue per-clip jobs (reframe and captions)
                durable_job_queue.enqueue(
                    job_type="reframe",
                    entity_id=clip.id,
                    entity_type="clip",
                    stage="reframe",
                    payload={"clip_id": clip.id, "video_id": ctx.video_id},
                    db=ctx.db_session,
                )
                durable_job_queue.enqueue(
                    job_type="captions",
                    entity_id=clip.id,
                    entity_type="clip",
                    stage="captions",
                    payload={"clip_id": clip.id, "video_id": ctx.video_id},
                    db=ctx.db_session,
                )

        if video:
            video.status = SourceVideoStatus.SEGMENTS_SELECTED
            ctx.db_session.commit()

        return {"candidates": candidates_out}
    except Exception as e:
        raise classify_exception(e)


def crop_plan_stage(ctx: StageContext) -> Dict[str, Any]:
    """Stage 4: Reframing & Crop Plan."""
    from shorts_engine.services.reframing.cropper import VideoReframer
    from shorts_engine.services.storage.path_manager import PathManager

    ctx.heartbeat()
    clip = None
    cand = ctx.payload.get("candidate", {})
    start_ms = cand.get("start_ms", 0)
    end_ms = cand.get("end_ms", 30000)

    if ctx.clip_id:
        clip = ctx.db_session.query(Clip).filter_by(id=ctx.clip_id).first()
        if clip and clip.clip_candidate:
            start_ms = clip.clip_candidate.start_ms
            end_ms = clip.clip_candidate.end_ms
            if not ctx.video_id and clip.clip_candidate.source_video:
                ctx.video_id = clip.clip_candidate.source_video.youtube_video_id

    video = ctx.db_session.query(SourceVideo).filter_by(youtube_video_id=ctx.video_id).first()
    video_path = (
        ctx.artifacts.get("download", {}).get("video_path")
        or ctx.artifacts.get("ingest", {}).get("video_path")
        or (video.downloaded_path if video else None)
    )
    if not video_path or not Path(video_path).exists():
        raise PermanentError(f"Master source video missing for crop planning: {video_path}")

    try:
        reframer = VideoReframer()
        plan = reframer.compute_crop_plan(Path(video_path), start_ms=start_ms, end_ms=end_ms)
        plan_dict = plan.to_dict() if hasattr(plan, "to_dict") else plan
        if ctx.clip_id:
            crops_dir = Path(settings.STORAGE_DIR) / "crops"
            crops_dir.mkdir(parents=True, exist_ok=True)
            plan_file = crops_dir / f"{ctx.clip_id}_crop_plan.json"
            plan_file.write_text(json.dumps(plan_dict))
        return {"crop_plan": plan_dict, "clip_id": ctx.clip_id}
    except Exception as e:
        raise classify_exception(e)


def caption_stage(ctx: StageContext) -> Dict[str, Any]:
    """Stage 5: Subtitle & Kinetic Caption Generation."""
    from shorts_engine.services.captioning.ass_generator import ASSSubtitleGenerator
    from shorts_engine.services.storage.path_manager import PathManager
    from shorts_engine.services.transcription.formatter import WordSegment

    ctx.heartbeat()
    clip = None
    cand = ctx.payload.get("candidate", {})
    start_ms = cand.get("start_ms", 0)
    end_ms = cand.get("end_ms", 30000)

    if ctx.clip_id:
        clip = ctx.db_session.query(Clip).filter_by(id=ctx.clip_id).first()
        if clip and clip.clip_candidate:
            start_ms = clip.clip_candidate.start_ms
            end_ms = clip.clip_candidate.end_ms
            if not ctx.video_id and clip.clip_candidate.source_video:
                ctx.video_id = clip.clip_candidate.source_video.youtube_video_id

    sub_path = PathManager.get_subtitle_path(ctx.video_id, start_ms, end_ms)

    trans_data = ctx.artifacts.get("transcribe", {}).get("transcript", {})
    words = trans_data.get("words", [])
    if not words and ctx.db_session:
        video = ctx.db_session.query(SourceVideo).filter_by(youtube_video_id=ctx.video_id).first()
        if video:
            t_row = ctx.db_session.query(Transcript).filter_by(source_video_id=video.id).first()
            json_str = getattr(t_row, "word_level_json", None) or getattr(t_row, "raw_transcript", None)
            if json_str:
                try:
                    words = json.loads(json_str).get("words", [])
                except Exception:
                    pass

    if not words:
        raise PermanentError("No words found in transcript for caption generation.")

    # Normalize words: handles both live WordSegment objects and raw dictionaries from queue/DB
    word_objs = [WordSegment.from_dict(w) if isinstance(w, dict) else w for w in words]

    try:
        generator = ASSSubtitleGenerator()
        generator.generate_ass(words=word_objs, segment_start_ms=start_ms, segment_end_ms=end_ms, output_path=sub_path)
        return {"subtitle_path": str(sub_path), "clip_id": ctx.clip_id}
    except Exception as e:
        raise classify_exception(e)


def render_stage(ctx: StageContext) -> Dict[str, Any]:
    """Stage 6: Video Composition & Mastering."""
    from shorts_engine.services.rendering.compositor import VideoCompositor
    from shorts_engine.services.storage.path_manager import PathManager

    ctx.heartbeat()
    clip = None
    cand = ctx.payload.get("candidate", {})
    start_ms = cand.get("start_ms", 0)
    end_ms = cand.get("end_ms", 30000)

    if ctx.clip_id:
        clip = ctx.db_session.query(Clip).filter_by(id=ctx.clip_id).first()
        if clip and clip.clip_candidate:
            start_ms = clip.clip_candidate.start_ms
            end_ms = clip.clip_candidate.end_ms
            if not ctx.video_id and clip.clip_candidate.source_video:
                ctx.video_id = clip.clip_candidate.source_video.youtube_video_id

    video = ctx.db_session.query(SourceVideo).filter_by(youtube_video_id=ctx.video_id).first()
    video_path = (
        ctx.artifacts.get("download", {}).get("video_path")
        or ctx.artifacts.get("ingest", {}).get("video_path")
        or (video.downloaded_path if video else None)
    )
    if not video_path or not Path(video_path).exists():
        raise PermanentError(f"Master video missing for render stage: {video_path}")

    try:
        compositor = VideoCompositor()
        crop_plan = (
            ctx.artifacts.get("crop_plan", {}).get("crop_plan")
            or ctx.artifacts.get("reframe", {}).get("crop_plan")
        )
        if not crop_plan and ctx.clip_id:
            crops_dir = Path(settings.STORAGE_DIR) / "crops"
            plan_file = crops_dir / f"{ctx.clip_id}_crop_plan.json"
            if plan_file.exists():
                try:
                    crop_plan = json.loads(plan_file.read_text())
                except Exception:
                    pass

        if crop_plan and isinstance(crop_plan, dict):
            from shorts_engine.services.reframing.cropper import CropPlan
            try:
                crop_plan = CropPlan.from_dict(crop_plan)
            except Exception as parse_err:
                logger.warning("Failed reconstructing CropPlan from dict: %s", parse_err)

        sub_path = (
            ctx.artifacts.get("caption", {}).get("subtitle_path")
            or ctx.artifacts.get("captions", {}).get("subtitle_path")
        )
        if not sub_path and ctx.video_id:
            cand_sub = PathManager.get_subtitle_path(ctx.video_id, start_ms, end_ms)
            if cand_sub.exists():
                sub_path = str(cand_sub)

        out_name = f"renders/{ctx.video_id}_{start_ms}.mp4" if not ctx.clip_id else f"renders/{ctx.clip_id}.mp4"
        rendered = compositor.render_short(
            source_video_path=Path(video_path),
            start_ms=start_ms,
            end_ms=end_ms,
            crop_plan=crop_plan,
            subtitle_ass_path=Path(sub_path) if sub_path else None,
            output_filename=out_name,
        )

        if clip:
            clip.rendered_path = str(rendered)
            clip.status = ClipStatus.WAITING_APPROVAL
            ctx.db_session.commit()

            if video:
                all_clips = (
                    ctx.db_session.query(Clip)
                    .join(ClipCandidate)
                    .filter(ClipCandidate.source_video_id == video.id)
                    .all()
                )
                if all_clips and all(c.status != ClipStatus.RENDERING for c in all_clips):
                    video.status = SourceVideoStatus.DONE
                    ctx.db_session.commit()

        return {"rendered_path": str(rendered), "clip_id": ctx.clip_id}
    except Exception as e:
        raise classify_exception(e)


def publish_stage(ctx: StageContext) -> Dict[str, Any]:
    """Stage 7: YouTube Publishing via Publish Worker."""
    from shorts_engine.services.publishing.worker import YouTubePublishWorker

    ctx.heartbeat()
    worker = YouTubePublishWorker()
    job_entity = None
    if ctx.job_id:
        job_entity = ctx.db.query(PipelineJob).filter_by(id=ctx.job_id).first()
    try:
        return worker.execute_publish_intent(
            clip_id=ctx.entity_id or ctx.video_id,
            payload=ctx.payload,
            db=ctx.db,
            job_entity=job_entity,
        )
    except Exception as e:
        raise classify_exception(e)


def metadata_update_stage(ctx: StageContext) -> Dict[str, Any]:
    """Metadata Update Stage via Publish Worker."""
    from shorts_engine.services.publishing.worker import YouTubePublishWorker

    ctx.heartbeat()
    worker = YouTubePublishWorker()
    try:
        return worker.execute_metadata_update_intent(
            clip_id=ctx.entity_id or ctx.video_id,
            payload=ctx.payload,
            db=ctx.db,
        )
    except Exception as e:
        raise classify_exception(e)


def analytics_poll_stage(ctx: StageContext) -> Dict[str, Any]:
    """Hourly Analytics Polling Stage via AnalyticsPoller."""
    from shorts_engine.services.analytics.poller import AnalyticsPoller

    ctx.heartbeat()
    poller = AnalyticsPoller()
    lookback = ctx.payload.get("lookback_days", 7)
    try:
        summary = poller.poll_all_published_clips(db=ctx.db_session, lookback_days=lookback)
        interval_hours = float(ctx.payload.get("scheduled_interval_hours", 1.0))
        poller.schedule_next_poll(db=ctx.db_session, delay_hours=interval_hours)
        return summary
    except Exception as e:
        raise classify_exception(e)


def daily_cleanup_stage(ctx: StageContext) -> Dict[str, Any]:
    """Daily Storage Retention Policy & Disk Maintenance Stage."""
    from shorts_engine.services.storage.retention_service import RetentionPolicyService
    from shorts_engine.services.queue.durable_queue import durable_job_queue

    ctx.heartbeat()
    dry_run = bool(ctx.payload.get("dry_run", False))
    try:
        summary = RetentionPolicyService.run_cleanup(db=ctx.db_session, dry_run=dry_run)

        # Self-schedule next daily cleanup in 24 hours
        import uuid
        interval_days = int(ctx.payload.get("interval_days", 1))
        next_run = datetime.utcnow() + timedelta(days=interval_days)
        cleanup_id = f"cleanup_{uuid.uuid4().hex[:12]}"
        durable_job_queue.enqueue(
            job_type="daily_cleanup",
            entity_id=cleanup_id,
            entity_type="system",
            stage="daily_cleanup",
            next_run_at=next_run,
            payload={"action": "retention_cleanup", "interval_days": interval_days},
            db=ctx.db_session,
        )
        return summary
    except Exception as e:
        raise classify_exception(e)


# The canonical PIPELINE registry mapping
PIPELINE: Dict[str, Stage] = {
    "ingest": Stage(
        name="ingest",
        callable=download_stage,
        produces="{storage_dir}/downloads/{video_id}.mp4",
        max_attempts=3,
    ),
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
    "select": Stage(
        name="select",
        callable=analyze_candidates_stage,
        produces="{storage_dir}/analysis/{video_id}_candidates.json",
        max_attempts=3,
    ),
    "analyze_candidates": Stage(
        name="analyze_candidates",
        callable=analyze_candidates_stage,
        produces="{storage_dir}/analysis/{video_id}_candidates.json",
        max_attempts=3,
    ),
    "reframe": Stage(
        name="reframe",
        callable=crop_plan_stage,
        produces="{storage_dir}/crops/{clip_id}_crop_plan.json",
        max_attempts=3,
    ),
    "crop_plan": Stage(
        name="crop_plan",
        callable=crop_plan_stage,
        produces="{storage_dir}/crops/{clip_id}_crop_plan.json",
        max_attempts=3,
    ),
    "captions": Stage(
        name="captions",
        callable=caption_stage,
        produces="{storage_dir}/subtitles/{clip_id}.ass",
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
    "metadata_update": Stage(
        name="metadata_update",
        callable=metadata_update_stage,
        produces=None,
        max_attempts=3,
    ),
    "analytics_poll": Stage(
        name="analytics_poll",
        callable=analytics_poll_stage,
        produces=None,
        max_attempts=3,
    ),
    "daily_cleanup": Stage(
        name="daily_cleanup",
        callable=daily_cleanup_stage,
        produces=None,
        max_attempts=3,
    ),
    "cleanup": Stage(
        name="cleanup",
        callable=daily_cleanup_stage,
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

        except DailyBudgetExceededError as budget_err:
            error_msg = str(budget_err)
            logger.warning(
                "Daily LLM budget exceeded in stage '%s': %s (deferring job to tomorrow)",
                stage.name, error_msg,
            )
            tomorrow = (datetime.utcnow() + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            if job:
                job.status = PipelineJobStatus.PENDING
                job.next_run_at = tomorrow
                job.last_error = error_msg
                job.locked_by = None
                if job.attempt_count > 0:
                    job.attempt_count -= 1
                ctx.db_session.commit()

            try:
                from shorts_engine.services.events.taxonomy import emit_platform_event, BUDGET_EXCEEDED
                from shorts_engine.services.monitoring.token_tracker import token_tracker
                emit_platform_event(
                    event_name=BUDGET_EXCEEDED,
                    payload={
                        "job_id": job.id if job else (ctx.job_id or "job"),
                        "stage": stage.name,
                        "error": error_msg,
                        "deferred_until": tomorrow.isoformat(),
                        "cost_today": token_tracker.get_cost_today(db=ctx.db_session),
                        "budget_usd": settings.LLM_DAILY_BUDGET_USD,
                    },
                    entity_id=job.id if job else ctx.job_id,
                    entity_type="job",
                    level="WARNING",
                    title=f"LLM Budget Exceeded in Stage: {stage.name}",
                    message=f"Job {job.id[:8] if job else 'job'} deferred to tomorrow: {error_msg}",
                    db=ctx.db_session,
                )
            except Exception as emit_err:
                logger.debug("Failed to emit budget_exceeded event: %s", emit_err)

            try:
                from shorts_engine.services.monitoring.alerter import pipeline_alerter
                from shorts_engine.services.monitoring.token_tracker import token_tracker
                pipeline_alerter.alert(
                    title=f"LLM Budget Exceeded: Stage {stage.name}",
                    message=f"Daily LLM budget exceeded. Job deferred until {tomorrow.isoformat()}: {error_msg}",
                    level="WARNING",
                    context={
                        "alert_type": "budget_exceeded",
                        "job_id": job.id if job else ctx.job_id,
                        "stage": stage.name,
                        "cost_today": token_tracker.get_cost_today(db=ctx.db_session),
                        "budget_usd": settings.LLM_DAILY_BUDGET_USD,
                    },
                )
            except Exception as alert_err:
                logger.debug("Failed to dispatch budget alert: %s", alert_err)

            emit(
                "job_retried",
                job_id=job.id if job else (ctx.job_id or "job"),
                attempt_count=job.attempt_count if job else ctx.attempt_count,
                next_run_at=tomorrow,
                error=error_msg,
                stage=stage.name,
            )
            raise budget_err

        except JobCancelledError as cancel_err:
            error_msg = str(cancel_err)
            logger.info("Job %s was cancelled during stage '%s': %s", job.id if job else (ctx.job_id or "job"), stage.name, error_msg)
            if job:
                job.status = PipelineJobStatus.CANCELLED
                job.last_error = error_msg
                job.completed_at = datetime.utcnow()
                job.locked_by = None
                job.locked_at = None
                ctx.db_session.commit()
            emit(
                "job_cancelled",
                job_id=job.id if job else (ctx.job_id or "job"),
                last_error=error_msg,
                stage=stage.name,
                attempt_count=job.attempt_count if job else ctx.attempt_count,
            )
            raise cancel_err

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
