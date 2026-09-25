"""
Clip Selection Service: Identifies viral short segments using Gemini 3.8 Flash (or configured LLM),
enforces Pydantic structured output, and snaps boundaries to natural speech pauses.
"""

import os
import json
import logging
from typing import List, Dict, Any, Optional
from shorts_engine.config.settings import settings
from shorts_engine.services.transcription.formatter import FormattedTranscript
from shorts_engine.services.clip_selection.boundaries import BoundarySnapper
from shorts_engine.services.clip_selection.prompts import (
    CandidateSegmentSchema,
    CandidateSegmentsResponse,
    GENERATOR_CRITIC_PROMPT,
)

from shorts_engine.services.llm.router import LLMRouter

import re

logger = logging.getLogger(__name__)


def parse_ai_provenance(reason: Optional[str]) -> Dict[str, Any]:
    """
    Extracts AI provider, model, clean human-readable reason, and visual badge
    from a candidate's recorded reason string.
    Supports tagged format '[provider:model] reason text' as well as legacy/heuristic strings.
    """
    if not reason:
        return {
            "provider": "unknown",
            "model": "unknown",
            "badge": "—",
            "clean_reason": "",
            "is_heuristic": False,
        }

    # Match [provider:model(:latency_ms)?(:failover)?] tag prefix
    m = re.match(r"^\[([a-zA-Z0-9_\-\.]+):([a-zA-Z0-9_\-\./:]+?)(?::(\d+)ms)?(?::(failover))?\]\s*(.*)", reason, re.DOTALL)
    if m:
        provider = m.group(1).lower()
        model = m.group(2)
        lat_ms = int(m.group(3)) if m.group(3) else 0
        is_fo = m.group(4) == "failover"
        clean = m.group(5).strip()
        badge_icons = {
            "groq": "⚡ Groq",
            "gemini": "🤖 Gemini",
            "openrouter": "🌐 OpenRouter",
            "openai": "🟢 OpenAI",
            "heuristic": "⚙️ Algorithmic Heuristic Fallback",
        }
        icon = badge_icons.get(provider, f"🤖 {provider.capitalize()}")
        badge_text = f"{icon} ({model})" if provider != "heuristic" else "⚙️ Algorithmic Heuristic Fallback"
        return {
            "provider": provider,
            "model": model,
            "latency_ms": lat_ms,
            "is_failover": is_fo,
            "badge": badge_text,
            "clean_reason": clean,
            "is_heuristic": provider == "heuristic",
        }

    # Fallback pattern heuristics for legacy records
    lower_r = reason.lower()
    if "heuristic" in lower_r or "sentence-density" in lower_r or "dialogue flow" in lower_r:
        return {
            "provider": "heuristic",
            "model": "algorithmic-fallback",
            "badge": "⚙️ Algorithmic Heuristic Fallback",
            "clean_reason": reason,
            "is_heuristic": True,
        }
    if "openai" in lower_r or "gpt" in lower_r:
        return {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "badge": "🟢 OpenAI (gpt-4o-mini)",
            "clean_reason": reason,
            "is_heuristic": False,
        }

    # Default assumed Gemini if no tag
    return {
        "provider": "gemini",
        "model": "gemini-3.8-flash",
        "badge": "🤖 Gemini (gemini-3.8-flash)",
        "clean_reason": reason,
        "is_heuristic": False,
    }


