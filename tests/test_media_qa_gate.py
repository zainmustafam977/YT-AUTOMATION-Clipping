import pytest
from pathlib import Path
from unittest.mock import patch
from shorts_engine.services.quality.media_qa import MediaQAGate, QAResult


def test_media_qa_missing_file():
    """Verifies that non-existent or empty files are rejected."""
    gate = MediaQAGate()
    res = gate.inspect_file(Path("non_existent_clip_12345.mp4"))
    assert not res.passed
    assert any("missing or 0 bytes" in v for v in res.violations)


def test_media_qa_dimension_mismatch(tmp_path):
    """Verifies that non-portrait videos (e.g. 1920x1080 landscape) are rejected."""
    dummy_video = tmp_path / "test_landscape.mp4"
    dummy_video.write_bytes(b"dummy mp4 data" * 100)

    mock_probe = {
        "valid": True,
        "duration": 30.0,
        "width": 1920,
        "height": 1080,
        "video_codec": "h264",
        "audio_codec": "aac",
        "fps": 30.0,
    }

    gate = MediaQAGate(min_duration_sec=15, max_duration_sec=60)
    with patch.object(gate, "_probe_media_streams", return_value=mock_probe):
        with patch.object(gate, "_detect_black_frames", return_value=0.0):
            with patch.object(gate, "_detect_audio_silence", return_value=0.0):
                res = gate.inspect_file(dummy_video)

    assert not res.passed
    assert any("Invalid dimensions" in v for v in res.violations)


def test_media_qa_excessive_black_frames(tmp_path):
    """Verifies that videos with continuous black screens exceeding threshold are rejected."""
    dummy_video = tmp_path / "test_black.mp4"
    dummy_video.write_bytes(b"dummy mp4 data" * 100)

    mock_probe = {
        "valid": True,
        "duration": 30.0,
        "width": 1080,
        "height": 1920,
        "video_codec": "h264",
        "audio_codec": "aac",
        "fps": 30.0,
    }

    gate = MediaQAGate(min_duration_sec=15, max_duration_sec=60, max_allowed_black_duration_sec=2.0)
    with patch.object(gate, "_probe_media_streams", return_value=mock_probe):
        with patch.object(gate, "_detect_black_frames", return_value=4.5):  # 4.5s black screen
            with patch.object(gate, "_detect_audio_silence", return_value=0.0):
                res = gate.inspect_file(dummy_video)

    assert not res.passed
    assert any("black screen detected" in v for v in res.violations)


def test_media_qa_valid_clip(tmp_path):
    """Verifies that standard 1080x1920 portrait clip with audio passes inspection."""
    dummy_video = tmp_path / "test_valid.mp4"
    dummy_video.write_bytes(b"dummy mp4 data" * 100)

    mock_probe = {
        "valid": True,
        "duration": 35.0,
        "width": 1080,
        "height": 1920,
        "video_codec": "h264",
        "audio_codec": "aac",
        "fps": 30.0,
    }

    gate = MediaQAGate(min_duration_sec=15, max_duration_sec=60)
    with patch.object(gate, "_probe_media_streams", return_value=mock_probe):
        with patch.object(gate, "_detect_black_frames", return_value=0.2):
            with patch.object(gate, "_detect_audio_silence", return_value=1.0):
                res = gate.inspect_file(dummy_video)

    assert res.passed
    assert len(res.violations) == 0
    assert res.metrics["duration_sec"] == 35.0


def test_media_qa_single_pass_combined_filtergraph(tmp_path):
    """Verifies that MediaQAGate executes blackdetect and silencedetect in a single FFmpeg pass (PLAN-P2-02 / F-51)."""
    dummy_video = tmp_path / "test_single_pass.mp4"
    dummy_video.write_bytes(b"dummy mp4 data" * 100)

    mock_probe = {
        "valid": True,
        "duration": 35.0,
        "width": 1080,
        "height": 1920,
        "video_codec": "h264",
        "audio_codec": "aac",
        "fps": 30.0,
    }

    gate = MediaQAGate(min_duration_sec=15, max_duration_sec=60)
    with patch.object(gate, "_probe_media_streams", return_value=mock_probe):
        with patch("shorts_engine.services.quality.media_qa.SubprocessWatchdog.run") as mock_run:
            from subprocess import CompletedProcess
            mock_run.return_value = CompletedProcess(
                args=[],
                returncode=0,
                stdout="",
                stderr="[blackdetect @ 0x1] black_start: 1.0 black_end: 1.5 black_duration: 0.5\n[silencedetect @ 0x2] silence_start: 2.0 | silence_end: 3.2 | silence_duration: 1.2",
            )
            res = gate.inspect_file(dummy_video)

    assert res.passed
    assert res.metrics["black_frames_duration_sec"] == 0.5
    assert res.metrics["audio_silence_duration_sec"] == 1.2
    # Verify that SubprocessWatchdog.run was called only once for anomalies (single pass)
    assert mock_run.call_count == 1
    called_cmd = mock_run.call_args[0][0]
    assert "-vf" in called_cmd and "blackdetect" in called_cmd[called_cmd.index("-vf") + 1]
    assert "-af" in called_cmd and "silencedetect" in called_cmd[called_cmd.index("-af") + 1]

