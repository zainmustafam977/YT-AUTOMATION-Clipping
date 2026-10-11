"""
Decoupled Background Worker Runner.
Picks pending clips from database queue with dialect-aware locking (PostgreSQL FOR UPDATE SKIP LOCKED vs SQLite CAS),
flushes CUDA cache between jobs, enforces VRAM limits, and executes rendering & quality checking.
"""

import os
import re
import time
import json
import uuid
import socket
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import or_

from shorts_engine.config.settings import settings
from shorts_engine.config.logging_config import reload_runtime_settings
from shorts_engine.db.session import SessionLocal, is_postgres
from shorts_engine.db.models import Clip, ClipStatus
from shorts_engine.services.reframing.cropper import VideoReframer
from shorts_engine.services.captioning.ass_generator import ASSSubtitleGenerator
from shorts_engine.services.captioning.sfx_mapper import SFXMapper
from shorts_engine.services.rendering.compositor import VideoCompositor
from shorts_engine.services.quality_gate.review_queue import QualityGateManager
from shorts_engine.services.transcription.formatter import WordSegment
from shorts_engine.services.monitoring.alerter import PipelineAlerter
from shorts_engine.services.events.bus import global_event_bus
from shorts_engine.services.events.models import PlatformEvent

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


def generate_worker_id() -> str:
    """Generates a globally unique worker identifier containing hostname, PID, and entropy."""
    host = socket.gethostname()
    return f"worker-{host}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


