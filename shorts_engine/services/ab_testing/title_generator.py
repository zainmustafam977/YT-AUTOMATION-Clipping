"""
Title generator for YouTube Shorts.
Generates 4 distinct archetypes within the strict 100-character YouTube Shorts limit:
1. Curiosity Gap
2. Question Hook
3. Bold Statement
4. Numbered Listicle
"""

import json
import logging
from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

from shorts_engine.config.settings import settings

logger = logging.getLogger(__name__)


class TitleArchetype(str, Enum):
    CURIOSITY_GAP = "curiosity_gap"
    QUESTION_HOOK = "question_hook"
    BOLD_STATEMENT = "bold_statement"
    NUMBERED_LISTICLE = "numbered_listicle"


class TitleVariantSet(BaseModel):
    curiosity_gap: str = Field(..., max_length=100)
    question_hook: str = Field(..., max_length=100)
    bold_statement: str = Field(..., max_length=100)
    numbered_listicle: str = Field(..., max_length=100)

    def get_variant(self, archetype: TitleArchetype) -> str:
        return getattr(self, archetype.value)

    def validate_lengths(self) -> bool:
        return all(len(getattr(self, a.value)) <= 100 for a in TitleArchetype)


from shorts_engine.services.llm.router import LLMRouter


class TitleGenerator:
    """
    Generates 4 title variants adhering to YouTube Shorts metadata limits (<= 100 chars).
    Uses Multi-Provider LLMRouter (Groq ⚡ -> Gemini 🤖 -> OpenRouter 🌐 -> OpenAI 🟢) with
    deterministic fallback for offline/test environments.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        router: Optional[LLMRouter] = None,
    ):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.router = router or LLMRouter(gemini_api_key=self.api_key)

    def generate_variants(
        self,
        topic: str,
        hook_text: Optional[str] = None,
        key_point: Optional[str] = None,
        video_id: Optional[str] = None,
    ) -> TitleVariantSet:
        """Generates all 4 archetypes for a given clip context."""
        has_any_key = bool(
            self.router.groq_api_keys
            or self.router.gemini_api_keys
            or self.router.openrouter_api_keys
            or self.router.openai_api_keys
        )
        if has_any_key and self.api_key != "mock-api-key":
            try:
                return self._generate_with_router(topic, hook_text, key_point, video_id)
            except Exception as e:
                logger.warning(f"Router title generation failed: {e}. Using deterministic fallback.")

        return self._generate_fallback(topic, hook_text, key_point)

    def _generate_with_router(
        self,
        topic: str,
        hook_text: Optional[str],
        key_point: Optional[str],
        video_id: Optional[str] = None,
    ) -> TitleVariantSet:
        prompt = (
            f"Generate 4 YouTube Shorts titles for a video about: '{topic}'.\n"
            f"Hook text: '{hook_text or topic}'\n"
            f"Key takeaway: '{key_point or topic}'\n\n"
            "Requirements:\n"
            "- Each title MUST be 100 characters or fewer.\n"
            "- 1. curiosity_gap: creates intense curiosity without clickbait lies.\n"
            "- 2. question_hook: asks an urgent question the viewer needs answered.\n"
            "- 3. bold_statement: contrarian or definitive declaration.\n"
            "- 4. numbered_listicle: uses a specific number (e.g. 3 Ways / Top 5 / Step 1).\n"
            "Return JSON adhering to schema."
        )

        res, meta = self.router.generate_structured(
            prompt=prompt,
            schema=TitleVariantSet,
            temperature=0.7,
            video_id=video_id,
        )
        if res:
            return TitleVariantSet(
                curiosity_gap=res.curiosity_gap[:100],
                question_hook=res.question_hook[:100],
                bold_statement=res.bold_statement[:100],
                numbered_listicle=res.numbered_listicle[:100],
            )
        return self._generate_fallback(topic, hook_text, key_point)

    def _generate_fallback(
        self,
        topic: str,
        hook_text: Optional[str],
        key_point: Optional[str],
    ) -> TitleVariantSet:
        """Deterministic rule-based generator guaranteeing character bounds."""
        clean_topic = (topic or "This").strip().capitalize()
        # Truncate clean_topic if excessively long to keep titles <= 100 chars
        if len(clean_topic) > 40:
            clean_topic = clean_topic[:37] + "..."

        cg = f"The Secret Nobody Tells You About {clean_topic}"[:100]
        qh = f"Why Does Nobody Talk About {clean_topic}?"[:100]
        bs = f"Stop Doing {clean_topic} The Wrong Way"[:100]
        nl = f"3 Simple Steps To Master {clean_topic}"[:100]

        return TitleVariantSet(
            curiosity_gap=cg,
            question_hook=qh,
            bold_statement=bs,
            numbered_listicle=nl,
        )
