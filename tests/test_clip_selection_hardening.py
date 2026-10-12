"""
Tests for AI Clip Selection Hardening & Resilience.

Validates:
1. Malformed output test table:
   - Non-numeric values
   - String numbers
   - Inverted ranges
   - Out-of-duration ranges (duration from ffprobe / database)
   - Overlapping ranges (trimmed or dropped)
   - Wrong JSON shape
   - Clip length < minimum configurable threshold
2. One repair retry feeding validation errors back to the model.
3. Resilience on lingering repair failure: skips candidates, logs failure, never crashes.
4. Stage-level acceptance test: analyze_candidates_stage completes cleanly despite 100% invalid model output.
"""

import math
import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.services.transcription.formatter import WordSegment, FormattedTranscript
from shorts_engine.services.clip_selection.prompts import (
    CandidateSegmentSchema,
    CandidateSegmentsResponse,
)
from shorts_engine.services.clip_selection.validator import (
    ClipCandidateValidator,
    CandidateValidationResult,
)
from shorts_engine.services.clip_selection.selector import (
    ClipSelectionService,
    SelectedClip,
)
from shorts_engine.orchestration.stage_registry import (
    StageContext,
    analyze_candidates_stage,
)
from shorts_engine.db.models import (
    Base,
    SourceVideo,
    SourceVideoStatus,
    ClipCandidate,
    Clip,
    Transcript,
)


# =====================================================================
# 1. UNIT-TEST TABLE OF MALFORMED OUTPUTS
# =====================================================================

MALFORMED_OUTPUT_CASES = [
    # Non-numeric values
    (
        "non_numeric_string",
        {"candidates": [{"start_sec": "not_a_number", "end_sec": 30.0}]},
        100.0,
        15.0,
        {"valid_count": 0, "error_contains": "non-numeric"},
    ),
    (
        "null_value",
        {"candidates": [{"start_sec": None, "end_sec": 30.0}]},
        100.0,
        15.0,
        {"valid_count": 0, "error_contains": "missing or null"},
    ),
    (
        "nan_value",
        {"candidates": [{"start_sec": float("nan"), "end_sec": 30.0}]},
        100.0,
        15.0,
        {"valid_count": 0, "error_contains": "must be finite"},
    ),
    (
        "inf_value",
        {"candidates": [{"start_sec": 0.0, "end_sec": float("inf")}]},
        100.0,
        15.0,
        {"valid_count": 0, "error_contains": "must be finite"},
    ),
    (
        "dict_value",
        {"candidates": [{"start_sec": {"sec": 10}, "end_sec": 30.0}]},
        100.0,
        15.0,
        {"valid_count": 0, "error_contains": "must be numeric"},
    ),
    # String numbers (validly coerced)
    (
        "string_numbers_valid",
        {"candidates": [{"start_sec": "15.0", "end_sec": "35.5"}]},
        100.0,
        15.0,
        {"valid_count": 1, "expected_start": 15.0, "expected_end": 35.5},
    ),
    (
        "string_numbers_with_units",
        {"candidates": [{"start_sec": " 20.0s ", "end_sec": " 45.0 seconds "}]},
        100.0,
        15.0,
        {"valid_count": 1, "expected_start": 20.0, "expected_end": 45.0},
    ),
    # Inverted ranges
    (
        "inverted_range",
        {"candidates": [{"start_sec": 45.0, "end_sec": 20.0}]},
        100.0,
        15.0,
        {"valid_count": 0, "error_contains": "inverted range"},
    ),
    (
        "zero_length_range",
        {"candidates": [{"start_sec": 25.0, "end_sec": 25.0}]},
        100.0,
        15.0,
        {"valid_count": 0, "error_contains": "inverted range"},
    ),
    # Out-of-duration ranges (duration from ffprobe)
    (
        "negative_start",
        {"candidates": [{"start_sec": -5.0, "end_sec": 25.0}]},
        100.0,
        15.0,
        {"valid_count": 0, "error_contains": "cannot be negative"},
    ),
    (
        "end_exceeds_video_duration",
        {"candidates": [{"start_sec": 80.0, "end_sec": 130.0}]},
        100.0,
        15.0,
        {"valid_count": 0, "error_contains": "exceeds video duration"},
    ),
    (
        "start_exceeds_video_duration",
        {"candidates": [{"start_sec": 110.0, "end_sec": 150.0}]},
        100.0,
        15.0,
        {"valid_count": 0, "error_contains": "exceeds video duration"},
    ),
    # Clip length < minimum allowed
    (
        "clip_below_minimum_length",
        {"candidates": [{"start_sec": 10.0, "end_sec": 20.0}]},
        100.0,
        15.0,
        {"valid_count": 0, "error_contains": "shorter than minimum"},
    ),
    # Wrong JSON shape
    (
        "invalid_json_string",
        "NOT_JSON_AT_ALL {{{",
        100.0,
        15.0,
        {"valid_count": 0, "error_contains": "Strict JSON parse error"},
    ),
    (
        "missing_candidates_key",
        {"random_key": "some_value"},
        100.0,
        15.0,
        {"valid_count": 0, "error_contains": "missing 'candidates' list"},
    ),
    (
        "candidates_not_a_list",
        {"candidates": "just a string"},
        100.0,
        15.0,
        {"valid_count": 0, "error_contains": "missing 'candidates' list"},
    ),
    (
        "primitive_payload",
        12345,
        100.0,
        15.0,
        {"valid_count": 0, "error_contains": "Wrong JSON shape"},
    ),
]