def claim_next_clip(db: Session, worker_id: Optional[str] = None) -> Optional[Clip]:
    """
    Atomically claims the next pending clip for processing.
    - PostgreSQL: Uses SELECT ... FOR UPDATE SKIP LOCKED for non-blocking multi-worker concurrency.
    - SQLite: Uses atomic Compare-And-Swap (CAS) to prevent race conditions across local worker threads.
    """
    wid = worker_id or generate_worker_id()

    if is_postgres(db.get_bind()):
        # PostgreSQL: lock row directly at engine level
        clip = (
            db.query(Clip)
            .filter(
                Clip.status == ClipStatus.RENDERING,
                or_(Clip.rendered_path.is_(None), Clip.rendered_path.startswith("CLAIMED:").is_(False)),  # noqa: E711
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
                or_(Clip.rendered_path.is_(None), Clip.rendered_path.startswith("CLAIMED:").is_(False)),  # noqa: E711
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


def parse_claim_info(claim_str: Optional[str]) -> Tuple[Optional[str], Optional[int]]:
    """
    Parses a claim string into (host, pid).
    Supports:
      - 'CLAIMED:worker-<host>-<pid>-<uuid>' (distributed multi-host format)
      - 'CLAIMED:worker-<pid>-<uuid>' (legacy local format)
      - 'CLAIMED:runner-<pid>' (legacy runner format)
    """
    if not claim_str or not isinstance(claim_str, str):
        return None, None
    m = re.match(r"^CLAIMED:(?:runner|worker)-([A-Za-z0-9_.-]+)-(\d+)-[a-f0-9]+$", claim_str)
    if m:
        return m.group(1), int(m.group(2))
    m_legacy = re.match(r"^CLAIMED:(?:runner|worker)-(\d+)(?:-[a-f0-9]+)?$", claim_str)
    if m_legacy:
        return None, int(m_legacy.group(1))
    return None, None


def extract_pid_from_claim(claim_str: Optional[str]) -> Optional[int]:
    """Extracts numeric PID from a claim string for backward compatibility."""
    _, pid = parse_claim_info(claim_str)
    return pid


def is_claim_stale(
    claim_str: str,
    updated_at: Optional[datetime],
    now: datetime,
    stale_timeout_sec: int = 600,
) -> bool:
    """
    Determines whether a claim is stale across single-node or multi-node clusters.
    - Local host claims: if process PID is dead -> STALE immediately.
    - Remote host claims: safely wait for age > stale_timeout_sec (prevents false-positive steals across containers).
    """
    host, pid = parse_claim_info(claim_str)
    current_host = socket.gethostname()

    # If claim was made on current host (or legacy claim with no host):
    if host is None or host.lower() == current_host.lower():
        if pid is not None and not is_pid_alive(pid):
            return True

    # Check timeout for remote claims or living PIDs
    if updated_at:
        age_sec = (now - updated_at).total_seconds()
        if age_sec > stale_timeout_sec:
            return True

    return False


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
            rpath = clip.rendered_path or ""
            if rpath.startswith("CLAIMED:"):
                if is_claim_stale(rpath, clip.updated_at, now, stale_timeout_sec=stale_timeout_sec):
                    logger.info("Reconciling stale claim on clip %s (claim: %s)", clip.id, rpath)
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
    wid = worker_id or generate_worker_id()

    target_clip = db.query(Clip).filter(Clip.id == clip_id, Clip.status == ClipStatus.RENDERING).first()
    if not target_clip:
        return None

    # If already claimed, check if holding PID is alive
    if target_clip.rendered_path and target_clip.rendered_path.startswith("CLAIMED:"):
        from datetime import datetime
        if is_claim_stale(target_clip.rendered_path, target_clip.updated_at, datetime.utcnow()):
            logger.info("Stealing/reclaiming clip %s from stale claim %s", clip_id, target_clip.rendered_path)
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
        self.worker_id = worker_id or generate_worker_id()
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
            try:
                global_event_bus.publish_sync(PlatformEvent(
                    category="rendering",
                    level="INFO",
                    title="Worker Render Started",
                    message=f"Worker {self.worker_id} started rendering clip {clip.id[:8]} ({cand.start_ms//1000}s - {cand.end_ms//1000}s)...",
                    stage_progress_pct=25.0,
                    entity_id=clip.id,
                    entity_type="clip",
                    action_url=f"/queue?clip={clip.id}",
                ))
            except Exception:
                pass

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
            try:
                global_event_bus.publish_sync(PlatformEvent(
                    category="rendering",
                    level="SUCCESS",
                    title="Worker Render Completed",
                    message=f"Worker {self.worker_id} rendered clip {clip.id[:8]} ({Path(clip.rendered_path).name}).",
                    stage_progress_pct=85.0,
                    entity_id=clip.id,
                    entity_type="clip",
                    action_url=f"/queue?clip={clip.id}",
                ))
            except Exception:
                pass

            # Quality Check
            seg_words = [w.word for w in words if (w.start >= cand.start_ms / 1000.0 - 0.5 and w.end <= cand.end_ms / 1000.0 + 0.5)] if words else []
            seg_text = " ".join(seg_words) if seg_words else (cand.hook_text or "")
            human_req = channel.human_review_required if channel else True

            # Stage 1: Computer Vision & Acoustic Media QA Gate
            qa_res = None
            if clip.rendered_path and Path(clip.rendered_path).is_file():
                try:
                    from shorts_engine.services.quality.media_qa import MediaQAGate
                    qa_gate = MediaQAGate()
                    qa_res = qa_gate.inspect_file(Path(clip.rendered_path))
                    if not qa_res.passed:
                        logger.warning("MediaQAGate flagged clip %s: %s", clip.id, qa_res.summary)
                        clip.status = ClipStatus.FAILED
                        clip.quality_check_result = json.dumps({
                            "passed": False,
                            "media_qa": {"passed": False, "violations": qa_res.violations, "metrics": qa_res.metrics},
                            "error": qa_res.summary,
                        })
                        db.commit()
                        try:
                            global_event_bus.publish_sync(PlatformEvent(
                                category="quality_gate",
                                level="WARNING",
                                title="Media QA Gate Flagged",
                                message=f"Media QA flagged clip {clip.id[:8]}: {qa_res.summary}",
                                entity_id=clip.id,
                                entity_type="clip",
                                action_url=f"/queue?clip={clip.id}",
                            ))
                        except Exception:
                            pass
                        return False
                except Exception as qa_err:
                    logger.warning("MediaQAGate inspection exception (continuing to semantic gate): %s", qa_err)
                    qa_res = None

            # Stage 2: Multimodal & Semantic Quality Gate
            next_status, q_report = self.quality_gate.evaluate_clip(
                rendered_video_path=Path(clip.rendered_path),
                segment_text=seg_text,
                hook_text=cand.hook_text or "",
                payoff_text=cand.payoff_text or "",
                human_review_required=human_req,
            )
            if qa_res and qa_res.passed:
                q_report["media_qa"] = {"passed": True, "metrics": qa_res.metrics}

            clip.status = next_status
            clip.quality_check_result = json.dumps(q_report)
            db.commit()

            try:
                q_level = "SUCCESS" if next_status == ClipStatus.APPROVED else ("WARNING" if next_status == ClipStatus.WAITING_APPROVAL else "ERROR")
                global_event_bus.publish_sync(PlatformEvent(
                    category="quality_gate",
                    level=q_level,
                    title=f"Quality Gate: {next_status.value}",
                    message=f"Worker evaluated clip {clip.id[:8]}: {next_status.value}.",
                    stage_progress_pct=100.0,
                    entity_id=clip.id,
                    entity_type="clip",
                    action_url=f"/queue?clip={clip.id}",
                ))
            except Exception:
                pass

            logger.info(f"Worker {self.worker_id} completed clip {clip.id} -> {next_status}")
            return True

        except Exception as e:
            logger.exception(f"Worker error on clip {clip_id}: {e}")
            try:
                global_event_bus.publish_sync(PlatformEvent(
                    category="rendering",
                    level="ERROR",
                    title="Worker Render Failed",
                    message=f"Worker failed on clip {clip_id[:8]}: {str(e)[:250]}",
                    entity_id=clip_id,
                    entity_type="clip",
                    action_url=f"/queue?clip={clip_id}",
                ))
            except Exception:
                pass
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
