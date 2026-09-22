"""
Decoupled Background Worker Runner.
Picks pending clips from database queue with dialect-aware locking (PostgreSQL FOR UPDATE SKIP LOCKED vs SQLite CAS),
flushes CUDA cache between jobs, enforces VRAM limits, and executes rendering & quality checking.
"""

import os
import re
import sys
import time
import json
import uuid
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import or_

from shorts_engine.config.settings import settings
from shorts_engine.config.logging_config import reload_runtime_settings
from shorts_engine.db.session import SessionLocal, is_postgres
from shorts_engine.db.models import Clip, ClipStatus, ClipCandidate, SourceVideo, SourceVideoStatus, Channel
from shorts_engine.services.reframing.cropper import VideoReframer
from shorts_engine.services.captioning.ass_generator import ASSSubtitleGenerator
from shorts_engine.services.captioning.sfx_mapper import SFXMapper
from shorts_engine.services.rendering.compositor import VideoCompositor
from shorts_engine.services.quality_gate.review_queue import QualityGateManager
from shorts_engine.services.transcription.formatter import FormattedTranscript, WordSegment
from shorts_engine.services.monitoring.alerter import PipelineAlerter

logger = logging.getLogger(__name__)


def clear_cuda_cache():
    """Flushes GPU VRAM and IPC memory cache if PyTorch / CUDA is available."""
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
            logger.debug("CUDA memory cache cleared successfully.")
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"Failed to clear CUDA cache: {e}")


def claim_next_clip(db: Session, worker_id: Optional[str] = None) -> Optional[Clip]:
    """
    Atomically claims the next pending clip for processing.
    - PostgreSQL: Uses SELECT ... FOR UPDATE SKIP LOCKED for non-blocking multi-worker concurrency.
    - SQLite: Uses atomic Compare-And-Swap (CAS) to prevent race conditions across local worker threads.
    """
    wid = worker_id or f"worker-{os.getpid()}-{uuid.uuid4().hex[:6]}"

    if is_postgres(db.get_bind()):
        # PostgreSQL: lock row directly at engine level
        clip = (
            db.query(Clip)
            .filter(
                Clip.status == ClipStatus.RENDERING,
                or_(Clip.rendered_path == None, Clip.rendered_path.startswith("CLAIMED:") == False),
            )
            .order_by(Clip.created_at.asc())
            .with_for_update(skip_locked=True)
            .first()
        )
        if clip:
            clip.rendered_path = f"CLAIMED:{wid}"
            db.commit()
            db.refresh(clip)
            return clip
        return None
    else:
        # SQLite: Atomic CAS in immediate transaction
        candidate = (
            db.query(Clip)
            .filter(
                Clip.status == ClipStatus.RENDERING,
                or_(Clip.rendered_path == None, Clip.rendered_path.startswith("CLAIMED:") == False),
            )
            .order_by(Clip.created_at.asc())
            .first()
        )
        if not candidate:
            return None

        # Attempt atomic claim
        current_val = candidate.rendered_path
        claimed_marker = f"CLAIMED:{wid}"
        rows_updated = (
            db.query(Clip)
            .filter(Clip.id == candidate.id, Clip.rendered_path == current_val)
            .update({"rendered_path": claimed_marker}, synchronize_session=False)
        )
        db.commit()

        if rows_updated > 0:
            db.refresh(candidate)
            return candidate
        return None


def is_pid_alive(pid: int) -> bool:
    """
    Checks if a given process ID is actively running.
    Uses psutil if available, with cross-platform OS fallback.
    """
    if pid <= 0:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        pass

    try:
        if os.name == "nt":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


def extract_pid_from_claim(claim_str: Optional[str]) -> Optional[int]:
    """Extracts numeric PID from a 'CLAIMED:runner-<PID>' or 'CLAIMED:worker-<PID>' string."""
    if not claim_str or not isinstance(claim_str, str):
        return None
    m = re.search(r"CLAIMED:(?:runner|worker)-(\d+)", claim_str)
    if m:
        try:
            return int(m.group(1))
        except (ValueError, TypeError):
            return None
    return None