@pytest.mark.parametrize("case_name, payload, video_duration, min_dur, expected", MALFORMED_OUTPUT_CASES)
def test_malformed_output_table(case_name, payload, video_duration, min_dur, expected):
    """
    Parametrized unit test table for every malformed output condition.
    Verifies that invalid payloads are strictly validated without raising unhandled exceptions.
    """
    validator = ClipCandidateValidator(min_duration_sec=min_dur, allow_trimming=True)
    res = validator.validate(payload, video_duration_sec=video_duration)

    assert len(res.valid_candidates) == expected["valid_count"]
    if "error_contains" in expected:
        assert len(res.errors) > 0
        matching = any(expected["error_contains"].lower() in err.lower() for err in res.errors)
        assert matching, f"Expected '{expected['error_contains']}' in errors: {res.errors}"

    if "expected_start" in expected:
        assert res.valid_candidates[0].start_sec == expected["expected_start"]
    if "expected_end" in expected:
        assert res.valid_candidates[0].end_sec == expected["expected_end"]


def test_overlapping_ranges_trimming_and_dropping():
    """
    Verifies overlap resolution:
    - Candidate B overlapping Candidate A's tail is trimmed to non-overlapping start.
    - Candidate C overlapping with insufficient remaining length is dropped.
    - Candidate D completely enclosed in Candidate A is dropped.
    """
    validator = ClipCandidateValidator(min_duration_sec=15.0, allow_trimming=True)
    payload = {
        "candidates": [
            # Candidate 1: 10s to 40s (score 0.95) -> accepted
            {"start_sec": 10.0, "end_sec": 40.0, "score": 0.95, "suggested_title": "Clip A"},
            # Candidate 2: 30s to 60s (score 0.85) -> overlaps A [30s-40s], trimmed to [40s-60s] (20s >= 15s)
            {"start_sec": 30.0, "end_sec": 60.0, "score": 0.85, "suggested_title": "Clip B"},
            # Candidate 3: 50s to 65s (score 0.75) -> overlaps trimmed B [50s-60s], remainder [60s-65s] (5s < 15s) -> dropped
            {"start_sec": 50.0, "end_sec": 65.0, "score": 0.75, "suggested_title": "Clip C"},
            # Candidate 4: 15s to 35s (score 0.65) -> completely inside A [10s-40s] -> dropped
            {"start_sec": 15.0, "end_sec": 35.0, "score": 0.65, "suggested_title": "Clip D"},
        ]
    }

    res = validator.validate(payload, video_duration_sec=100.0)

    # Candidate A and trimmed Candidate B must be accepted
    assert len(res.valid_candidates) == 2
    assert res.trimmed_count == 1
    assert res.dropped_count == 2

    c_a = res.valid_candidates[0]
    c_b = res.valid_candidates[1]

    assert c_a.start_sec == 10.0
    assert c_a.end_sec == 40.0

    assert c_b.start_sec == 40.0
    assert c_b.end_sec == 60.0


