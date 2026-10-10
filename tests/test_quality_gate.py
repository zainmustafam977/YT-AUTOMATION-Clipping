"""
Unit and integration tests for two-tier Quality Gate and Multimodal Render Inspector.
"""

import subprocess
from shorts_engine.db.models import ClipStatus
from shorts_engine.services.quality_gate.technical_check import TechnicalVerifier
from shorts_engine.services.quality_gate.semantic_check import SemanticVerifier
from shorts_engine.services.quality_gate.review_queue import QualityGateManager


from unittest.mock import patch
from shorts_engine.services.llm.router import LLMRouter


def test_technical_verifier_rejects_zero_byte_file(tmp_path):
    empty_file = tmp_path / "empty.mp4"
    empty_file.write_bytes(b"")
    
    verifier = TechnicalVerifier()
    passed, details = verifier.verify(empty_file)
    assert passed is False
    assert "error" in details


def test_semantic_verifier_detects_dangling_references():
    with patch.object(LLMRouter, "generate_structured", return_value=(None, {})):
        verifier = SemanticVerifier()  # Heuristic fallback
        bad_text = "As I said before in the previous video, this strategy does not work."
        passed, details = verifier.verify_segment(bad_text, "Hook", "Payoff")
        assert passed is False
        assert details["has_unresolved_pronouns"] is True

        good_text = (
            "Here is the single most important rule for saving money. "
            "Always automate your investments the moment your paycheck arrives. "
            "By paying yourself first, you build wealth automatically without feeling the pinch."
        )
        passed_good, details_good = verifier.verify_segment(good_text, "Hook", "Payoff")
        assert passed_good is True
        assert details_good["has_unresolved_pronouns"] is False


def test_quality_gate_manager_transitions_with_human_toggle(tmp_path):
    # 1. Create a 16-second synthetic 1080x1920 video with audio using FFmpeg
    video_path = tmp_path / "valid_916_short.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
        "-t", "16.0",
        "-c:v", "libx264", "-c:a", "aac",
        str(video_path)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    good_text = (
        "Here is the single most important rule for saving money. "
        "Always automate your investments the moment your paycheck arrives. "
        "By paying yourself first, you build wealth automatically without feeling the pinch."
    )

    manager = QualityGateManager()

    with patch.object(LLMRouter, "generate_structured", return_value=(None, {})):
        # Case A: human review enabled -> WAITING_APPROVAL
        status_a, report_a = manager.evaluate_clip(
            rendered_video_path=video_path,
            segment_text=good_text,
            hook_text="Hook",
            payoff_text="Payoff",
            human_review_required=True,
        )
        assert status_a == ClipStatus.WAITING_APPROVAL
        assert report_a["technical_check"]["all_passed"] is True

        # Case B: human review disabled -> APPROVED
        status_b, report_b = manager.evaluate_clip(
            rendered_video_path=video_path,
            segment_text=good_text,
            hook_text="Hook",
            payoff_text="Payoff",
            human_review_required=False,
        )
        assert status_b == ClipStatus.APPROVED

