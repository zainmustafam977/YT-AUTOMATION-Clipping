"""
Clip Selection Service: Multi-Pass AI Virality Hunter & Yield Maximizer.
Pass 1: Acoustic energy & pitch dynamic peak detection (ebur128 momentary loudness spikes).
Pass 2: 50% overlapping sliding-window transcript segmentation with boundary snapping.
Pass 3: Multi-criteria evaluation (Curiosity Gap, Narrative Climax, Standalone Coherence, Loopability).
"""

import os
import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

from shorts_engine.config.settings import settings
from shorts_engine.services.transcription.formatter import FormattedTranscript
from shorts_engine.services.clip_selection.boundaries import BoundarySnapper
from shorts_engine.services.clip_selection.prompts import (
    CandidateSegmentSchema,
    CandidateSegmentsResponse,
    GENERATOR_CRITIC_PROMPT,
    compute_composite_virality_score,
)
from shorts_engine.services.llm.router import LLMRouter
from shorts_engine.services.execution.watchdog import SubprocessWatchdog

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

    lower_r = reason.lower()
    if "heuristic" in lower_r or "sentence-density" in lower_r or "dialogue flow" in lower_r or "sliding-window" in lower_r:
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
        curiosity_gap_score: Optional[float] = None,
        narrative_climax_score: Optional[float] = None,
        standalone_coherence_score: Optional[float] = None,
        loopability_score: Optional[float] = None,
        composite_score: Optional[float] = None,
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

        self.curiosity_gap_score = float(curiosity_gap_score or 0.0)
        self.narrative_climax_score = float(narrative_climax_score or 0.0)
        self.standalone_coherence_score = float(standalone_coherence_score or 0.0)
        self.loopability_score = float(loopability_score or 0.0)
        self.composite_score = float(composite_score if composite_score is not None else score)

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
            "curiosity_gap_score": round(self.curiosity_gap_score, 1),
            "narrative_climax_score": round(self.narrative_climax_score, 1),
            "standalone_coherence_score": round(self.standalone_coherence_score, 1),
            "loopability_score": round(self.loopability_score, 1),
            "composite_score": round(self.composite_score, 2),
        }

    def __repr__(self) -> str:
        return (
            f"<SelectedClip [{self.start_ms}ms-{self.end_ms}ms] "
            f"score={self.score:.2f} title='{self.suggested_title}'>"
        )


class AcousticPeakDetector:
    """
    Pass 1: Acoustic energy & pitch dynamic peak detection.
    Analyzes momentary loudness spikes (EBU R128 M), laughter bursts, and volume inflections
    using FFmpeg ebur128 audio telemetry.
    """

    @classmethod
    def detect_acoustic_peaks(
        cls,
        media_path: Optional[Path],
        min_peak_gap_sec: float = 3.0,
        lufs_threshold: float = -22.0,
    ) -> List[Dict[str, Any]]:
        """
        Extracts momentary loudness spikes from media audio stream.
        Returns:
            [{"timestamp_sec": float, "momentary_lufs": float, "energy_score": float (0-10)}, ...]
        """
        if not media_path or not Path(media_path).exists():
            return []

        cmd = [
            "ffmpeg",
            "-v", "info",
            "-i", str(media_path),
            "-af", "ebur128=metadata=1,ametadata=print:key=lavfi.r128.M:file=-",
            "-f", "null",
            "-",
        ]
        try:
            proc = SubprocessWatchdog.run(cmd, timeout_sec=60.0, check=False)
            output = proc.stdout + "\n" + proc.stderr
            pts_matches = list(re.finditer(r"pts_time:([0-9.]+)", output))
            m_matches = list(re.finditer(r"lavfi\.r128\.M=(-?[0-9.]+)", output))

            times = [float(m.group(1)) for m in pts_matches]
            lufs = [float(m.group(1)) for m in m_matches]

            points = list(zip(times, lufs))
            if not points:
                return []

            peaks = []
            last_peak_t = -999.0
            for t, val in points:
                if val >= lufs_threshold and (t - last_peak_t) >= min_peak_gap_sec:
                    energy_score = round(max(1.0, min(10.0, (val + 30.0) / 2.0)), 1)
                    peaks.append({
                        "timestamp_sec": round(t, 2),
                        "momentary_lufs": round(val, 2),
                        "energy_score": energy_score,
                    })
                    last_peak_t = t

            return peaks
        except Exception as e:
            logger.debug("Acoustic peak detection failed for %s: %s", media_path, e)
            return []