# =====================================================================
# 2. REPAIR RETRY TESTS (SERVICE LEVEL)
# =====================================================================

def test_selector_one_repair_retry_succeeds():
    """
    Verifies that when initial candidate proposal has validation errors,
    the selector triggers exactly 1 repair retry passing the validation errors,
    and accepts the repaired response.
    """
    words = [
        WordSegment(word=f"word{i}.", start=float(i), end=float(i) + 0.8)
        for i in range(120)
    ]
    transcript = FormattedTranscript(words=words)

    service = ClipSelectionService()

    # Pass 1 response: malformed inverted range
    invalid_candidate = CandidateSegmentSchema(
        start_sec=50.0,
        end_sec=20.0,  # inverted!
        hook_text="Invalid Hook",
        payoff_text="Invalid Payoff",
        score=0.9,
        reason="Test candidate",
        suggested_title="Invalid Clip",
        suggested_description="Description",
        suggested_tags=["#shorts"],
    )
    invalid_resp = CandidateSegmentsResponse(candidates=[invalid_candidate])

    # Pass 2 (repair retry) response: corrected valid range
    valid_candidate = CandidateSegmentSchema(
        start_sec=20.0,
        end_sec=45.0,
        hook_text="Valid Hook",
        payoff_text="Valid Payoff",
        score=0.9,
        reason="Repaired candidate",
        suggested_title="Valid Clip",
        suggested_description="Description",
        suggested_tags=["#shorts"],
    )
    repaired_resp = CandidateSegmentsResponse(candidates=[valid_candidate])

    calls = []

    def mock_generate_structured(prompt, schema, **kwargs):
        calls.append((prompt, kwargs.get("stage")))
        if len(calls) == 1:
            return invalid_resp, {"provider": "gemini", "model": "gemini-3.8-flash"}
        else:
            return repaired_resp, {"provider": "gemini", "model": "gemini-3.8-flash"}

    with patch.object(service.router, "generate_structured", side_effect=mock_generate_structured):
        clips = service.select_clips(
            transcript,
            video_duration_sec=120.0,
            target_duration_min=15,
            target_duration_max=45,
        )

    # Exactly 2 LLM calls: initial + 1 repair retry
    assert len(calls) == 2
    initial_call, repair_call = calls[0], calls[1]
    assert repair_call[1] == "clip_selection_repair"
    # Repair prompt should contain the specific validation error
    assert "inverted range" in repair_call[0]

    assert len(clips) == 1
    assert 15.0 <= clips[0].duration_sec <= 45.0
    assert clips[0].suggested_title == "Valid Clip"


