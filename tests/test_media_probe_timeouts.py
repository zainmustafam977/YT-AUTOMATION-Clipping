import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from shorts_engine.services.rendering.compositor import VideoCompositor
from shorts_engine.services.quality_gate.technical_check import TechnicalVerifier
from shorts_engine.services.rendering.thumbnail_generator import ThumbnailGenerator
from shorts_engine.services.audio.mastering import AudioMasteringEngine
from shorts_engine.services.execution.watchdog import SubprocessWatchdog, SubprocessTimeoutError, ExecutionResult


def test_compositor_validate_render_enforces_timeout(tmp_path):
    """Verify that validate_render uses SubprocessWatchdog.run with timeout=15.0."""
    video_file = tmp_path / "test.mp4"
    video_file.write_bytes(b"dummy content 12345")

    compositor = VideoCompositor()

    with patch.object(SubprocessWatchdog, "run", side_effect=SubprocessTimeoutError(cmd=["ffprobe"], timeout_sec=15.0)) as mock_run:
        res = compositor.validate_render(video_file)

    assert mock_run.called
    assert mock_run.call_args[1]["timeout_sec"] == 15.0
    assert res.is_valid is False
    assert "timed out" in res.error.lower()


def test_technical_verifier_enforces_timeout(tmp_path):
    """Verify that TechnicalVerifier.verify uses SubprocessWatchdog.run with timeout=15.0."""
    video_file = tmp_path / "test.mp4"
    video_file.write_bytes(b"dummy content 12345")

    verifier = TechnicalVerifier()

    with patch.object(SubprocessWatchdog, "run", side_effect=SubprocessTimeoutError(cmd=["ffprobe"], timeout_sec=15.0)) as mock_run:
        passed, details = verifier.verify(video_file)

    assert mock_run.called
    assert mock_run.call_args[1]["timeout_sec"] == 15.0
    assert passed is False
    assert "timed out" in details["error"].lower() or "failed" in details["error"].lower()
    assert "checks" in details, "TechnicalVerifier must return structured checks dict on timeout"
    assert details["checks"].get("probe_timeout") is False
    assert details["all_passed"] is False


def test_thumbnail_generator_enforces_timeout(tmp_path):
    """Verify that ThumbnailGenerator.extract_frame_ffmpeg uses SubprocessWatchdog.run with timeout=15.0."""
    video_file = tmp_path / "test.mp4"
    video_file.write_bytes(b"dummy")
    out_file = tmp_path / "thumb.jpg"

    with patch.object(SubprocessWatchdog, "run", side_effect=SubprocessTimeoutError(cmd=["ffmpeg"], timeout_sec=15.0)) as mock_run:
        success = ThumbnailGenerator.extract_frame_ffmpeg(video_file, out_file)

    assert mock_run.called
    assert mock_run.call_args[1]["timeout_sec"] == 15.0
    assert success is False


def test_audio_mastering_measure_loudness_enforces_timeout(tmp_path):
    """Verify that AudioMasteringEngine.measure_loudness uses SubprocessWatchdog.run with timeout=30.0."""
    media_file = tmp_path / "test.wav"
    media_file.write_bytes(b"dummy")

    engine = AudioMasteringEngine()

    with patch.object(SubprocessWatchdog, "run", side_effect=SubprocessTimeoutError(cmd=["ffmpeg"], timeout_sec=30.0)) as mock_run:
        res = engine.measure_loudness(media_file)

    assert mock_run.called
    assert mock_run.call_args[1]["timeout_sec"] == 30.0
    assert "error" in res


def test_execution_result_returncode_compatibility():
    """Verify that ExecutionResult provides returncode property compatible with CompletedProcess."""
    res = ExecutionResult(cmd=["true"], exit_code=0, stdout="", stderr="", duration_ms=10.0)
    assert res.returncode == 0
    assert res.is_success is True

    res_fail = ExecutionResult(cmd=["false"], exit_code=1, stdout="", stderr="err", duration_ms=10.0)
    assert res_fail.returncode == 1
    assert res_fail.is_success is False


def test_compositor_encoder_probes_use_watchdog():
    """Verify that compositor hardware encoder probe and encoder list use SubprocessWatchdog."""
    import shorts_engine.services.rendering.compositor as comp_mod
    comp_mod._CACHED_ENCODER = None

    with patch.object(SubprocessWatchdog, "run", return_value=ExecutionResult(cmd=["ffmpeg"], exit_code=0, stdout="h264_nvenc", stderr="", duration_ms=5.0)) as mock_run:
        _ = comp_mod._test_hardware_encoder("h264_nvenc")
        assert mock_run.called
        assert mock_run.call_args[1]["timeout_sec"] == 2.0

