"""
Hard validation, normalization, and overlap-trimming for LLM-proposed clip candidates.

Guarantees:
1. Strict JSON / shape parsing with no eval shortcuts.
2. Numeric timestamps: coerces valid numeric strings (e.g. '15.5', '15.5s'), rejects non-numeric/NaN/Inf.
3. Hard bounds: 0.0 <= start_sec < end_sec <= video_duration_sec (from ffprobe).
4. Minimum clip duration: (end_sec - start_sec) >= min_duration_sec (default 15.0s).
5. Overlap resolution: overlapping proposals are trimmed to non-overlapping boundaries,
   or dropped if the remaining duration is < min_duration_sec.
"""

import json
import math
import logging
import re
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field

from shorts_engine.services.clip_selection.prompts import (
    CandidateSegmentSchema,
    CandidateSegmentsResponse,
)

logger = logging.getLogger(__name__)


class CandidateValidationResult(BaseModel):
    valid_candidates: List[CandidateSegmentSchema] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    trimmed_count: int = 0
    dropped_count: int = 0

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0 and len(self.valid_candidates) > 0


class ClipCandidateValidator:
    def __init__(
        self,
        min_duration_sec: float = 15.0,
        max_duration_sec: float = 60.0,
        allow_trimming: bool = True,
    ):
        self.min_duration_sec = max(1.0, float(min_duration_sec))
        self.max_duration_sec = max(self.min_duration_sec, float(max_duration_sec))
        self.allow_trimming = allow_trimming

    @staticmethod
    def parse_numeric(val: Any, field_name: str, candidate_idx: int) -> Tuple[Optional[float], Optional[str]]:
        """
        Parses a value into a finite float. Supports float, int, and numeric strings like '15.5' or '15.5s'.
        Returns (parsed_float, error_message).
        """
        if val is None:
            return None, f"Candidate #{candidate_idx}: '{field_name}' is missing or null."

        if isinstance(val, (int, float)):
            if math.isnan(val) or math.isinf(val):
                return None, f"Candidate #{candidate_idx}: '{field_name}' must be finite, got {val}."
            return float(val), None

        if isinstance(val, str):
            clean = val.strip().lower()
            # Strip trailing 's', 'sec', 'seconds'
            clean = re.sub(r"\s*(seconds?|secs?|s)$", "", clean).strip()
            try:
                num = float(clean)
                if math.isnan(num) or math.isinf(num):
                    return None, f"Candidate #{candidate_idx}: '{field_name}' must be finite, got '{val}'."
                return num, None
            except (ValueError, TypeError):
                return None, f"Candidate #{candidate_idx}: '{field_name}' ('{val}') is non-numeric."

        return None, f"Candidate #{candidate_idx}: '{field_name}' must be numeric, got {type(val).__name__}."

    def validate(
        self,
        raw_candidates: Any,
        video_duration_sec: Optional[float] = None,
    ) -> CandidateValidationResult:
        """
        Validates raw candidate data from the LLM or caller.
        raw_candidates can be:
          - A JSON string
          - A dict with a 'candidates' or 'clips' key
          - A list of candidate dicts or CandidateSegmentSchema objects
          - A CandidateSegmentsResponse instance
        """
        errors: List[str] = []

        # 1. Strict parsing: Handle string input with strict JSON (no eval)
        data = raw_candidates
        if isinstance(data, str):
            clean_str = data.strip()
            if clean_str.startswith("```"):
                lines = clean_str.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                clean_str = "\n".join(lines).strip()
            try:
                data = json.loads(clean_str)
            except Exception as e:
                return CandidateValidationResult(
                    valid_candidates=[],
                    errors=[f"Strict JSON parse error: {e}"],
                )

        # 2. Extract candidate list
        candidate_list: List[Any] = []
        if isinstance(data, CandidateSegmentsResponse):
            candidate_list = list(data.candidates)
        elif isinstance(data, dict):
            if "candidates" in data and isinstance(data["candidates"], list):
                candidate_list = data["candidates"]
            elif "clips" in data and isinstance(data["clips"], list):
                candidate_list = data["clips"]
            else:
                return CandidateValidationResult(
                    valid_candidates=[],
                    errors=["Wrong JSON shape: missing 'candidates' list in payload."],
                )
        elif isinstance(data, list):
            candidate_list = data
        else:
            return CandidateValidationResult(
                valid_candidates=[],
                errors=[f"Wrong JSON shape: expected object or list, got {type(data).__name__}."],
            )

        if not candidate_list:
            return CandidateValidationResult(
                valid_candidates=[],
                errors=["Payload contains an empty candidate list."],
            )

        # 3. Individual candidate validation
        valid_items: List[CandidateSegmentSchema] = []
        for idx, item in enumerate(candidate_list):
            cand_dict: Dict[str, Any] = {}
            if isinstance(item, CandidateSegmentSchema):
                cand_dict = item.model_dump() if hasattr(item, "model_dump") else item.dict()
            elif isinstance(item, dict):
                cand_dict = dict(item)
            else:
                errors.append(f"Candidate #{idx}: expected dictionary or schema, got {type(item).__name__}.")
                continue

            # Check start_sec and end_sec presence
            if "start_sec" not in cand_dict:
                errors.append(f"Candidate #{idx}: missing required field 'start_sec'.")
                continue
            if "end_sec" not in cand_dict:
                errors.append(f"Candidate #{idx}: missing required field 'end_sec'.")
                continue

            st, err_st = self.parse_numeric(cand_dict.get("start_sec"), "start_sec", idx)
            en, err_en = self.parse_numeric(cand_dict.get("end_sec"), "end_sec", idx)

            if err_st:
                errors.append(err_st)
            if err_en:
                errors.append(err_en)
            if err_st or err_en or st is None or en is None:
                continue

            # Hard bounds validation
            if st < 0.0:
                errors.append(f"Candidate #{idx}: start_sec ({st:.2f}) cannot be negative.")
                continue

            if st >= en:
                errors.append(f"Candidate #{idx}: inverted range start_sec ({st:.2f}) >= end_sec ({en:.2f}).")
                continue

            if video_duration_sec is not None and video_duration_sec > 0.0:
                if st >= video_duration_sec:
                    errors.append(
                        f"Candidate #{idx}: start_sec ({st:.2f}) exceeds video duration ({video_duration_sec:.2f}s)."
                    )
                    continue
                if en > video_duration_sec:
                    errors.append(
                        f"Candidate #{idx}: end_sec ({en:.2f}) exceeds video duration ({video_duration_sec:.2f}s)."
                    )
                    continue

            dur = en - st
            if dur < self.min_duration_sec:
                errors.append(
                    f"Candidate #{idx}: clip length ({dur:.2f}s) is shorter than minimum allowed ({self.min_duration_sec:.2f}s)."
                )
                continue

            # Fill safe defaults for Pydantic instantiation
            cand_dict["start_sec"] = round(st, 3)
            cand_dict["end_sec"] = round(en, 3)
            cand_dict.setdefault("hook_text", "")
            cand_dict.setdefault("payoff_text", "")

            # Normalize score to 0.0 - 1.0 range
            raw_score = cand_dict.get("score", 0.8)
            try:
                sc = float(raw_score)
                if sc > 1.0 and sc <= 10.0:
                    sc = sc / 10.0
                cand_dict["score"] = max(0.0, min(1.0, sc))
            except (ValueError, TypeError):
                cand_dict["score"] = 0.8

            cand_dict.setdefault("reason", "Selected by LLM")
            cand_dict.setdefault("suggested_title", "Viral Short #Shorts")
            cand_dict.setdefault("suggested_description", "#Shorts")
            if not isinstance(cand_dict.get("suggested_tags"), list):
                cand_dict["suggested_tags"] = ["#shorts"]

            try:
                schema_obj = CandidateSegmentSchema(**cand_dict)
                valid_items.append(schema_obj)
            except Exception as e:
                errors.append(f"Candidate #{idx}: schema validation error: {e}")

        # 4. Overlap resolution (Trimming or Dropping)
        # Prioritize by score descending
        valid_items.sort(key=lambda c: getattr(c, "score", 0.0), reverse=True)

        accepted: List[CandidateSegmentSchema] = []
        trimmed_count = 0
        dropped_count = 0

        for cand in valid_items:
            s = cand.start_sec
            e = cand.end_sec
            orig_s, orig_e = s, e

            # Check overlaps with accepted clips
            accepted_intervals = [(a.start_sec, a.end_sec) for a in accepted]
            resolved_range, status = self._resolve_overlap(s, e, accepted_intervals)

            if resolved_range is None:
                dropped_count += 1
                errors.append(
                    f"Candidate '{cand.suggested_title}' [{orig_s:.1f}s-{orig_e:.1f}s] dropped due to overlap ({status})."
                )
                logger.info(
                    "Dropped candidate [%.1fs-%.1fs] due to overlap (%s).",
                    orig_s, orig_e, status
                )
                continue

            new_s, new_e = resolved_range
            if new_s != orig_s or new_e != orig_e:
                trimmed_count += 1
                cand.start_sec = round(new_s, 3)
                cand.end_sec = round(new_e, 3)
                logger.info(
                    "Trimmed candidate from [%.1fs-%.1fs] to [%.1fs-%.1fs] to resolve overlap.",
                    orig_s, orig_e, new_s, new_e
                )

            accepted.append(cand)

        return CandidateValidationResult(
            valid_candidates=accepted,
            errors=errors,
            trimmed_count=trimmed_count,
            dropped_count=dropped_count,
        )

    def _resolve_overlap(
        self,
        cand_start: float,
        cand_end: float,
        accepted: List[Tuple[float, float]],
    ) -> Tuple[Optional[Tuple[float, float]], str]:
        """
        Checks interval [cand_start, cand_end] against already accepted intervals.
        Trims interval boundaries if allow_trimming=True, or drops if cannot resolve.
        Returns ((new_start, new_end), status_string) or (None, reason).
        """
        if not accepted:
            return (cand_start, cand_end), "no_overlap"

        s = cand_start
        e = cand_end

        if not self.allow_trimming:
            for a_s, a_e in accepted:
                if max(s, a_s) < min(e, a_e):
                    return None, "overlap_detected"
            return (s, e), "no_overlap"

        # Sort accepted intervals by start time
        for a_s, a_e in sorted(accepted, key=lambda x: x[0]):
            # Check overlap
            if max(s, a_s) < min(e, a_e):
                if a_s <= s < a_e < e:
                    # Candidate starts inside accepted, ends after accepted -> trim start
                    s = a_e
                elif s < a_s < e <= a_e:
                    # Candidate starts before accepted, ends inside accepted -> trim end
                    e = a_s
                elif a_s <= s < e <= a_e:
                    # Candidate is completely enclosed in accepted -> drop
                    return None, "completely_enclosed"
                elif s < a_s < a_e < e:
                    # Candidate strictly encloses accepted: choose largest non-overlapping fragment
                    left_len = a_s - s
                    right_len = e - a_e
                    if left_len >= right_len and left_len >= self.min_duration_sec:
                        e = a_s
                    elif right_len >= self.min_duration_sec:
                        s = a_e
                    else:
                        return None, "split_fragments_too_short"

        # Final verification: check that trimmed interval does not overlap any accepted clip
        for a_s, a_e in accepted:
            if max(s, a_s) < min(e, a_e):
                return None, "lingering_overlap"

        dur = e - s
        if dur < self.min_duration_sec:
            return None, f"trimmed_duration_{dur:.1f}s_below_minimum_{self.min_duration_sec:.1f}s"

        return (s, e), "trimmed" if (s != cand_start or e != cand_end) else "kept"
