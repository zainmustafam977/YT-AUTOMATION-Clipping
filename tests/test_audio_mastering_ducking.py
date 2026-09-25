"""
Unit & Integration Tests for Component 3: Studio-Grade Audio Mastering & Sidechain Ducking
(80Hz HPF, Voice Gate, Sidechain Ducking, EBU R128 Loudnorm, Music Library Manager)
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from shorts_engine.services.audio.mastering import AudioMasteringEngine
from shorts_engine.services.audio.music_library import MusicLibraryManager
from shorts_engine.services.rendering.compositor import VideoCompositor
from shorts_engine.services.reframing.cropper import CropPlan


def test_audio_mastering_engine_filter_construction():
    """Verifies that AudioMasteringEngine builds correct FFmpeg filter chains."""
    engine = AudioMasteringEngine(
        target_lufs=-14.0,
        true_peak_dbfs=-1.0,
        hpf_cutoff_hz=80,
        ducking_attack_ms=15,
        ducking_release_ms=350,
    )

    # 1. Clean speech filter
    clean_filter = engine.build_speech_clean_filter(input_label="0:a")
    assert "highpass=f=80:poles=2" in clean_filter
    assert "agate=range=-24dB:threshold=-36dB:attack=10:release=120" in clean_filter

    # 2. Sidechain ducking filter graph
    fc = engine.build_sidechain_ducking_filter_graph(
        speech_input_label="0:a",
        music_input_label="1:a",
        output_label="a_out",
        music_volume_ratio=0.25,
    )
    assert "[0:a]highpass=f=80:poles=2" in fc
    assert "asplit=2[voice_main][voice_side]" in fc
    assert "[1:a]volume=0.25[music_bed]" in fc
    assert "sidechaincompress=threshold=0.06:ratio=4:attack=15:release=350[music_ducked]" in fc
    assert "[voice_main][music_ducked]amix=inputs=2:duration=first:dropout_transition=2" in fc
    assert "loudnorm=I=-14.0:TP=-1.0:LRA=7[a_out]" in fc

    # 3. Single dialogue master filter
    single_fc = engine.build_single_stream_master_filter(input_label="0:a", output_label="a_out")
    assert "highpass=f=80:poles=2" in single_fc
    assert "agate=" in single_fc
    assert "loudnorm=I=-14.0:TP=-1.0:LRA=7[a_out]" in single_fc


def test_audio_mastering_engine_measure_loudness():
    """Verifies parsing of FFmpeg loudnorm measurement pass output."""
    engine = AudioMasteringEngine()
    dummy_ffmpeg_stderr = """
    Input #0, matroska,webm:
    [Parsed_loudnorm_0 @ 000001df]
    {
        "input_i" : "-18.42",
        "input_tp" : "-2.10",
        "input_lra" : "6.80",
        "input_thresh" : "-29.10",
        "output_i" : "-14.02",
        "output_tp" : "-1.01",
        "output_lra" : "5.40",
        "output_thresh" : "-24.20",
        "normalization_type" : "dynamic",
        "target_offset" : "0.02"
    }
    """
    mock_res = MagicMock()
    mock_res.returncode = 0
    mock_res.stderr = dummy_ffmpeg_stderr
    mock_res.stdout = ""

    with patch("subprocess.run", return_value=mock_res):
        metrics = engine.measure_loudness(Path("test.mp4"))
        assert metrics["input_i"] == -18.42
        assert metrics["input_tp"] == -2.10
        assert metrics["input_lra"] == 6.80
        assert metrics["target_offset"] == 0.02


def test_music_library_manager_cataloging(tmp_path):
    """Verifies that MusicLibraryManager indexes audio tracks by genre subfolders."""
    music_dir = tmp_path / "music"
    cinematic_dir = music_dir / "cinematic"
    cinematic_dir.mkdir(parents=True)
    lofi_dir = music_dir / "lofi"
    lofi_dir.mkdir(parents=True)

    track1 = cinematic_dir / "heroic_journey.mp3"
    track1.write_bytes(b"dummy_mp3_1")
    track2 = lofi_dir / "chill_study_beat.wav"
    track2.write_bytes(b"dummy_wav_1")
    general_track = music_dir / "ambient.aac"
    general_track.write_bytes(b"dummy_aac_1")

    mgr = MusicLibraryManager(library_dir=music_dir)
    catalog = mgr.scan_tracks()

    assert "cinematic" in catalog
    assert "lofi" in catalog
    assert "general" in catalog
    assert len(catalog["cinematic"]) == 1
    assert len(catalog["lofi"]) == 1
    assert len(catalog["general"]) == 1

    # Test mood matching
    assert mgr.get_track_for_mood("cinematic") == track1
    assert mgr.get_track_for_mood("lofi") == track2
    assert mgr.get_track_for_mood("unknown_genre") in [track1, track2, general_track]

    # Test list all tracks
    all_tracks = mgr.list_all_tracks()
    assert len(all_tracks) == 3
    track_names = {t["name"] for t in all_tracks}
    assert track_names == {"heroic_journey", "chill_study_beat", "ambient"}


def test_music_library_manager_empty_fallback(tmp_path):
    """Verifies that MusicLibraryManager gracefully returns None when library is empty."""
    empty_dir = tmp_path / "empty_music"
    mgr = MusicLibraryManager(library_dir=empty_dir)
    assert mgr.scan_tracks() == {}
    assert mgr.get_track_for_mood("cinematic") is None
    assert mgr.list_all_tracks() == []


def test_video_compositor_ducking_filter_integration(tmp_path):
    """Verifies that VideoCompositor properly wires music bed and sidechain ducking into FFmpeg."""
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"source_stream")
    music_file = tmp_path / "background_music.mp3"
    music_file.write_bytes(b"music_stream")

    out_dir = tmp_path / "output"
    compositor = VideoCompositor(output_dir=out_dir)

    crop_plan = CropPlan(
        crop_w=1080,
        crop_h=1920,
        crop_x=420,
        crop_y=0,
    )

    mock_run = MagicMock()
    mock_run.returncode = 0
    mock_run.stderr = ""
    mock_run.stdout = ""

    captured_cmds = []

    def fake_subprocess_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        return mock_run

    with patch("subprocess.run", side_effect=fake_subprocess_run), \
         patch.object(compositor, "validate_render") as mock_val:
        mock_val.return_value = MagicMock(is_valid=True)

        compositor.render_short(
            source_video_path=source_video,
            start_ms=0,
            end_ms=15000,
            crop_plan=crop_plan,
            output_filename="ducked_short.mp4",
            music_track_path=music_file,
            normalize_audio=True,
        )

    render_cmds = [c for c in captured_cmds if "-i" in c]
    assert len(render_cmds) > 0
    main_cmd = render_cmds[0]
    cmd_str = " ".join([str(c) for c in main_cmd])

    # Verifies background music loop input
    assert "-stream_loop -1 -i" in cmd_str
    assert str(music_file) in cmd_str

    # Verifies filter_complex ducking chain
    assert "-filter_complex" in cmd_str
    assert "sidechaincompress=" in cmd_str
    assert "agate=" in cmd_str
    assert "loudnorm=" in cmd_str
    assert "-map [a_out]" in cmd_str
