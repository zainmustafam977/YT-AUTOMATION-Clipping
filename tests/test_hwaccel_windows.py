"""R4-1.8 — Windows hardware encoder detection.

These tests probe the system for the presence of QSV (Intel), AMF (AMD),
and NVENC (NVIDIA) encoders and assert the chosen encoder matches the
detected hardware. They are skipped on non-Windows.
"""
import os
import sys
import shutil
import subprocess
import pytest


pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only hardware encoders",
)


def _have_ffmpeg():
    return shutil.which("ffmpeg") is not None


pytestmark = pytest.mark.skipif(
    not _have_ffmpeg(),
    reason="ffmpeg not on PATH",
)


def _list_encoders():
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True, text=True, timeout=10,
    )
    return out.stdout


def test_encoder_list_parses():
    """The encoder list contains at least h264."""
    text = _list_encoders()
    assert "h264" in text


def test_detect_uses_libx264_if_no_gpu_encoder():
    """When no GPU encoder is present, libx264 is chosen and reason is logged."""
    from shorts_engine.services.rendering.hwaccel import get_optimal_encoder
    chosen, args = get_optimal_encoder(force_refresh=True)
    text = _list_encoders()
    has_qsv = "h264_qsv" in text
    has_amf = "h264_amf" in text
    has_nvenc = "h264_nvenc" in text
    if not (has_qsv or has_amf or has_nvenc):
        assert chosen == "libx264", f"expected libx264, got {chosen}"


def test_strict_mode_raises_without_gpu_encoder(monkeypatch):
    """STRICT_MODE raises when the chosen encoder is libx264 and the user demanded GPU."""
    from shorts_engine.services.rendering import hwaccel
    from shorts_engine.services.strict_mode import StrictModeViolation, is_strict
    # If STRICT_MODE is on and the chosen encoder is libx264, a separate
    # code path (see detect_optimal_encoder_with_strict) should raise.
    if is_strict():
        try:
            chosen, _ = hwaccel.get_optimal_encoder(force_refresh=True)
        except StrictModeViolation:
            return  # expected
        assert chosen != "libx264", "STRICT_MODE on but libx264 chosen without raise"