class SelectedClip:
    def __init__(
        self,
        start_ms: int,
        end_ms: int,
        score: float,
        reason: str,
        hook_text: str,
        payoff_text: str,
        suggested_title: str,
        suggested_description: str,
        suggested_tags: List[str],
        segment_text: str,
        ai_provider: Optional[str] = None,
        ai_model: Optional[str] = None,
    ):
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.score = score
        self.reason = reason
        self.hook_text = hook_text
        self.payoff_text = payoff_text
        self.suggested_title = suggested_title
        self.suggested_description = suggested_description
        self.suggested_tags = suggested_tags
        self.segment_text = segment_text

        prov = parse_ai_provenance(reason)
        self.ai_provider = ai_provider or prov["provider"]
        self.ai_model = ai_model or prov["model"]
        self.ai_badge = prov["badge"]
        self.clean_reason = prov["clean_reason"]
        self.is_heuristic = prov["is_heuristic"]

    @property
    def duration_sec(self) -> float:
        return (self.end_ms - self.start_ms) / 1000.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "duration_sec": round(self.duration_sec, 2),
            "score": round(self.score, 2),
            "reason": self.reason,
            "clean_reason": self.clean_reason,
            "ai_provider": self.ai_provider,
            "ai_model": self.ai_model,
            "ai_badge": self.ai_badge,
            "is_heuristic": self.is_heuristic,
            "hook_text": self.hook_text,
            "payoff_text": self.payoff_text,
            "suggested_title": self.suggested_title,
            "suggested_description": self.suggested_description,
            "suggested_tags": self.suggested_tags,
            "segment_text": self.segment_text,
        }

    def __repr__(self) -> str:
        return (
            f"<SelectedClip [{self.start_ms}ms-{self.end_ms}ms] "
            f"score={self.score:.2f} title='{self.suggested_title}'>"
        )


