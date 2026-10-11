"""
A/B Testing Variant Coordinator:
Coordinates title variant generation (via TitleGenerator) and multi-variant thumbnail generation
(via ThumbnailVariantGenerator), structuring candidate A/B variants for Bayesian optimization.
"""

import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session

from shorts_engine.db.models import Clip
from shorts_engine.services.ab_testing.title_generator import TitleGenerator, TitleVariantSet
from shorts_engine.services.ab_testing.thumbnail_generator import ThumbnailVariantGenerator

logger = logging.getLogger(__name__)


class ABVariantCoordinator:
    """Orchestrates generation of coupled title and thumbnail A/B testing variants."""

    def __init__(
        self,
        title_generator: Optional[TitleGenerator] = None,
        thumbnail_generator: Optional[ThumbnailVariantGenerator] = None,
    ):
        self.title_gen = title_generator or TitleGenerator()
        self.thumb_gen = thumbnail_generator or ThumbnailVariantGenerator()

    def generate_clip_variants(
        self,
        clip: Clip,
        video_path: Path,
        output_dir: Path,
        duration_sec: float = 30.0,
        db_session: Optional[Session] = None,
    ) -> List[Dict[str, Any]]:
        """
        Generates 3 paired title and thumbnail variants for the clip,
        stores them in clip.variations, and optionally commits to db_session.
        """
        topic = (clip.candidate.hook_text or "YouTube Shorts") if clip.candidate else "YouTube Shorts"
        hook_text = clip.candidate.hook_text if clip.candidate else None

        # 1. Generate 4 title archetypes
        titles: TitleVariantSet = self.title_gen.generate_variants(
            topic=topic,
            hook_text=hook_text,
            key_point=clip.generated_title or topic,
            video_id=clip.id,
        )

        # 2. Generate 3 thumbnail variants
        thumbs = self.thumb_gen.generate_3_variants(
            video_path=video_path,
            output_dir=output_dir,
            hook_text=hook_text or topic,
            duration_sec=duration_sec,
        )

        # 3. Pair titles and thumbnails into structured variants
        # Variant A: Curiosity Gap title + Hook Impact thumbnail (Active baseline)
        # Variant B: Question Hook title + Curiosity Peak thumbnail
        # Variant C: Bold Statement title + Contrarian Punch thumbnail
        paired_variants = [
            {
                "variant_id": "var_a",
                "archetype": "curiosity_gap",
                "title": titles.curiosity_gap,
                "thumbnail_path": thumbs[0]["thumbnail_path"],
                "thumbnail_style": thumbs[0]["archetype"],
                "impressions": 0,
                "clicks": 0,
                "is_active": True,
            },
            {
                "variant_id": "var_b",
                "archetype": "question_hook",
                "title": titles.question_hook,
                "thumbnail_path": thumbs[1]["thumbnail_path"],
                "thumbnail_style": thumbs[1]["archetype"],
                "impressions": 0,
                "clicks": 0,
                "is_active": False,
            },
            {
                "variant_id": "var_c",
                "archetype": "bold_statement",
                "title": titles.bold_statement,
                "thumbnail_path": thumbs[2]["thumbnail_path"],
                "thumbnail_style": thumbs[2]["archetype"],
                "impressions": 0,
                "clicks": 0,
                "is_active": False,
            },
        ]

        clip.variations = paired_variants
        clip.generated_title = paired_variants[0]["title"]

        if db_session:
            try:
                db_session.add(clip)
                db_session.commit()
            except Exception as e:
                logger.warning("Could not commit clip variants to DB: %s", e)
                db_session.rollback()

        return paired_variants
