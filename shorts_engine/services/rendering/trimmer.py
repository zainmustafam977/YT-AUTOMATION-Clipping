"""
Clip Trimmer & Re-Timer Service:
Enables sub-frame precision trimming with natural speech pause snapping,
dynamic active-speaker crop recalculation, sub-second ASS subtitle re-indexing,
and atomic video re-rendering without re-downloading or re-transcribing long-form video.
"""

import json
import uuid
import shutil
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from sqlalchemy.orm import Session

from shorts_engine.config.settings import settings
from shorts_engine.db.models import Clip, ClipCandidate, SourceVideo
from shorts_engine.services.storage.path_manager import PathManager
from shorts_engine.services.transcription.formatter import WordSegment
from shorts_engine.services.captioning.ass_generator import ASSSubtitleGenerator
from shorts_engine.services.captioning.templates import get_caption_template, parse_custom_template
from shorts_engine.services.reframing.cropper import VideoReframer
from shorts_engine.services.rendering.compositor import VideoCompositor
from shorts_engine.services.quality_gate.technical_check import TechnicalVerifier
from shorts_engine.services.quality_gate.render_inspector import RenderInspector

logger = logging.getLogger(__name__)


def snap_to_word_boundary(
    words: List[WordSegment],
    target_start_sec: float,
    target_end_sec: float,
    tolerance_sec: float = 1.5,
    min_silence_gap_sec: float = 0.20,
) -> Tuple[float, float]:
    """
    Evaluates word-level timestamps and snaps requested start/end points
    to natural inter-word silence pauses to avoid clipping vocal syllables.
    """
    if not words:
        return target_start_sec, target_end_sec

    snapped_start = target_start_sec
    snapped_end = target_end_sec

    # Snap start boundary
    candidates_start = [
        w for w in words if abs(w.start - target_start_sec) <= tolerance_sec or abs(w.end - target_start_sec) <= tolerance_sec
    ]
    if candidates_start:
        cutting_word = next((w for w in candidates_start if w.start <= target_start_sec <= w.end), None)
        if cutting_word:
            snapped_start = cutting_word.start
        else:
            closest_w = min(candidates_start, key=lambda w: min(abs(w.start - target_start_sec), abs(w.end - target_start_sec)))
            snapped_start = closest_w.start if abs(closest_w.start - target_start_sec) < abs(closest_w.end - target_start_sec) else closest_w.end

    # Snap end boundary
    candidates_end = [
        w for w in words if abs(w.start - target_end_sec) <= tolerance_sec or abs(w.end - target_end_sec) <= tolerance_sec
    ]
    if candidates_end:
        cutting_word = next((w for w in candidates_end if w.start <= target_end_sec <= w.end), None)
        if cutting_word:
            snapped_end = cutting_word.end
        else:
            closest_w = min(candidates_end, key=lambda w: min(abs(w.start - target_end_sec), abs(w.end - target_end_sec)))
            snapped_end = closest_w.end if abs(closest_w.end - target_end_sec) < abs(closest_w.start - target_end_sec) else closest_w.start

    # Safety guard: ensure start < end and minimum duration
    if snapped_end - snapped_start < 5.0:
        snapped_start = target_start_sec
        snapped_end = target_end_sec

    return round(snapped_start, 3), round(snapped_end, 3)