def reconcile_stale_claims(db: Session, stale_timeout_sec: int = 600) -> int:
    """
    Finds clips in RENDERING status that are claimed by terminated worker processes
    or have been claimed longer than stale_timeout_sec without updates, and safely
    resets them so they can be processed cleanly.
    Returns the number of reclaimed clips.
    """
    from datetime import datetime
    now = datetime.utcnow()
    reclaimed = 0

    try:
        rendering_clips = db.query(Clip).filter(Clip.status == ClipStatus.RENDERING).all()
        for clip in rendering_clips:
            is_stale = False
            rpath = clip.rendered_path or ""

            if rpath.startswith("CLAIMED:"):
                pid = extract_pid_from_claim(rpath)
                if pid is not None:
                    if not is_pid_alive(pid):
                        logger.info("Reconciling stale claim on clip %s: worker PID %d is dead.", clip.id, pid)
                        is_stale = True

                if not is_stale and clip.updated_at:
                    age_sec = (now - clip.updated_at).total_seconds()
                    if age_sec > stale_timeout_sec:
                        logger.info("Reconciling stale claim on clip %s: claim age (%.1fs) exceeds timeout.", clip.id, age_sec)
                        is_stale = True

            if is_stale:
                clip.rendered_path = None
                reclaimed += 1

        if reclaimed > 0:
            db.commit()
            logger.info("Stale claim reconciliation completed: %d clip(s) reclaimed.", reclaimed)
    except Exception as e:
        logger.warning("Error during stale claim reconciliation: %s", e)
        db.rollback()

    return reclaimed


def claim_clip_by_id(db: Session, clip_id: str, worker_id: Optional[str] = None) -> Optional[Clip]:
    """
    Atomically claims a specific clip by ID if it is currently unclaimed in RENDERING status
    or claimed by a dead/terminated worker PID.
    - PostgreSQL: Uses SELECT ... FOR UPDATE SKIP LOCKED.
    - SQLite: Uses atomic CAS to guarantee single winner.
    """
    wid = worker_id or f"worker-{os.getpid()}-{uuid.uuid4().hex[:6]}"

    target_clip = db.query(Clip).filter(Clip.id == clip_id, Clip.status == ClipStatus.RENDERING).first()
    if not target_clip:
        return None

    # If already claimed, check if holding PID is alive
    if target_clip.rendered_path and target_clip.rendered_path.startswith("CLAIMED:"):
        held_pid = extract_pid_from_claim(target_clip.rendered_path)
        if held_pid is not None and not is_pid_alive(held_pid):
            logger.info("Stealing/reclaiming clip %s from dead worker PID %s", clip_id, held_pid)
        else:
            # Still held by an active live process or named worker, do not steal
            return None

    current_val = target_clip.rendered_path
    claimed_marker = f"CLAIMED:{wid}"

    if is_postgres(db.get_bind()):
        clip = (
            db.query(Clip)
            .filter(Clip.id == clip_id, Clip.status == ClipStatus.RENDERING)
            .with_for_update(skip_locked=True)
            .first()
        )
        if clip:
            clip.rendered_path = claimed_marker
            db.commit()
            db.refresh(clip)
            return clip
        return None
    else:
        rows_updated = (
            db.query(Clip)
            .filter(Clip.id == target_clip.id, Clip.rendered_path == current_val)
            .update({"rendered_path": claimed_marker}, synchronize_session=False)
        )
        db.commit()

        if rows_updated > 0:
            db.refresh(target_clip)
            return target_clip
        return None


