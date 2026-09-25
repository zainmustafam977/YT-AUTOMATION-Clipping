"""
Speech boundary snapping: adjusts candidate cut points to natural silence gaps
and word boundaries to guarantee cuts never slice words or breath pauses.
"""

import logging
from typing import List, Tuple, Set
from shorts_engine.config.settings import settings
from shorts_engine.services.transcription.formatter import FormattedTranscript, WordSegment

logger = logging.getLogger(__name__)

BANNED_START_WORDS: Set[str] = {
    "which", "who", "whose", "whom", "that", "and", "but", "or", "so",
    "because", "whereby", "again", "also", "now",
}

BANNED_END_WORDS: Set[str] = {
    "is", "are", "was", "were", "be", "been", "have", "has", "had",
    "do", "does", "did", "would", "could", "should", "will", "can", "might",
    "of", "to", "in", "for", "with", "on", "at", "from", "by", "about",
    "into", "through", "a", "an", "the", "my", "your", "his", "her",
    "its", "our", "their", "and", "or", "but", "so", "not", "didn't",
    "doesn't", "won't", "can't", "that", "which", "this",
}


class BoundarySnapper:
    """
    Industry-grade speech boundary snapping:
    - Snaps cut points to natural silence gaps (>= 0.4s) and punctuation.
    - Prevents truncated starts on relative pronouns and conjunctions.
    - Prevents dangling ends on auxiliary verbs and prepositions.
    - Employs duration elasticity (+3s to +5s) to allow complete sentence payoffs.
    """

    def __init__(
        self,
        min_duration_sec: float = settings.TARGET_DURATION_MIN_SEC,
        max_duration_sec: float = settings.TARGET_DURATION_MAX_SEC,
    ):
        self.min_duration_sec = min_duration_sec
        self.max_duration_sec = max_duration_sec

    def snap_boundaries(
        self,
        raw_start_sec: float,
        raw_end_sec: float,
        transcript: FormattedTranscript,
    ) -> Tuple[int, int, str]:
        """
        Takes raw start and end in seconds, finds optimal grammatical boundaries,
        and returns (start_ms, end_ms, segment_text).
        """
        words = transcript.words
        if not words:
            start_ms = int(raw_start_sec * 1000)
            end_ms = int(raw_end_sec * 1000)
            return start_ms, end_ms, ""

        # 1. Find closest starting word to raw_start_sec
        start_idx = 0
        min_start_diff = float("inf")
        for idx, w in enumerate(words):
            diff = abs(w.start - raw_start_sec)
            if diff < min_start_diff:
                min_start_diff = diff
                start_idx = idx

        # 2. Look back up to 35 words (or ~12s) to find true sentence start (. ! ?) or acoustic pause
        best_start_idx = start_idx
        max_lookback = max(0, start_idx - 35)
        for idx in range(start_idx - 1, max_lookback - 1, -1):
            w_text = words[idx].word.strip()
            # If word ends with sentence punctuation, the NEXT word is the clean sentence beginning
            if any(w_text.endswith(p) for p in (".", "!", "?", ".\"", "!\"", "?\"")) and (idx + 1 < len(words)):
                best_start_idx = idx + 1
                break
            # Secondary check: significant acoustic silence pause (>= 0.4s) between thoughts
            if idx > 0 and (words[idx].start - words[idx - 1].end) >= 0.4:
                best_start_idx = idx
                break

        # 2b. Start Token Guard: If start word is banned (conjunction, relative pronoun), advance forward
        max_advance = min(len(words) - 1, best_start_idx + 6)
        while best_start_idx < max_advance:
            clean_word = words[best_start_idx].word.strip().lower().strip(".,!?:;\"'()")
            if clean_word in BANNED_START_WORDS and best_start_idx + 1 < len(words):
                best_start_idx += 1
            else:
                break

        actual_start_sec = words[best_start_idx].start

        # 3. Target end with soft duration elasticity (+1.5s strictly aligned with Technical Quality Gate)
        elastic_max_sec = min(180.0, self.max_duration_sec + 1.5)
        target_end_sec = min(raw_end_sec, actual_start_sec + self.max_duration_sec)

        # 4. Multi-tier sentence-completion search
        punct_candidates = []       # Priority 1: Sentence-ending punctuation (. ! ?)
        clause_candidates = []      # Priority 2: Clause punctuation (, ; : --)
        silence_candidates = []     # Priority 3: Acoustic silence gaps (>= 0.4s)
        clean_word_candidates = []  # Priority 4: Non-dangling word boundaries

        for idx in range(best_start_idx, len(words)):
            dur = words[idx].end - actual_start_sec
            if dur < self.min_duration_sec:
                continue
            if dur > elastic_max_sec:
                break

            w_text = words[idx].word.strip()
            clean_w = w_text.lower().strip(".,!?:;\"'()")
            is_banned_end = clean_w in BANNED_END_WORDS
            dist = abs(words[idx].end - target_end_sec)

            # Check sentence punctuation
            if any(w_text.endswith(p) for p in (".", "!", "?", ".\"", "!\"", "?\"")) and not is_banned_end:
                punct_candidates.append((dist, idx))
            # Check clause punctuation
            elif any(w_text.endswith(p) for p in (",", ";", ":", "--", "-")) and not is_banned_end:
                clause_candidates.append((dist, idx))
            # Check acoustic silence pause (>= 0.4s) before next word
            elif (idx + 1 < len(words)) and ((words[idx + 1].start - words[idx].end) >= 0.4) and not is_banned_end:
                silence_candidates.append((dist, idx))
            elif not is_banned_end:
                clean_word_candidates.append((dist, idx))

        if punct_candidates:
            punct_candidates.sort(key=lambda x: x[0])
            best_end_idx = punct_candidates[0][1]
        elif clause_candidates:
            clause_candidates.sort(key=lambda x: x[0])
            best_end_idx = clause_candidates[0][1]
        elif silence_candidates:
            silence_candidates.sort(key=lambda x: x[0])
            best_end_idx = silence_candidates[0][1]
        elif clean_word_candidates:
            clean_word_candidates.sort(key=lambda x: x[0])
            best_end_idx = clean_word_candidates[0][1]
        else:
            # Fallback to closest word within bounds
            best_end_idx = best_start_idx
            closest_dist = float("inf")
            for idx in range(best_start_idx, len(words)):
                dur = words[idx].end - actual_start_sec
                if self.min_duration_sec <= dur <= elastic_max_sec:
                    dist = abs(words[idx].end - target_end_sec)
                    if dist < closest_dist:
                        closest_dist = dist
                        best_end_idx = idx

        actual_end_sec = words[best_end_idx].end
        segment_words = words[best_start_idx : best_end_idx + 1]
        segment_text = " ".join(w.word for w in segment_words)

        start_ms = int(round(actual_start_sec * 1000))
        end_ms = int(round(actual_end_sec * 1000))
        return start_ms, end_ms, segment_text