class ClipTrimmerService:
    """Industrial-grade clip trimming, re-timing, and atomic rendering service."""

    def __init__(self, db: Session):
        self.db = db
        self.reframer = VideoReframer()
        self.compositor = VideoCompositor()
        self.ass_generator = ASSSubtitleGenerator()

    def trim_clip(
        self,
        clip_id: str,
        start_sec: float,
        end_sec: float,
        snap_words: bool = True,
        caption_style_preset: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Trims and atomically re-renders a Short video.
        """
        clip = self.db.query(Clip).filter_by(id=clip_id).first()
        if not clip:
            raise ValueError(f"Clip not found: {clip_id}")

        cand = clip.candidate
        if not cand:
            raise ValueError(f"Candidate not found for clip: {clip_id}")

        source_video = cand.source_video
        if not source_video or not source_video.downloaded_path:
            raise ValueError(f"Source video media missing for clip: {clip_id}")

        source_path = PathManager.resolve_download_path(source_video)
        if not source_path or not source_path.exists():
            raise FileNotFoundError(f"Source video media file does not exist on disk: {source_path}")

        # Parse word-level transcript
        words: List[WordSegment] = []
        if source_video.transcript and source_video.transcript.word_level_json:
            try:
                t_data = json.loads(source_video.transcript.word_level_json)
                raw_words = t_data.get("words", []) if isinstance(t_data, dict) else t_data
                words = [WordSegment(word=w["word"], start=float(w["start"]), end=float(w["end"])) for w in raw_words]
            except Exception as e:
                logger.warning("Could not parse word timestamps for trimming: %s", e)

        # Apply natural speech pause snapping if requested
        eff_start = max(0.0, float(start_sec))
        eff_end = float(end_sec)
        if snap_words and words:
            eff_start, eff_end = snap_to_word_boundary(words, eff_start, eff_end)

        duration = eff_end - eff_start
        min_allowed = getattr(settings, "TARGET_DURATION_MIN_SEC", 15)
        max_allowed = getattr(settings, "MAX_EXTENDED_DURATION_SEC", 180) if getattr(settings, "EXTENDED_SHORTS_ALLOWED", True) else getattr(settings, "TARGET_DURATION_MAX_SEC", 58)

        if duration < 10.0:
            raise ValueError(f"Trimmed duration ({duration:.1f}s) is too short. Minimum allowed is 10.0s.")
        if duration > max_allowed:
            raise ValueError(f"Trimmed duration ({duration:.1f}s) exceeds maximum allowed ({max_allowed}s).")

        start_ms = int(round(eff_start * 1000))
        end_ms = int(round(eff_end * 1000))

        # 1. Re-generate subtitle file for newly bounded interval
        style_overrides = None
        if caption_style_preset:
            tmpl = get_caption_template(caption_style_preset)
            clip.caption_style = json.dumps(tmpl.to_ui_dict())
            style_overrides = tmpl.to_ass_overrides()
        elif clip.caption_style:
            try:
                c_data = json.loads(clip.caption_style)
                style_overrides = parse_custom_template(c_data).to_ass_overrides()
            except Exception:
                style_overrides = None

        if not style_overrides:
            tmpl = get_caption_template(getattr(settings, "DEFAULT_CAPTION_PRESET", "hormozi"))
            style_overrides = tmpl.to_ass_overrides()

        sub_path = PathManager.get_subtitle_path(source_video.youtube_video_id, start_ms, end_ms)
        self.ass_generator.generate_ass(
            words=words,
            segment_start_ms=start_ms,
            segment_end_ms=end_ms,
            output_path=sub_path,
            style_overrides=style_overrides,
        )

        # 2. Re-compute active speaker 9:16 crop plan
        crop_plan = self.reframer.compute_crop_plan(
            video_path=source_path,
            start_ms=start_ms,
            end_ms=end_ms,
        )

        # 3. Render short to deterministic hierarchical output path
        ch_name = source_video.channel.name if source_video.channel else "Channel"
        ch_id = source_video.channel.youtube_channel_id if source_video.channel else "default"
        target_clip_path = PathManager.get_clip_output_path(
            channel_name=ch_name,
            channel_id=ch_id,
            video_title=source_video.title,
            video_id=source_video.youtube_video_id,
            hook_text=cand.hook_text,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        try:
            rel_output_name = str(target_clip_path.relative_to(settings.OUTPUT_DIR)).replace("\\", "/")
        except ValueError:
            rel_output_name = target_clip_path.name

        # Preserve opening hook banner if active
        hook_overlay_text = None
        if getattr(settings, "ENABLE_HOOK_OVERLAY", False):
            hook_overlay_text = cand.hook_text
        elif cand.variations and isinstance(cand.variations, list) and len(cand.variations) > 0:
            first_v = cand.variations[0]
            if isinstance(first_v, dict) and first_v.get("hook_overlay"):
                hook_overlay_text = first_v.get("hook_overlay")

        rendered_file = self.compositor.render_short(
            source_video_path=source_path,
            start_ms=start_ms,
            end_ms=end_ms,
            crop_plan=crop_plan,
            subtitle_ass_path=sub_path,
            output_filename=rel_output_name,
            normalize_audio=True,
            hook_overlay_text=hook_overlay_text,
        )

        # Validate rendered video
        validation = self.compositor.validate_render(rendered_file)
        if not validation.is_valid:
            logger.warning("Rendered trimmed clip failed validation checks: %s", validation.error)

        # Clean up old rendered file if different from the newly generated file (F-39, PLAN-P0-06)
        old_path = clip.rendered_path
        if old_path:
            p_old = Path(old_path)
            p_new = Path(rendered_file)
            if p_old.exists() and p_old.resolve() != p_new.resolve():
                try:
                    p_old.unlink()
                    logger.info("Unlinked orphaned previous render file: %s", old_path)
                except OSError as e:
                    logger.warning("Could not unlink old render file %s: %s", old_path, e)

        # 4. Atomic database state update
        cand.start_ms = start_ms
        cand.end_ms = end_ms
        clip.rendered_path = str(rendered_file)

        # Re-run quality gate technical check
        tech_verifier = TechnicalVerifier()
        passed, tech_details = tech_verifier.verify(
            rendered_file,
            min_duration_sec=10.0,
            max_duration_sec=max_allowed,
        )
        try:
            curr_qc = json.loads(clip.quality_check_result or "{}")
        except Exception:
            curr_qc = {}
        curr_qc["technical_check"] = {"passed": passed, "details": tech_details}
        curr_qc["trimmed_metadata"] = {
            "start_sec": eff_start,
            "end_sec": eff_end,
            "duration_sec": round(duration, 2),
            "snapped": snap_words,
        }
        clip.quality_check_result = json.dumps(curr_qc)

        self.db.commit()
        self.db.refresh(clip)
        self.db.refresh(cand)

        logger.info(
            "Successfully trimmed and re-rendered clip %s -> [%.2fs - %.2fs] (duration: %.2fs)",
            clip.id, eff_start, eff_end, duration,
        )

        return {
            "success": True,
            "clip_id": clip.id,
            "start_sec": eff_start,
            "end_sec": eff_end,
            "duration_sec": round(duration, 2),
            "rendered_path": str(rendered_file),
            "is_valid": validation.is_valid,
        }