def test_selector_repair_failure_skips_and_never_crashes():
    """
    Verifies that if both initial call and repair retry return invalid candidates,
    the selector skips the invalid candidates, logs the failure, and returns []
    without raising an unhandled exception or crashing.
    """
    words = [
        WordSegment(word=f"word{i}.", start=float(i), end=float(i) + 0.8)
        for i in range(120)
    ]
    transcript = FormattedTranscript(words=words)

    service = ClipSelectionService()

    invalid_cand_1 = CandidateSegmentSchema(
        start_sec=150.0,
        end_sec=200.0,  # exceeds video duration 100s
        hook_text="Hook 1",
        payoff_text="Payoff 1",
        score=0.8,
        reason="Bad duration",
        suggested_title="Bad Duration 1",
        suggested_description="Desc",
        suggested_tags=["#shorts"],
    )
    invalid_cand_2 = CandidateSegmentSchema(
        start_sec=-10.0,
        end_sec=25.0,  # negative start
        hook_text="Hook 2",
        payoff_text="Payoff 2",
        score=0.8,
        reason="Negative start",
        suggested_title="Bad Duration 2",
        suggested_description="Desc",
        suggested_tags=["#shorts"],
    )

    resp_1 = CandidateSegmentsResponse(candidates=[invalid_cand_1])
    resp_2 = CandidateSegmentsResponse(candidates=[invalid_cand_2])

    calls = []

    def mock_generate_structured(prompt, schema, **kwargs):
        calls.append(prompt)
        return (resp_1, {"provider": "gemini"}) if len(calls) == 1 else (resp_2, {"provider": "gemini"})

    with patch.object(service.router, "generate_structured", side_effect=mock_generate_structured):
        clips = service.select_clips(
            transcript,
            video_duration_sec=100.0,
            target_duration_min=15,
            target_duration_max=45,
        )

    # Exactly 2 calls: initial + 1 repair retry
    assert len(calls) == 2
    # 100% invalid output after repair retry -> 0 clips selected
    assert clips == []


# =====================================================================
# 3. STAGE-LEVEL ACCEPTANCE TEST
# =====================================================================

def test_analyze_candidates_stage_completes_despite_100_percent_invalid_output():
    """
    ACCEPTANCE CRITERIA:
    A stage-level test shows selection completing despite 100% invalid model output:
    - Zero clips selected.
    - Stage succeeds cleanly with {"candidates": []}.
    - A warning log entry is emitted.
    - Zero fan-out jobs enqueued.
    - Video status is preserved/updated without crashing.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    video = SourceVideo(
        id="video_acceptance_1",
        channel_id="channel_1",
        youtube_video_id="yt_video_100",
        title="Test Long Video",
        duration_sec=120,
        status=SourceVideoStatus.ANALYZING,
    )
    db.add(video)
    db.commit()

    words = [
        {"word": f"word{i}.", "start": float(i), "end": float(i) + 0.8}
        for i in range(120)
    ]
    t_row = Transcript(
        id="trans_1",
        source_video_id=video.id,
        raw_transcript='{"words": ' + str(words).replace("'", '"') + '}',
        language="en",
    )
    db.add(t_row)
    db.commit()

    ctx = StageContext(
        db_session=db,
        video_id=video.youtube_video_id,
        job_id="job_analyze_1",
        artifacts={"transcribe": {"transcript": {"words": words}}},
    )

    # Mock selector.select_clips to return [] (which happens when model returns 100% invalid output)
    with patch("shorts_engine.services.clip_selection.selector.ClipSelectionService.select_clips", return_value=[]) as mock_select:
        with patch("shorts_engine.services.queue.durable_queue.durable_job_queue.enqueue") as mock_enqueue:
            with patch("shorts_engine.orchestration.stage_registry.logger.warning") as mock_log_warn:
                result = analyze_candidates_stage(ctx)

    # 1. Stage succeeds cleanly and returns empty candidate list
    assert result == {"candidates": []}

    # 2. Select clips was called with authoritative duration from video
    assert mock_select.call_count == 1
    assert mock_select.call_args[1].get("video_duration_sec") == 120.0

    # 3. Zero downstream jobs enqueued
    assert mock_enqueue.call_count == 0

    # 4. Clean warning logged about 0 candidates discovered
    assert mock_log_warn.call_count >= 1
    log_msg = mock_log_warn.call_args[0][0]
    assert "0 candidates" in log_msg

    # 5. Video status transitioned cleanly to SEGMENTS_SELECTED without failing
    db.refresh(video)
    assert video.status == SourceVideoStatus.SEGMENTS_SELECTED
