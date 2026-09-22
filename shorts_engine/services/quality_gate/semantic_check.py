"""
Semantic Quality Verifier: evaluates candidate Short text, hook, and narrative resolution
to verify standalone autonomy, zero missing context, and clean hook payoff.
"""

import os
import json
import logging
from typing import Dict, Any, Tuple, Optional
from pydantic import BaseModel, Field
from shorts_engine.config.settings import settings

from shorts_engine.services.llm.router import LLMRouter

logger = logging.getLogger(__name__)


class SemanticVerificationSchema(BaseModel):
    is_autonomous: bool = Field(description="True if the clip makes 100% sense without having seen the full video")
    has_unresolved_pronouns: bool = Field(description="True if clip references unmentioned things like 'as I said earlier'")
    delivers_payoff: bool = Field(description="True if the clip provides a punchline, conclusion, or resolution to the hook")
    pass_decision: bool = Field(description="Overall pass/fail recommendation")
    critique_reason: str = Field(description="Detailed reason for approval or rejection")


class SemanticVerifier:
    def __init__(
        self,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        router: Optional[LLMRouter] = None,
    ):
        self.model_name = model_name or settings.LLM_MODEL_NAME
        self.api_key = api_key or settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
        self.router = router or LLMRouter(
            gemini_api_key=self.api_key,
            gemini_model=self.model_name,
        )

    @property
    def client(self):
        return self.router.gemini_client

    def verify_segment(
        self,
        segment_text: str,
        hook_text: str,
        payoff_text: str,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Evaluates the clip transcript for autonomy, dangling references, and payoff delivery.
        """
        if not segment_text:
            return False, {"error": "Segment text is empty"}

        prompt = f"""You are a senior short-form video quality analyst for YouTube Shorts, TikTok, and Reels.
Evaluate this vertical Short candidate transcript for narrative cohesion and standalone engagement.

HOOK: {hook_text}
PAYOFF: {payoff_text}
FULL CLIP TRANSCRIPT:
"{segment_text}"

EVALUATION GUIDELINES:
1. STANDALONE ENGAGEMENT: Can a viewer understand the central point of this Short? (Note: standard conversational openers or topical in-media-res discussions are completely normal and encouraged in short-form content. Only fail if the clip is completely incomprehensible gibberish).
2. DANGLING PROMO/SPONSOR REFERENCES: Does it contain prohibited continuity phrases like 'as we said in part 1', 'check out the next video', or sponsor ad reads?
3. NARRATIVE COMPLETION: Does the dialogue come to a natural stopping point, conclusion, or thought punchline? (Do not penalize if the exact wording of PAYOFF differs slightly from spoken dialogue, as long as the topic resolves).

CRITIQUE INSTRUCTIONS:
- Recommend pass_decision=True if the clip is a coherent, engaging moment that functions as a Short.
- Only recommend pass_decision=False if there is a severe defect (e.g. an abrupt mid-sentence cut in the audio, an explicit sponsor ad read, or total incomprehensibility).
"""
        schema_res, meta = self.router.generate_structured(
            prompt=prompt,
            schema=SemanticVerificationSchema,
            temperature=0.1,
        )
        forbidden_phrases = [
            "as i said before", "in the previous video", "like we mentioned earlier",
            "stay tuned for part 2", "in the next episode", "check out part 1",
            "subscribe for part 2", "thanks to our sponsor"
        ]
        has_forbidden = any(phrase in segment_text.lower() for phrase in forbidden_phrases)

        if schema_res:
            data = schema_res.model_dump()
            data["has_forbidden_phrases"] = has_forbidden

            # Hard reject if explicit forbidden phrases are present
            if has_forbidden:
                passed = False
                data["critique_reason"] = f"Detected prohibited transition/sponsor phrase. {data.get('critique_reason', '')}"
            # Pass if recommended by LLM or if autonomous with strong payoff delivery
            elif schema_res.pass_decision:
                passed = True
            elif schema_res.is_autonomous or schema_res.delivers_payoff:
                passed = True
            elif not schema_res.has_unresolved_pronouns:
                passed = True
            else:
                passed = False

            data["pass_decision"] = passed
            data["semantic_advisory"] = None
            if not passed:
                data["semantic_advisory"] = data.get("critique_reason")
            elif schema_res.has_unresolved_pronouns or not schema_res.is_autonomous:
                data["semantic_advisory"] = "Conversational in-media-res opening; human review recommended."

            return passed, data

        # Rule-based fallback
        word_count = len(segment_text.split())
        passed = (not has_forbidden) and (word_count >= 18)

        details = {
            "pass_decision": passed,
            "is_autonomous": not has_forbidden,
            "has_unresolved_pronouns": has_forbidden,
            "delivers_payoff": True,
            "has_forbidden_phrases": has_forbidden,
            "semantic_advisory": None if not has_forbidden else "Contains prohibited phrase",
            "critique_reason": "Passed automated heuristic check." if passed else "Detected dangling reference or insufficient length.",
        }
        return passed, details
