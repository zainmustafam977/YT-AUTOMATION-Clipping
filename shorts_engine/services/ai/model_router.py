"""
Context-Aware Dynamic Model Selection Router.
Analyzes video duration, transcript token count, and language complexity to pick
the optimal AI model (Groq LPU vs Gemini 2.0 Flash vs OpenAI vs Heuristic).
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Dict, Any

from shorts_engine.services.events.bus import global_event_bus
from shorts_engine.services.events.models import PlatformEvent

logger = logging.getLogger(__name__)


@dataclass
class VideoAnalysisContext:
    """Input context describing video characteristics for intelligent AI model routing."""
    duration_sec: float
    transcript_token_count: int
    detected_language: str = "en"
    audio_snr_db: Optional[float] = None
    has_multiple_speakers: Optional[bool] = None
    target_platform: Optional[str] = "youtube_shorts"


@dataclass
class ModelRouteResult:
    """Result returned by DynamicModelRouter specifying target provider and fallback chain."""
    provider: str                      # 'groq', 'gemini', 'openai', 'heuristic'
    model_name: str                    # e.g. 'llama-3.3-70b-versatile', 'gemini-2.0-flash'
    rationale: str
    estimated_tokens: int
    fallback_provider: Optional[str] = None
    fallback_model: Optional[str] = None
    candidate_chain: List[Tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model_name": self.model_name,
            "rationale": self.rationale,
            "estimated_tokens": self.estimated_tokens,
            "fallback_provider": self.fallback_provider,
            "fallback_model": self.fallback_model,
            "candidate_chain": self.candidate_chain,
        }


class DynamicModelRouter:
    """
    Context-aware model selector implementing C-04 and C-16 resilience rules:
    - High-speed Groq LPU for short, punchy content (<5 min, <4k tokens, English).
    - Gemini 2.0 Flash for long-form podcasts (>15 min, >12k tokens) or non-English speech.
    - Tiered fallback failover chain when providers hit rate limits or cooldowns.
    """

    ENGLISH_LANGS = {"en", "eng", "english"}

    # Default model identifiers
    GROQ_MODEL = "llama-3.3-70b-versatile"
    GEMINI_MODEL = "gemini-2.0-flash"
    OPENAI_MODEL = "gpt-4o"
    HEURISTIC_MODEL = "algorithmic_v2"

    @classmethod
    def select_optimal_model(cls, ctx: VideoAnalysisContext) -> ModelRouteResult:
        """
        Determines the optimal AI provider and candidate fallback chain based on video characteristics.
        """
        lang = (ctx.detected_language or "en").strip().lower()
        duration = max(0.0, float(ctx.duration_sec)) if ctx.duration_sec else 0.0
        tokens = max(0, int(ctx.transcript_token_count)) if ctx.transcript_token_count else 0
        est_tokens = max(tokens + 1500, 2000)

        # Check if language is English (including dialect codes e.g. en-US, en-GB, en_US)
        is_english = (
            lang in cls.ENGLISH_LANGS
            or lang.startswith("en-")
            or lang.startswith("en_")
        )

        # Rule 1: High Token Count / Long-form Content (>12,000 tokens or >15 minutes)
        if tokens > 12000 or duration > 900.0:
            return ModelRouteResult(
                provider="gemini",
                model_name=cls.GEMINI_MODEL,
                rationale="Long-form content requires Gemini's large 1M+ token context window for global narrative coherence.",
                estimated_tokens=est_tokens,
                fallback_provider="openai",
                fallback_model=cls.OPENAI_MODEL,
                candidate_chain=[
                    ("gemini", cls.GEMINI_MODEL),
                    ("openai", cls.OPENAI_MODEL),
                    ("groq", cls.GROQ_MODEL),
                ],
            )

        # Rule 2: Non-English Content
        if not is_english:
            return ModelRouteResult(
                provider="gemini",
                model_name=cls.GEMINI_MODEL,
                rationale=f"Detected language '{ctx.detected_language}'. Gemini selected for superior multilingual understanding and nuance.",
                estimated_tokens=est_tokens,
                fallback_provider="openai",
                fallback_model=cls.OPENAI_MODEL,
                candidate_chain=[
                    ("gemini", cls.GEMINI_MODEL),
                    ("openai", cls.OPENAI_MODEL),
                    ("groq", cls.GROQ_MODEL),
                ],
            )

        # Rule 3: Short & Fast Viral Content (< 5 min / 300s, < 4000 tokens, English)
        if duration <= 300.0 and tokens <= 4000:
            return ModelRouteResult(
                provider="groq",
                model_name=cls.GROQ_MODEL,
                rationale="High-speed Groq LPU selected for instantaneous curiosity-gap hook analysis and sub-second generation.",
                estimated_tokens=est_tokens,
                fallback_provider="gemini",
                fallback_model=cls.GEMINI_MODEL,
                candidate_chain=[
                    ("groq", cls.GROQ_MODEL),
                    ("gemini", cls.GEMINI_MODEL),
                    ("openai", cls.OPENAI_MODEL),
                ],
            )

        # Rule 4: Mid-tier standard content (5-15 min, 4k-12k tokens, English)
        return ModelRouteResult(
            provider="gemini",
            model_name=cls.GEMINI_MODEL,
            rationale="Standard length content routed to Gemini 2.0 Flash for balanced throughput and context size.",
            estimated_tokens=est_tokens,
            fallback_provider="groq",
            fallback_model=cls.GROQ_MODEL,
            candidate_chain=[
                ("gemini", cls.GEMINI_MODEL),
                ("groq", cls.GROQ_MODEL),
                ("openai", cls.OPENAI_MODEL),
            ],
        )

    @classmethod
    def select_model_with_vault(
        cls,
        ctx: VideoAnalysisContext,
        vault: Any,  # ApiKeyVault
    ) -> Tuple[ModelRouteResult, Any]:
        """
        Attempts to acquire a key from ApiKeyVault by traversing the optimal model's candidate chain.
        If all external AI keys are unavailable, falls back to algorithmic heuristic segmentation.
        """
        route = cls.select_optimal_model(ctx)

        # Traverse candidate chain
        for idx, (prov, model) in enumerate(route.candidate_chain):
            try:
                key = vault.acquire_key(provider=prov, estimated_tokens=route.estimated_tokens)
                if key:
                    next_cand = route.candidate_chain[idx + 1] if idx + 1 < len(route.candidate_chain) else (None, None)
                    selected_route = ModelRouteResult(
                        provider=prov,
                        model_name=model,
                        rationale=route.rationale if prov == route.provider else f"Fell back from {route.provider} to {prov} due to key availability.",
                        estimated_tokens=route.estimated_tokens,
                        fallback_provider=next_cand[0],
                        fallback_model=next_cand[1],
                        candidate_chain=route.candidate_chain,
                    )
                    return selected_route, key
            except Exception as e:
                logger.debug("Provider '%s' unavailable in vault: %s", prov, e)

        # Fallback to algorithmic heuristic
        heuristic_route = ModelRouteResult(
            provider="heuristic",
            model_name=cls.HEURISTIC_MODEL,
            rationale="All configured external AI providers are cooling down or exhausted. Activated algorithmic heuristic fallback.",
            estimated_tokens=0,
            fallback_provider=None,
            fallback_model=None,
            candidate_chain=[],
        )

        try:
            event = PlatformEvent(
                category="analysis",
                level="WARNING",
                title="AI Providers Exhausted: Heuristic Fallback",
                message="All LLM API keys exhausted or rate-limited. Running rule-based clip detection.",
            )
            global_event_bus.publish_sync(event)
        except Exception:
            pass

        return heuristic_route, None