class SlidingWindowSegmenter:
    """
    Pass 2: 50% overlapping sliding-window transcript segmentation.
    Divides word transcript into 30-60s target chunks with 50% step overlap,
    snaps start/end to natural sentence and word pause boundaries,
    and correlates windows with acoustic energy peaks from Pass 1.
    """

    @classmethod
    def generate_candidate_windows(
        cls,
        transcript: FormattedTranscript,
        target_min_sec: float = 30.0,
        target_max_sec: float = 60.0,
        overlap_ratio: float = 0.50,
        acoustic_peaks: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        words = transcript.words
        if not words:
            return []

        total_dur = words[-1].end
        if total_dur <= target_min_sec:
            return [{
                "start_sec": 0.0,
                "end_sec": total_dur,
                "duration_sec": round(total_dur, 2),
                "text": " ".join(w.word for w in words),
                "words": words,
                "acoustic_score": 5.0,
                "has_acoustic_peak": False,
            }]

        target_window = min(target_max_sec, max(target_min_sec, 45.0))
        step = max(10.0, target_window * (1.0 - overlap_ratio))

        peaks = acoustic_peaks or []
        windows: List[Dict[str, Any]] = []
        curr_start = 0.0
        snapper = BoundarySnapper(min_duration_sec=target_min_sec, max_duration_sec=target_max_sec)

        while curr_start + target_min_sec <= total_dur + 5.0:
            raw_end = min(total_dur, curr_start + target_window)
            start_ms, end_ms, seg_text = snapper.snap_boundaries(curr_start, raw_end, transcript)
            st_sec = start_ms / 1000.0
            en_sec = end_ms / 1000.0
            dur = en_sec - st_sec

            if target_min_sec <= dur <= (target_max_sec + 2.0):
                matching_peaks = [
                    p for p in peaks if st_sec <= p.get("timestamp_sec", 0.0) <= en_sec
                ]
                ac_score = max([p.get("energy_score", 5.0) for p in matching_peaks], default=5.0)

                if not any(abs(w["start_sec"] - st_sec) < 1.0 and abs(w["end_sec"] - en_sec) < 1.0 for w in windows):
                    win_words = [w for w in words if st_sec <= w.start and w.end <= en_sec]
                    windows.append({
                        "start_sec": st_sec,
                        "end_sec": en_sec,
                        "duration_sec": round(dur, 2),
                        "text": seg_text,
                        "words": win_words,
                        "acoustic_score": ac_score,
                        "has_acoustic_peak": len(matching_peaks) > 0,
                    })

            curr_start += step
            if curr_start >= total_dur:
                break

        return windows


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
        media_path: Optional[Path] = None,
    ) -> List[SelectedClip]:
        """
        Multi-Pass AI Virality Hunter:
        Pass 1: Acoustic energy & dynamic peak detection.
        Pass 2: 50% overlapping sliding-window transcript segmentation.
        Pass 3: Multi-criteria evaluation (Curiosity, Climax, Coherence, Loopability).
        """
        if not transcript.words:
            return []

        eff_min_clips = min_clips if min_clips is not None else settings.MIN_SEGMENTS_PER_VIDEO
        eff_max_clips = max_clips if max_clips is not None else settings.MAX_SEGMENTS_PER_VIDEO
        eff_min_dur = target_duration_min if target_duration_min is not None else settings.TARGET_DURATION_MIN_SEC
        eff_max_dur = target_duration_max if target_duration_max is not None else settings.TARGET_DURATION_MAX_SEC
        eff_temp = temperature_override if temperature_override is not None else getattr(settings, "LLM_TEMPERATURE", 0.3)

        # Pass 1: Acoustic energy & pitch dynamic peak detection
        acoustic_peaks = AcousticPeakDetector.detect_acoustic_peaks(media_path) if media_path else []
        if acoustic_peaks:
            logger.info("Pass 1: Detected %d acoustic loudness peaks in source audio.", len(acoustic_peaks))

        # Pass 2: 50% overlapping sliding-window segmentation
        _ = SlidingWindowSegmenter.generate_candidate_windows(
            transcript=transcript,
            target_min_sec=eff_min_dur,
            target_max_sec=eff_max_dur,
            overlap_ratio=0.50,
            acoustic_peaks=acoustic_peaks,
        )

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
                # Re-compute composite virality score if criteria provided
                if any(v is not None for v in (c.curiosity_gap_score, c.narrative_climax_score, c.standalone_coherence_score, c.loopability_score)):
                    cur = c.curiosity_gap_score if c.curiosity_gap_score is not None else 8.0
                    clm = c.narrative_climax_score if c.narrative_climax_score is not None else 7.5
                    coh = c.standalone_coherence_score if c.standalone_coherence_score is not None else 8.0
                    loop = c.loopability_score if c.loopability_score is not None else 7.0
                    c.score = compute_composite_virality_score(cur, clm, coh, loop)
            logger.info(
                "Pass 3: Selected %d candidates via provider '%s' (model: %s, latency: %dms, failover=%s)",
                len(candidates_raw),
                provider,
                model,
                lat,
                is_fo,
            )
        else:
            logger.info(
                "LLM selection unavailable or failed (%s). Using multi-pass heuristic clip selection.",
                meta.get("error", "no response"),
            )
            provider = "heuristic"
            model = "algorithmic-fallback"
            candidates_raw = self._heuristic_select(
                transcript,
                eff_min_clips,
                eff_max_clips,
                acoustic_peaks=acoustic_peaks,
            )
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
            raw_dur = cand.end_sec - cand.start_sec
            if raw_dur > (eff_max_dur + 1.5):
                cand.end_sec = cand.start_sec + eff_max_dur

            start_ms, end_ms, seg_text = snapper.snap_boundaries(
                cand.start_sec, cand.end_sec, transcript
            )
            duration_sec = (end_ms - start_ms) / 1000.0
            eff_max_allowed = min(180.0, eff_max_dur + 1.5)

            if eff_min_dur <= duration_sec <= eff_max_allowed:
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
                        curiosity_gap_score=cand.curiosity_gap_score or 8.0,
                        narrative_climax_score=cand.narrative_climax_score or 7.5,
                        standalone_coherence_score=cand.standalone_coherence_score or 8.0,
                        loopability_score=cand.loopability_score or 7.0,
                        composite_score=cand.score,
                    )
                )

        selected_clips.sort(key=lambda x: x.score, reverse=True)
        return selected_clips[:max_clips]

    def _heuristic_select(
        self,
        transcript: FormattedTranscript,
        min_clips: int,
        max_clips: int,
        acoustic_peaks: Optional[List[Dict[str, Any]]] = None,
    ) -> List[CandidateSegmentSchema]:
        """
        Algorithmic fallback: scans transcript using 50% overlapping sliding windows,
        detects curiosity gap hooks, narrative payoffs, and computes multi-criteria scores.
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
                    curiosity_gap_score=8.5,
                    narrative_climax_score=8.0,
                    standalone_coherence_score=9.0,
                    loopability_score=7.5,
                )
            ]

        candidate_windows = SlidingWindowSegmenter.generate_candidate_windows(
            transcript=transcript,
            target_min_sec=settings.TARGET_DURATION_MIN_SEC,
            target_max_sec=settings.TARGET_DURATION_MAX_SEC,
            overlap_ratio=0.50,
            acoustic_peaks=acoustic_peaks,
        )

        high_energy_keywords = {"secret", "never", "why", "truth", "mistake", "always", "critical", "warning", "insane", "how", "what", "remember"}
        climax_keywords = {"finally", "realize", "truth", "result", "proved", "key", "remember", "never", "impossible"}

        results: List[CandidateSegmentSchema] = []
        clip_idx = 1

        for win in candidate_windows:
            win_words = win["words"]
            if not win_words:
                continue

            sentences = []
            cur_s = []
            for w in win_words:
                cur_s.append(w.word)
                if w.word.endswith((".", "?", "!")):
                    sentences.append(" ".join(cur_s))
                    cur_s = []
            if cur_s:
                sentences.append(" ".join(cur_s))

            hook_cand = sentences[0] if sentences else " ".join(w.word for w in win_words[:10])
            for s in sentences[:3]:
                if any(kw in s.lower() for kw in high_energy_keywords) or s.strip().endswith("?"):
                    hook_cand = s
                    break

            payoff_cand = sentences[-1] if len(sentences) > 1 else (sentences[0] if sentences else " ".join(w.word for w in win_words[-10:]))

            clean_hook = hook_cand.strip()
            for filler in ["And ", "So ", "Like, ", "Well, ", "You know, ", "I mean, "]:
                if clean_hook.startswith(filler):
                    clean_hook = clean_hook[len(filler):].strip()

            hook_text = clean_hook[:120] if clean_hook else f"Key moment at {int(win['start_sec'])}s"
            payoff_text = payoff_cand.strip()[:150] if payoff_cand else f"Resolution for {hook_text[:50]}"

            clean_title_hook = re.sub(r"[^\w\s]", "", hook_text).strip()
            title_words = clean_title_hook.split()[:7]
            suggested_title = f"{' '.join(title_words).title()} #Shorts"[:95] if title_words else f"Key Insight Part {clip_idx} #Shorts"

            has_question = hook_text.endswith("?")
            has_energy_hook = any(kw in hook_text.lower() for kw in high_energy_keywords)
            curiosity_gap = 9.0 if (has_question and has_energy_hook) else (8.5 if (has_question or has_energy_hook) else 7.0)

            has_excl = payoff_text.endswith("!")
            has_climax_kw = any(kw in payoff_text.lower() for kw in climax_keywords)
            narrative_climax = 9.0 if (has_excl and has_climax_kw) else (8.0 if (has_excl or has_climax_kw) else 7.0)

            dur = win["duration_sec"]
            standalone_coherence = 9.0 if (dur >= 20.0 and len(sentences) >= 2) else 8.0

            hook_words_set = set(clean_title_hook.lower().split())
            payoff_words_set = set(re.sub(r"[^\w\s]", "", payoff_text).lower().split())
            shared_vocab = hook_words_set.intersection(payoff_words_set)
            loopability = 8.5 if len(shared_vocab) >= 1 else 7.0

            ac_boost = win.get("acoustic_score", 5.0)
            composite_score = compute_composite_virality_score(
                curiosity_gap=curiosity_gap,
                narrative_climax=narrative_climax,
                standalone_coherence=standalone_coherence,
                loopability=loopability,
                acoustic_boost=ac_boost,
            )

            results.append(
                CandidateSegmentSchema(
                    start_sec=win["start_sec"],
                    end_sec=win["end_sec"],
                    hook_text=hook_text,
                    payoff_text=payoff_text,
                    score=composite_score,
                    reason="Selected via 50% overlapping sliding-window multi-criteria virality heuristic.",
                    suggested_title=suggested_title,
                    suggested_description=f"{hook_text} — {payoff_text}\n\n#Shorts #Highlights #Viral",
                    suggested_tags=["#shorts", "#highlights", "#clip", "#viral"],
                    curiosity_gap_score=curiosity_gap,
                    narrative_climax_score=narrative_climax,
                    standalone_coherence_score=standalone_coherence,
                    loopability_score=loopability,
                )
            )
            clip_idx += 1

        results.sort(key=lambda c: c.score, reverse=True)
        return results[:max_clips]