class ClipSelectionService:
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
        self.snapper = BoundarySnapper(
            min_duration_sec=settings.TARGET_DURATION_MIN_SEC,
            max_duration_sec=settings.TARGET_DURATION_MAX_SEC,
        )

    @property
    def client(self):
        return self.router.gemini_client

    def _build_transcript_prompt_text(self, transcript: FormattedTranscript) -> str:
        """Formats the transcript with grouped timestamp markers every ~15 seconds."""
        lines = []
        words = transcript.words
        if not words:
            return ""

        current_line = []
        line_start = words[0].start
        for w in words:
            current_line.append(w.word)
            if (w.end - line_start) >= 10.0 or w.word.endswith((".", "!", "?")):
                lines.append(f"[{line_start:.1f}s - {w.end:.1f}s] {' '.join(current_line)}")
                current_line = []
                line_start = w.end
        if current_line:
            lines.append(f"[{line_start:.1f}s - {words[-1].end:.1f}s] {' '.join(current_line)}")

        return "\n".join(lines)

    def select_clips(
        self,
        transcript: FormattedTranscript,
        min_clips: Optional[int] = None,
        max_clips: Optional[int] = None,
        target_duration_min: Optional[int] = None,
        target_duration_max: Optional[int] = None,
        provider_override: Optional[str] = None,
        model_override: Optional[str] = None,
        temperature_override: Optional[float] = None,
    ) -> List[SelectedClip]:
        """
        Extracts top short candidates using Gemini/Groq/OpenRouter/OpenAI (or heuristic fallback),
        respecting per-video runtime overrides for duration, bounds, and LLM preferences.
        """
        if not transcript.words:
            return []

        eff_min_clips = min_clips if min_clips is not None else settings.MIN_SEGMENTS_PER_VIDEO
        eff_max_clips = max_clips if max_clips is not None else settings.MAX_SEGMENTS_PER_VIDEO
        eff_min_dur = target_duration_min if target_duration_min is not None else settings.TARGET_DURATION_MIN_SEC
        eff_max_dur = target_duration_max if target_duration_max is not None else settings.TARGET_DURATION_MAX_SEC
        eff_temp = temperature_override if temperature_override is not None else getattr(settings, "LLM_TEMPERATURE", 0.3)

        candidates_raw: List[CandidateSegmentSchema] = []

        prompt = GENERATOR_CRITIC_PROMPT.format(
            min_count=eff_min_clips,
            max_count=eff_max_clips,
            min_dur=eff_min_dur,
            max_dur=eff_max_dur,
            transcript_text=self._build_transcript_prompt_text(transcript),
        )

        active_router = self.router
        if provider_override or model_override:
            prov = (provider_override or self.router.provider).lower()
            active_router = LLMRouter(
                provider=prov,
                gemini_model=model_override if prov in ("gemini", "auto") else self.router.gemini_model,
                groq_model=model_override if prov == "groq" else self.router.groq_model,
                openrouter_model=model_override if prov == "openrouter" else self.router.openrouter_model,
                openai_model=model_override if prov == "openai" else self.router.openai_model,
            )

        resp_obj, meta = active_router.generate_structured(
            prompt=prompt,
            schema=CandidateSegmentsResponse,
            temperature=eff_temp,
        )

        provider = "gemini"
        model = settings.LLM_MODEL_NAME

        if resp_obj and resp_obj.candidates:
            candidates_raw = resp_obj.candidates
            provider = meta.get("provider") or "gemini"
            model = meta.get("model") or settings.LLM_MODEL_NAME
            lat = meta.get("latency_ms", 0)
            is_fo = meta.get("is_failover", False) or meta.get("failover", False)
            tag_suffix = f":{lat}ms" if lat > 0 else ""
            if is_fo:
                tag_suffix += ":failover"
            for c in candidates_raw:
                if not c.reason.startswith("["):
                    c.reason = f"[{provider}:{model}{tag_suffix}] {c.reason}"
            logger.info(
                "Selected %d candidates using provider '%s' (model: %s, latency: %dms, failover=%s)",
                len(candidates_raw),
                provider,
                model,
                lat,
                is_fo,
            )
        else:
            logger.info(
                "LLM selection unavailable or failed (%s). Using heuristic clip selection.",
                meta.get("error", "no response"),
            )
            provider = "heuristic"
            model = "algorithmic-fallback"
            candidates_raw = self._heuristic_select(transcript, eff_min_clips, eff_max_clips)
            for c in candidates_raw:
                if not c.reason.startswith("["):
                    c.reason = f"[{provider}:{model}:0ms:failover] {c.reason}"

        # Snap all candidates to natural speech boundaries
        snapper = (
            BoundarySnapper(min_duration_sec=eff_min_dur, max_duration_sec=eff_max_dur)
            if (eff_min_dur != settings.TARGET_DURATION_MIN_SEC or eff_max_dur != settings.TARGET_DURATION_MAX_SEC)
            else self.snapper
        )

        selected_clips: List[SelectedClip] = []
        for cand in candidates_raw:
            # Pre-clamp raw candidate duration if it exceeds allowed maximum
            raw_dur = cand.end_sec - cand.start_sec
            if raw_dur > (eff_max_dur + 1.5):
                cand.end_sec = cand.start_sec + eff_max_dur

            start_ms, end_ms, seg_text = snapper.snap_boundaries(
                cand.start_sec, cand.end_sec, transcript
            )
            duration_sec = (end_ms - start_ms) / 1000.0
            eff_max_allowed = min(180.0, eff_max_dur + 1.5)

            if eff_min_dur <= duration_sec <= eff_max_allowed:
                # Ensure hook_text and payoff_text anchor to genuine spoken dialogue in seg_text
                sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', seg_text) if s.strip()]
                effective_hook = cand.hook_text or (sentences[0] if sentences else "")
                effective_payoff = cand.payoff_text or (sentences[-1] if sentences else "")

                selected_clips.append(
                    SelectedClip(
                        start_ms=start_ms,
                        end_ms=end_ms,
                        score=cand.score,
                        reason=cand.reason,
                        hook_text=effective_hook,
                        payoff_text=effective_payoff,
                        suggested_title=cand.suggested_title[:100],
                        suggested_description=cand.suggested_description[:5000],
                        suggested_tags=cand.suggested_tags,
                        segment_text=seg_text,
                        ai_provider=provider,
                        ai_model=model,
                    )
                )

        # Sort by score descending
        selected_clips.sort(key=lambda x: x.score, reverse=True)
        return selected_clips[:max_clips]

    def _heuristic_select(
        self,
        transcript: FormattedTranscript,
        min_clips: int,
        max_clips: int,
    ) -> List[CandidateSegmentSchema]:
        """
        Algorithmic fallback: scans transcript for sentence density, question marks,
        and high-engagement transition phrases (e.g. 'the secret', 'never', 'why').
        Extracts genuine declarative/question hooks and concluding punchlines from actual dialogue.
        """
        words = transcript.words
        total_duration = words[-1].end if words else 0.0
        if total_duration < settings.TARGET_DURATION_MIN_SEC:
            full_text = " ".join(w.word for w in words)
            return [
                CandidateSegmentSchema(
                    start_sec=0.0,
                    end_sec=total_duration,
                    hook_text=words[0].word if words else "",
                    payoff_text=words[-1].word if words else "",
                    score=0.85,
                    reason="Full source video within Shorts duration limit.",
                    suggested_title=f"{full_text[:50]}... #Shorts" if len(full_text) > 50 else f"{full_text} #Shorts",
                    suggested_description=f"Key highlights from the video: {full_text[:200]} #Shorts #Insight",
                    suggested_tags=["#shorts", "#viral", "#highlights"],
                )
            ]

        # Partition into target windows (~30s to 50s each)
        step = max(15.0, (total_duration - settings.TARGET_DURATION_MAX_SEC) / max(1, max_clips))
        results = []
        curr_start = 0.0
        clip_idx = 1

        high_energy_keywords = {"secret", "never", "why", "truth", "mistake", "always", "critical", "warning", "insane", "how", "what", "remember"}

        while curr_start + settings.TARGET_DURATION_MIN_SEC <= total_duration and len(results) < max_clips:
            clip_end = min(total_duration, curr_start + settings.TARGET_DURATION_MAX_SEC)
            win_words = [w for w in words if w.start >= curr_start and w.end <= clip_end]

            if win_words:
                # Group words into sentences based on punctuation
                sentences = []
                cur_s = []
                for w in win_words:
                    cur_s.append(w.word)
                    if w.word.endswith((".", "?", "!")):
                        sentences.append(" ".join(cur_s))
                        cur_s = []
                if cur_s:
                    sentences.append(" ".join(cur_s))

                # Identify best hook: look for questions or high-energy words in opening sentences
                hook_cand = sentences[0] if sentences else " ".join(w.word for w in win_words[:10])
                for s in sentences[:3]:
                    if any(kw in s.lower() for kw in high_energy_keywords) or s.strip().endswith("?"):
                        hook_cand = s
                        break

                # Identify best payoff: concluding sentence
                payoff_cand = sentences[-1] if len(sentences) > 1 else (sentences[0] if sentences else " ".join(w.word for w in win_words[-10:]))

                # Clean up dangling filler words from hook start
                clean_hook = hook_cand.strip()
                for filler in ["And ", "So ", "Like, ", "Well, ", "You know, ", "I mean, "]:
                    if clean_hook.startswith(filler):
                        clean_hook = clean_hook[len(filler):].strip()

                hook_text = clean_hook[:120] if clean_hook else f"Key moment at {int(curr_start)}s"
                payoff_text = payoff_cand.strip()[:150] if payoff_cand else f"Resolution for {hook_text[:50]}"

                clean_title_hook = re.sub(r"[^\w\s]", "", hook_text).strip()
                title_words = clean_title_hook.split()[:7]
                suggested_title = f"{' '.join(title_words).title()} #Shorts"[:95] if title_words else f"Key Insight Part {clip_idx} #Shorts"

                results.append(
                    CandidateSegmentSchema(
                        start_sec=curr_start,
                        end_sec=clip_end,
                        hook_text=hook_text,
                        payoff_text=payoff_text,
                        score=round(0.88 - (clip_idx * 0.02), 2),
                        reason="Selected via sentence-density and extractive dialogue heuristic.",
                        suggested_title=suggested_title,
                        suggested_description=f"{hook_text} — {payoff_text}\n\n#Shorts #Highlights #Viral",
                        suggested_tags=["#shorts", "#highlights", "#clip", "#viral"],
                    )
                )

            curr_start += step
            clip_idx += 1

        return results[:max_clips]
