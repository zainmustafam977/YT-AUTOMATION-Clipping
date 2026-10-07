"""
Quality Gate Review Queue & State Machine: orchestrates technical, multimodal,
and semantic verifiers to transition clips to WAITING_APPROVAL, APPROVED, or REJECTED.
"""

import logging
from pathlib import Path
from typing import Dict, Any, Tuple, Optional
from shorts_engine.db.models import ClipStatus
from shorts_engine.services.quality_gate.technical_check import TechnicalVerifier
from shorts_engine.services.quality_gate.render_inspector import RenderInspector
from shorts_engine.services.quality_gate.semantic_check import SemanticVerifier

logger = logging.getLogger(__name__)


class QualityGateManager:
    def __init__(self):
        self.technical_verifier = TechnicalVerifier()
        self.render_inspector = RenderInspector()
        self.semantic_verifier = SemanticVerifier()

    def evaluate_clip(
        self,
        rendered_video_path: Path,
        segment_text: str,
        hook_text: str,
        payoff_text: str,
        human_review_required: bool = True,
        min_duration_sec: Optional[float] = None,
        max_duration_sec: Optional[float] = None,
    ) -> Tuple[ClipStatus, Dict[str, Any]]:
        """
        Runs the tiered quality gate pipeline:
        1. Fatal Technical check (aspect ratio, duration bounds, streams) -> Hard reject if failed
        2. Multimodal post-render inspection (subject safe framing, caption UI collision)
        3. Semantic check (autonomy, narrative resolution, sponsor ad detection)
        
        When human_review_required=True, clips that pass technical checks and have no forbidden
        sponsor ads are routed to WAITING_APPROVAL with advisory warnings, giving the human
        operator full control in the Review Queue.
        """
        logger.info("Running Quality Gate on %s...", rendered_video_path.name)
        report: Dict[str, Any] = {}

        # 1. Technical verification (Fatal Gate)
        tech_pass, tech_details = self.technical_verifier.verify(
            rendered_video_path,
            min_duration_sec=min_duration_sec,
            max_duration_sec=max_duration_sec,
        )
        report["technical_check"] = tech_details
        if not tech_pass:
            logger.warning("Clip %s failed fatal technical check: %s", rendered_video_path.name, tech_details)
            return ClipStatus.REJECTED, report

        # 2. Multimodal post-render inspection
        render_pass, render_details = self.render_inspector.inspect_render(rendered_video_path)
        report["render_inspection"] = render_details

        # 3. Semantic check
        sem_pass, sem_details = self.semantic_verifier.verify_segment(
            segment_text=segment_text,
            hook_text=hook_text,
            payoff_text=payoff_text,
        )
        report["semantic_check"] = sem_details

        # Hard reject if explicit prohibited sponsor or promotional ad reads are detected
        if sem_details.get("has_forbidden_phrases", False):
            logger.warning("Clip %s rejected due to prohibited sponsor/promo phrase: %s", rendered_video_path.name, sem_details.get("critique_reason"))
            return ClipStatus.REJECTED, report

        # Collect advisories for operator review
        advisories = []
        if not render_pass:
            framing_viols = render_details.get("subject_framing_violations", 0)
            caption_viols = render_details.get("caption_collision_violations", 0)
            if framing_viols > 0:
                advisories.append(f"Subject framing alert ({framing_viols} frame{'s' if framing_viols > 1 else ''} near edge)")
            if caption_viols > 0:
                advisories.append(f"Caption UI margin alert ({caption_viols} frame{'s' if caption_viols > 1 else ''})")
        if not sem_pass or sem_details.get("semantic_advisory"):
            adv = sem_details.get("semantic_advisory") or sem_details.get("critique_reason") or "Context review recommended"
            advisories.append(f"Semantic note: {adv[:80]}")

        report["advisories"] = advisories
        report["has_advisories"] = len(advisories) > 0

        if human_review_required:
            if advisories:
                logger.info("Clip %s queued for WAITING_APPROVAL with %d advisory warning(s).", rendered_video_path.name, len(advisories))
            else:
                logger.info("Clip %s passed all quality checks cleanly. Queued for WAITING_APPROVAL.", rendered_video_path.name)
            return ClipStatus.WAITING_APPROVAL, report
        else:
            if render_pass and sem_pass:
                logger.info("Clip %s passed all quality checks. Auto-promoted to APPROVED.", rendered_video_path.name)
                return ClipStatus.APPROVED, report
            else:
                logger.info("Clip %s has advisories in auto-publish mode. Routing to WAITING_APPROVAL for safety.", rendered_video_path.name)
                return ClipStatus.WAITING_APPROVAL, report
