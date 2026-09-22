"""
Unit and integration tests for Quality Gate Enhancements:
- Duration bounds parameterization & container tolerance (2.0s).
- Primary subject framing evaluation (ignoring secondary peripheral faces).
- Advisory routing to WAITING_APPROVAL when human_review_required=True.
- BoundarySnapper speech guards (banned start/end words, silence gaps, duration elasticity).
"""

import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from shorts_engine.db.models import ClipStatus
from shorts_engine.services.quality_gate.technical_check import TechnicalVerifier
from shorts_engine.services.quality_gate.render_inspector import RenderInspector
from shorts_engine.services.quality_gate.semantic_check import SemanticVerifier
from shorts_engine.services.quality_gate.review_queue import QualityGateManager
from shorts_engine.services.clip_selection.boundaries import BoundarySnapper
from shorts_engine.services.transcription.formatter import FormattedTranscript, WordSegment
from shorts_engine.services.reframing.detector import DetectedFace
from shorts_engine.services.llm.router import LLMRouter


@pytest.fixture(scope="module")
def synthetic_video_65s(tmp_path_factory):
    """Generates a 65-second synthetic 1080x1920 video with audio."""
    tmp_dir = tmp_path_factory.mktemp("vid_65s")
    vid_path = tmp_dir / "synthetic_65s.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
        "-t", "65.0",
        "-c:v", "libx264", "-c:a", "aac",
        str(vid_path)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return vid_path


def test_technical_verifier_respects_custom_duration_bounds_and_tolerance(synthetic_video_65s):
    verifier = TechnicalVerifier()

    # Default ceiling (55-58s) should reject a 65s clip
    passed_default, details_default = verifier.verify(synthetic_video_65s)
    assert passed_default is False
    assert details_default["checks"]["duration_within_bounds"] is False

    # Parameterized ceiling with override (e.g., max 120s) should pass
    passed_override, details_override = verifier.verify(
        synthetic_video_65s,
        min_duration_sec=15.0,
        max_duration_sec=120.0,
    )
    assert passed_override is True
    assert details_override["checks"]["duration_within_bounds"] is True

    # Tolerance test: 65s video with max_duration_sec=64s (within 2.0s container tolerance: 64 + 2 = 66)
    passed_tol, details_tol = verifier.verify(
        synthetic_video_65s,
        min_duration_sec=15.0,
        max_duration_sec=64.0,
    )
    assert passed_tol is True
    assert details_tol["checks"]["duration_within_bounds"] is True


def test_render_inspector_evaluates_primary_subject_only():
    inspector = RenderInspector()

    # Frame with 2 faces:
    # Face A: secondary peripheral face (small area 50x50 = 2500), at border x=50
    # Face B: primary subject (large area 200x200 = 40000), centered at x=440 (center = 540 in 1080w frame)
    faces = [
        DetectedFace(x=50, y=400, w=50, h=50, score=0.8),
        DetectedFace(x=440, y=300, w=200, h=200, score=0.95),
    ]

    # Inspecting should select Face B (largest area) and pass framing without violation
    with patch("cv2.VideoCapture") as mock_cap:
        mock_instance = MagicMock()
        mock_instance.isOpened.return_value = True
        mock_instance.get.side_effect = lambda prop: 300 if prop == 7 else 30.0  # 300 frames, 30 fps
        # Mock frame: black frame (no caption collision)
        import numpy as np
        blank_frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
        mock_instance.read.return_value = (True, blank_frame)
        mock_cap.return_value = mock_instance

        with patch.object(inspector.detector, "detect_faces", return_value=faces):
            passed, details = inspector.inspect_render(Path("dummy_path.mp4"))
            assert passed is True
            assert details["subject_framing_violations"] == 0


def test_quality_gate_manager_routes_advisories_to_waiting_approval(synthetic_video_65s):
    manager = QualityGateManager()

    good_text = (
        "Here is the secret to building high performing software systems. "
        "You must constantly verify assumptions and test against production edge cases. "
        "That is how reliable software is created."
    )

    # Mock semantic verifier to return pass with advisory
    with patch.object(
        manager.semantic_verifier,
        "verify_segment",
        return_value=(True, {"pass_decision": True, "semantic_advisory": "Slight mid-sentence rhythm note"}),
    ):
        status, report = manager.evaluate_clip(
            rendered_video_path=synthetic_video_65s,
            segment_text=good_text,
            hook_text="Secret to software",
            payoff_text="Reliable software",
            human_review_required=True,
            min_duration_sec=15.0,
            max_duration_sec=90.0,
        )
        # Should be WAITING_APPROVAL, NOT REJECTED!
        assert status == ClipStatus.WAITING_APPROVAL
        assert "advisories" in report
        assert any("Slight mid-sentence rhythm note" in adv for adv in report["advisories"])


def test_boundary_snapper_speech_guards():
    # Build word segments:
    # 0.0 - 0.8: "Because" (banned start word)
    # 0.9 - 1.2: "the"
    # 1.3 - 2.0: "market"
    # 2.1 - 2.5: "is" (banned end word)
    # 2.6 - 3.5: "growing."
    # 4.0 - 4.8: "rapidly." (0.5s silence gap before)
    # 4.9 - 5.2: "and" (banned end word candidate)
    words = [
        WordSegment(word="Because", start=0.0, end=0.8),
        WordSegment(word="the", start=0.9, end=1.2),
        WordSegment(word="market", start=1.3, end=2.0),
        WordSegment(word="is", start=2.1, end=2.5),
        WordSegment(word="growing.", start=2.6, end=3.5),
        WordSegment(word="rapidly.", start=4.0, end=4.8),
        WordSegment(word="and", start=4.9, end=5.2),
    ]

    transcript = FormattedTranscript(
        words=words,
        full_text="Because the market is growing. rapidly. and",
        language="en",
    )

    snapper = BoundarySnapper(min_duration_sec=2.0, max_duration_sec=10.0)

    # Calling snap_boundaries starting around 0.0:
    # The snapper should advance past "Because" to "the" or avoid starting on "Because"
    start_ms, end_ms, text = snapper.snap_boundaries(0.0, 5.2, transcript)

    # Start should advance past "Because" (0.0 - 0.8s) -> start_ms >= 900
    assert start_ms >= 900
    # End should NOT land on "and" (banned end word) -> end_ms <= 4800 (lands on "rapidly.")
    assert end_ms <= 4900
    assert "and" not in text.split()[-1]
