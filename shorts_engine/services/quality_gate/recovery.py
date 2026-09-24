"""
Universal Clip Recovery & Failure Fixing Service:
Diagnoses, auto-fixes, and recovers clips from:
1. Technical failures (duration out of bounds, container muxing, stream issues).
2. Framing failures (caption collisions with YouTube UI deadzones, subject centering).
3. Semantic failures (hallucinated hooks/payoffs, short-form narrative criteria).
4. Advisory flags (soft conversational notes, operator review routing).
"""

import json
import logging
import os
import platform
import re
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List, Union, Protocol
from sqlalchemy.orm import Session

from shorts_engine.config.settings import settings
from shorts_engine.db.models import Clip, ClipStatus, ClipCandidate, SourceVideo, Transcript
from shorts_engine.services.quality_gate.technical_check import TechnicalVerifier
from shorts_engine.services.quality_gate.render_inspector import RenderInspector
from shorts_engine.services.quality_gate.semantic_check import SemanticVerifier
from shorts_engine.services.quality_gate.review_queue import QualityGateManager
from shorts_engine.services.execution.watchdog import SubprocessWatchdog
from shorts_engine.utils.fs import atomic_replace

logger = logging.getLogger(__name__)


class TrimmerProtocol(Protocol):
    def trim_clip(
        self,
        clip_id: str,
        start_sec: float,
        end_sec: float,
        snap_words: bool = True,
        caption_style_preset: Optional[str] = None,
    ) -> Dict[str, Any]:
        ...