class WorkerRunner:
    """
    Background worker orchestrator. Continuously polls the queue, claims jobs,
    executes reframing, captioning, compositing, and quality verification.
    """

    def __init__(self, session_factory=SessionLocal, worker_id: Optional[str] = None):
        self.session_factory = session_factory
        self.worker_id = worker_id or f"worker-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.reframer = VideoReframer()
        self.ass_generator = ASSSubtitleGenerator()
        self.sfx_mapper = SFXMapper()
        self.compositor = VideoCompositor()
        self.quality_gate = QualityGateManager()
        self.alerter = PipelineAlerter()

    def process_clip(self, clip_id: str) -> bool:
        """Processes a single claimed clip from rendering through quality gate."""
        reload_runtime_settings()
        db = self.session_factory()
        try:
            clear_cuda_cache()
            clip = db.query(Clip).filter_by(id=clip_id).first()
            if not clip:
                logger.error(f"Clip {clip_id} not found in DB")
                return False

            cand = clip.candidate
            if not cand:
                logger.error(f"Candidate not found for clip {clip_id}")
                clip.status = ClipStatus.FAILED
                db.commit()
                return False

            video = cand.source_video
            channel = video.channel

            logger.info(f"Worker {self.worker_id} processing clip {clip.id} (range {cand.start_ms}-{cand.end_ms}ms)")

            # Transcript words
            words = []
            if video.transcript:
                try:
                    raw_data = json.loads(video.transcript.word_level_json)
                    words_data = raw_data.get("words", raw_data) if isinstance(raw_data, dict) else raw_data
                    words = [
                        WordSegment(
                            word=w["word"],
                            start=float(w["start"]),
                            end=float(w["end"]),
                            probability=float(w.get("probability", 1.0)),
                        )
                        for w in words_data
                    ]
                except Exception as e:
                    logger.warning(f"Could not parse transcript words: {e}")

            # Reframing crop plan
            source_path = Path(video.downloaded_path) if video.downloaded_path else None
            if not source_path or not source_path.exists():
                logger.error(f"Source video file missing: {source_path}")
                clip.status = ClipStatus.FAILED
                db.commit()
                return False

            crop_plan = self.reframer.compute_crop_plan(
                source_path,
                start_ms=cand.start_ms,
                end_ms=cand.end_ms,
            )

            # Generate subtitles with template overrides
            from shorts_engine.services.captioning.templates import get_caption_template, parse_custom_template
            from shorts_engine.services.storage.path_manager import PathManager

            caption_preset = getattr(settings, "DEFAULT_CAPTION_PRESET", "hormozi")
            style_overrides = None
            if clip.caption_style:
                try:
                    c_data = json.loads(clip.caption_style)
                    style_overrides = parse_custom_template(c_data).to_ass_overrides()
                except Exception:
                    pass
            if not style_overrides:
                style_overrides = get_caption_template(caption_preset).to_ass_overrides()

            sub_path = PathManager.get_subtitle_path(video.youtube_video_id, cand.start_ms, cand.end_ms)
            self.ass_generator.generate_ass(
                words=words,
                segment_start_ms=cand.start_ms,
                segment_end_ms=cand.end_ms,
                output_path=sub_path,
                style_overrides=style_overrides,
            )

            # SFX mapping (respects master toggle and per-channel setting)
            sfx_active = getattr(settings, "ENABLE_SFX", True)
            if sfx_active:
                sfx_cues = self.sfx_mapper.map_sfx_cues(
                    words,
                    segment_start_sec=cand.start_ms / 1000.0,
                )
                clip.sfx_tags_json = json.dumps([c.to_dict() for c in sfx_cues])
            else:
                clip.sfx_tags_json = json.dumps([])

            # Render video with hierarchical subfolder and slug
            ch_name = channel.name if channel else "Channel"
            ch_id = channel.youtube_channel_id if channel else "default"
            target_clip_path = PathManager.get_clip_output_path(
                channel_name=ch_name,
                channel_id=ch_id,
                video_title=video.title,
                video_id=video.youtube_video_id,
                hook_text=cand.hook_text,
                start_ms=cand.start_ms,
                end_ms=cand.end_ms,
            )
            out_name = str(target_clip_path.relative_to(settings.OUTPUT_DIR)).replace("\\", "/")

            rendered_file = self.compositor.render_short(
                source_video_path=source_path,
                start_ms=cand.start_ms,
                end_ms=cand.end_ms,
                crop_plan=crop_plan,
                subtitle_ass_path=sub_path,
                output_filename=out_name,
                normalize_audio=True,
            )

            # Generate Thumbnail Cover
            try:
                from shorts_engine.services.rendering.thumbnail_generator import ThumbnailGenerator
                thumb_path = PathManager.get_thumbnail_output_path(
                    channel_name=ch_name,
                    channel_id=ch_id,
                    video_title=video.title,
                    video_id=video.youtube_video_id,
                    hook_text=cand.hook_text,
                    start_ms=cand.start_ms,
                    end_ms=cand.end_ms,
                )
                ThumbnailGenerator.generate_thumbnail(
                    video_path=rendered_file,
                    output_path=thumb_path,
                    hook_text=cand.hook_text,
                )
                queue_thumb = settings.STORAGE_DIR / "thumbnails" / f"{clip.id}.jpg"
                if thumb_path.exists():
                    queue_thumb.parent.mkdir(parents=True, exist_ok=True)
                    import shutil
                    shutil.copy2(thumb_path, queue_thumb)
            except Exception as thumb_err:
                logger.warning("Optional thumbnail generation skipped in worker for clip %s: %s", clip.id, thumb_err)

            clip.rendered_path = str(rendered_file)
            clip.status = ClipStatus.QUALITY_CHECK
            db.commit()

            # Quality Check
            seg_words = [w.word for w in words if (w.start >= cand.start_ms / 1000.0 - 0.5 and w.end <= cand.end_ms / 1000.0 + 0.5)] if words else []
            seg_text = " ".join(seg_words) if seg_words else (cand.hook_text or "")
            human_req = channel.human_review_required if channel else True
            next_status, q_report = self.quality_gate.evaluate_clip(
                rendered_video_path=Path(clip.rendered_path),
                segment_text=seg_text,
                hook_text=cand.hook_text or "",
                payoff_text=cand.payoff_text or "",
                human_review_required=human_req,
            )
            clip.status = next_status
            clip.quality_check_result = json.dumps(q_report)
            db.commit()

            logger.info(f"Worker {self.worker_id} completed clip {clip.id} -> {next_status}")
            return True

        except Exception as e:
            logger.exception(f"Worker error on clip {clip_id}: {e}")
            db.rollback()
            try:
                failed_clip = db.query(Clip).filter_by(id=clip_id).first()
                if failed_clip:
                    failed_clip.retry_count += 1
                    err_msg = str(e)
                    failed_clip.quality_check_result = json.dumps({
                        "error": f"Attempt {failed_clip.retry_count} failed: {err_msg}",
                        "technical_check": {"passed": False, "error": err_msg},
                    })
                    if failed_clip.retry_count >= settings.MAX_RETRY_COUNT:
                        failed_clip.status = ClipStatus.FAILED
                        logger.error(f"Clip {clip_id} exhausted max retries ({failed_clip.retry_count}/{settings.MAX_RETRY_COUNT}) -> FAILED")
                    else:
                        failed_clip.status = ClipStatus.RENDERING
                        failed_clip.rendered_path = None
                        logger.warning(f"Clip {clip_id} attempt {failed_clip.retry_count} failed; reset to RENDERING for automatic retry")
                    db.commit()
            except Exception as commit_err:
                logger.error(f"Failed to record clip retry state: {commit_err}")
            return False
        finally:
            clear_cuda_cache()
            db.close()

    def run_once(self) -> int:
        """Claims and processes at most one clip. Returns 1 if processed, 0 if queue empty."""
        db = self.session_factory()
        try:
            clip = claim_next_clip(db, worker_id=self.worker_id)
            if not clip:
                return 0
            clip_id = clip.id
        finally:
            db.close()

        success = self.process_clip(clip_id)
        return 1 if success else 0

    def run_loop(self, poll_interval: float = 5.0, stop_event=None):
        """Continuous polling loop."""
        logger.info(f"Worker {self.worker_id} started. Polling interval: {poll_interval}s")
        try:
            while True:
                if stop_event and stop_event.is_set():
                    logger.info("Worker stop requested.")
                    break

                reload_runtime_settings()
                count = self.run_once()
                if count == 0:
                    time.sleep(poll_interval)
        except KeyboardInterrupt:
            logger.info("Worker stopped by keyboard interrupt.")