class ClipRecoveryService:
    def __init__(self, trimmer_service: Optional[TrimmerProtocol] = None):
        self.quality_gate = QualityGateManager()
        self.trimmer_service = trimmer_service

    def diagnose_clip(self, clip: Clip) -> Dict[str, Any]:
        """
        Extracts detailed diagnosis of gate checks, failure reasons, and recommended fixes.
        """
        qr = {}
        if clip.quality_check_result:
            try:
                qr = json.loads(clip.quality_check_result)
            except Exception:
                qr = {}

        tech = qr.get("technical_check") or {}
        framing = qr.get("render_inspection") or {}
        sem = qr.get("semantic_check") or {}
        advisories = qr.get("advisories") or []

        tech_passed = bool(tech.get("all_passed") or tech.get("passed"))
        frame_passed = bool(framing.get("all_passed") or framing.get("passed"))
        sem_passed = bool(sem.get("pass_decision") or sem.get("passed"))

        # Technical details
        tech_errors = []
        if not tech_passed and tech:
            if tech.get("error"):
                tech_errors.append(tech["error"])
            if "checks" in tech:
                for k, v in tech["checks"].items():
                    if not v:
                        tech_errors.append(k.replace("_", " "))

        # Framing details
        frame_errors = []
        if not frame_passed and framing:
            if framing.get("error"):
                frame_errors.append(framing["error"])
            sub_v = framing.get("subject_framing_violations", 0)
            cap_v = framing.get("caption_collision_violations", 0)
            if sub_v > 0:
                frame_errors.append(f"Subject off-center in {sub_v} sample frames")
            if cap_v > 0:
                frame_errors.append(f"Caption collided with UI overlays in {cap_v} sample frames")

        # Semantic details
        sem_errors = []
        if not sem_passed and sem:
            critique = sem.get("critique_reason") or sem.get("error")
            if critique:
                sem_errors.append(critique)

        # Recommended fixes
        recommended_fixes = []
        if not tech_passed:
            recommended_fixes.append({
                "type": "technical_clamp",
                "label": "Clamp Duration & Re-mux",
                "description": "Losslessly trim container duration to fit YouTube Shorts bounds.",
            })
        if not frame_passed:
            recommended_fixes.append({
                "type": "caption_safe_rerender",
                "label": "Re-render with Safe Caption Margin",
                "description": "Re-renders captions with elevated vertical clearance to avoid bottom UI buttons.",
            })
        if not sem_passed or advisories:
            recommended_fixes.append({
                "type": "reanchor_dialogue",
                "label": "Re-anchor Dialogue from Audio",
                "description": "Re-extracts exact spoken sentences from the word-level transcript for hook and payoff.",
            })

        status_val = clip.status.value if hasattr(clip.status, "value") else str(clip.status)

        return {
            "clip_id": clip.id,
            "status": status_val,
            "rendered_path": clip.rendered_path,
            "file_exists": bool(clip.rendered_path and Path(clip.rendered_path).exists()),
            "tech_gate": {
                "passed": tech_passed,
                "errors": tech_errors,
                "details": tech,
            },
            "framing_gate": {
                "passed": frame_passed,
                "errors": frame_errors,
                "details": framing,
            },
            "semantic_gate": {
                "passed": sem_passed,
                "errors": sem_errors,
                "details": sem,
            },
            "advisories": advisories,
            "has_advisories": len(advisories) > 0,
            "recommended_fixes": recommended_fixes,
        }

    def re_evaluate_clip(self, clip: Clip, db: Session) -> Tuple[ClipStatus, Dict[str, Any]]:
        """
        Re-evaluates a single clip against the current Quality Gate rules, duration bounds,
        and advisory routing in-place.
        """
        if not clip.rendered_path or not Path(clip.rendered_path).exists():
            return clip.status, {"error": "Rendered video file not found on disk"}

        cand = clip.candidate
        video = cand.source_video if (cand and cand.source_video) else None

        # Resolve duration bounds
        overrides = video.runtime_overrides if (video and video.runtime_overrides) else {}
        eff_min_dur = int(overrides.get("target_duration_min_sec") or settings.TARGET_DURATION_MIN_SEC)
        eff_max_dur = int(overrides.get("target_duration_max_sec") or settings.TARGET_DURATION_MAX_SEC)

        # Load transcript for dialogue anchoring
        transcript_record = db.query(Transcript).filter_by(source_video_id=video.id).first() if video else None
        words = []
        if transcript_record and transcript_record.word_level_json:
            try:
                t_data = json.loads(transcript_record.word_level_json)
                words = t_data.get("words", [])
            except Exception:
                pass

        seg_text = ""
        if words and cand:
            seg_text = " ".join([
                w.get("word", "") for w in words
                if cand.start_ms <= w.get("start", 0) * 1000 <= cand.end_ms
            ])
        if not seg_text and cand:
            seg_text = f"{cand.hook_text or ''} {cand.payoff_text or ''}".strip()

        human_review = video.channel.human_review_required if (video and video.channel) else True

        new_status, q_report = self.quality_gate.evaluate_clip(
            rendered_video_path=Path(clip.rendered_path),
            segment_text=seg_text,
            hook_text=cand.hook_text if cand else "",
            payoff_text=cand.payoff_text if cand else "",
            human_review_required=human_review,
            min_duration_sec=eff_min_dur,
            max_duration_sec=eff_max_dur,
        )

        clip.status = new_status
        clip.quality_check_result = json.dumps(q_report)
        db.commit()

        logger.info("Re-evaluated clip %s: new status is %s", clip.id, new_status.value)
        return new_status, q_report

    def force_approve_clip(
        self,
        clip: Clip,
        db: Session,
        operator_name: str = "operator",
        note: Optional[str] = None,
    ) -> Tuple[ClipStatus, Dict[str, Any]]:
        """
        Operator override: bypasses any technical, framing, or semantic rejections
        and transitions clip directly to APPROVED (or WAITING_APPROVAL).
        """
        qr = {}
        if clip.quality_check_result:
            try:
                qr = json.loads(clip.quality_check_result)
            except Exception:
                qr = {}

        qr["operator_override"] = {
            "approved_by": operator_name,
            "previous_status": clip.status.value if hasattr(clip.status, "value") else str(clip.status),
            "note": note or "Operator manual quality gate override",
        }

        # Transition to WAITING_APPROVAL if human review is desired, or APPROVED
        cand = clip.candidate
        video = cand.source_video if (cand and cand.source_video) else None
        target_status = ClipStatus.WAITING_APPROVAL if (video and video.channel and video.channel.human_review_required) else ClipStatus.APPROVED

        clip.status = target_status
        clip.quality_check_result = json.dumps(qr)
        db.commit()

        logger.info("Operator '%s' force-approved clip %s (status: %s)", operator_name, clip.id, target_status.value)
        return target_status, qr

    def apply_auto_fix(
        self,
        clip: Clip,
        fix_type: str,
        db: Session,
    ) -> Dict[str, Any]:
        """
        Executes an automated remediation pass for a specific failure mode:
        - 'technical_clamp': Trims/remuxes the clip container to the target duration.
        - 'reanchor_dialogue': Replaces paraphrased hook/payoff with genuine transcript sentences.
        - 'caption_safe_rerender': Re-renders clip with elevated caption placement.
        """
        cand = clip.candidate
        video = cand.source_video if (cand and cand.source_video) else None

        if fix_type == "technical_clamp":
            if not clip.rendered_path or not Path(clip.rendered_path).exists():
                return {"status": "error", "message": "Rendered video file not found"}

            in_path = Path(clip.rendered_path)
            out_path = in_path.with_name(f"{in_path.stem}_clamped.mp4")

            overrides = video.runtime_overrides if (video and video.runtime_overrides) else {}
            target_max = float(overrides.get("target_duration_max_sec") or settings.TARGET_DURATION_MAX_SEC)

            # Clamp using fast stream copy or re-encode
            cmd = [
                "ffmpeg", "-y",
                "-i", str(in_path),
                "-t", str(target_max),
                "-c:v", "copy",
                "-c:a", "copy",
                "-movflags", "+faststart",
                str(out_path),
            ]
            try:
                watchdog_res = SubprocessWatchdog.run(cmd, timeout_sec=60.0)
            except Exception as e:
                return {"status": "error", "message": f"FFmpeg clamp failed on exception: {e}"}

            if watchdog_res.returncode == 0 and out_path.exists() and out_path.stat().st_size > 50_000:
                atomic_replace(out_path, in_path)
                logger.info("Clamped duration for clip %s to %ss", clip.id, target_max)
                # Re-evaluate
                self.re_evaluate_clip(clip, db)
                return {"status": "success", "message": f"Clamped video duration to {target_max}s"}
            else:
                err_msg = watchdog_res.stderr[:200] if watchdog_res.stderr else "Output validation failed"
                return {"status": "error", "message": f"FFmpeg clamp failed: {err_msg}"}

        elif fix_type == "reanchor_dialogue":
            if not video or not cand:
                return {"status": "error", "message": "Source video or candidate missing"}

            transcript_record = db.query(Transcript).filter_by(source_video_id=video.id).first()
            if not transcript_record or not transcript_record.word_level_json:
                return {"status": "error", "message": "Transcript record not found"}

            try:
                t_data = json.loads(transcript_record.word_level_json)
                words = t_data.get("words", [])
            except Exception as e:
                return {"status": "error", "message": f"Could not parse transcript: {str(e)}"}

            seg_text = " ".join([
                w.get("word", "") for w in words
                if cand.start_ms <= w.get("start", 0) * 1000 <= cand.end_ms
            ])
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', seg_text) if s.strip()]
            if sentences:
                cand.hook_text = sentences[0]
                cand.payoff_text = sentences[-1]
                db.commit()
                # Re-evaluate
                self.re_evaluate_clip(clip, db)
                return {
                    "status": "success",
                    "message": "Re-anchored hook and payoff to genuine dialogue.",
                    "hook": cand.hook_text,
                    "payoff": cand.payoff_text,
                }
            return {"status": "error", "message": "No sentences found in candidate range"}

        elif fix_type == "caption_safe_rerender":
            # Re-renders with elevated caption margin to ensure clearance from YouTube UI
            if not clip.rendered_path or not Path(clip.rendered_path).exists():
                return {"status": "error", "message": "Rendered video file not found"}

            if not self.trimmer_service:
                return {"status": "error", "message": "Trimmer service not configured for caption safe re-render."}

            start_sec = max(0.0, float(cand.start_ms or 0) / 1000.0) if cand else 0.0
            end_sec = max(start_sec, float(cand.end_ms or 0) / 1000.0) if cand else 0.0

            try:
                res = self.trimmer_service.trim_clip(
                    clip_id=clip.id,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    snap_words=False,
                    caption_style_preset=clip.caption_style or "hormozi",
                )
                self.re_evaluate_clip(clip, db)
                return {"status": "success", "message": "Re-rendered clip with safe caption clearance.", "result": res}
            except Exception as e:
                logger.exception("Failed to re-render clip with safe captions: %s", e)
                return {"status": "error", "message": f"Failed to re-render safe clip: {str(e)}"}

        return {"status": "error", "message": f"Unknown fix type: {fix_type}"}

    @classmethod
    def open_in_folder(cls, file_path: Path) -> Dict[str, Any]:
        """
        Launches the native OS file explorer pointing directly to the file.
        Supports Windows (explorer /select,), macOS (open -R), and Linux (xdg-open).
        """
        if not file_path.exists():
            return {
                "status": "error",
                "message": f"File does not exist: {file_path}",
                "path": str(file_path),
            }

        target_str = str(file_path.resolve())
        system = platform.system().lower()

        try:
            if system == "windows":
                # explorer /select,<path> highlights the specific file
                subprocess.Popen(["explorer.exe", f"/select,{target_str}"])
            elif system == "darwin":
                subprocess.Popen(["open", "-R", target_str])
            else:
                subprocess.Popen(["xdg-open", str(file_path.parent.resolve())])
            return {
                "status": "success",
                "message": f"Opened folder containing {file_path.name}",
                "path": target_str,
                "folder": str(file_path.parent.resolve()),
            }
        except Exception as e:
            logger.warning("Could not launch OS file manager: %s", str(e))
            return {
                "status": "fallback",
                "message": f"Could not launch file manager: {str(e)}",
                "path": target_str,
                "folder": str(file_path.parent.resolve()),
            }
