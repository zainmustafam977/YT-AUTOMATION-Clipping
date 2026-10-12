"""
Unit & Integration Tests for Phase 4:
Hardware Acceleration & AI Virality Engine

Components tested:
1. Task 4.1: Adaptive Hardware Acceleration Engine (shorts_engine/services/rendering/hwaccel.py)
   - HardwareEncoderProber priority discovery (NVENC -> QSV -> AMF -> libx264)
   - Probe caching, singleton pattern, and settings overrides
   - Command argument generation for video encoding and hardware decoders
2. Task 4.2: Silero VAD Pre-Filter & Whisper Hallucination Shield (shorts_engine/services/transcription/engine.py)
   - Silero VAD speech segment detection and audio masking
   - Hallucination shield parameters (compression_ratio_threshold=2.4, no_speech_threshold=0.6,
     temperature schedule, repetition_penalty=1.2, no_repeat_ngram_size=3)
3. Task 4.3: Multi-Pass AI Virality Hunter & Yield Maximizer (shorts_engine/services/clip_selection/selector.py & prompts.py)
   - Pass 1: Acoustic peak detector (EBU R128 momentary loudness telemetry)
   - Pass 2: 50% overlapping sliding window segmenter with boundary snapping
   - Pass 3: Multi-criteria composite virality scoring (0.35, 0.25, 0.25, 0.15 weights)
4. Task 4.4: Multi-Aspect Ratio Compositor (shorts_engine/services/rendering/compositor.py)
   - Aspect ratio probing (16:9 widescreen, 9:16 vertical, 1:1 square, 4:3 standard)
   - Adaptive filter synthesis: tracked crop, direct vertical passthrough, Gaussian-blurred mirror
5. Task 4.5: Sub-Frame AV Lip-Sync & Two-Pass -14 LUFS Mastering (shorts_engine/services/rendering/trimmer.py & shorts_engine/services/audio/mastering.py)
   - Sub-frame AV lip-sync flags (-avoid_negative_ts make_zero -async 1)
   - 3kHz vocal presence filter (+2.5dB boost)
   - Two-pass ITU-R BS.1770-4 loudness normalization
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from shorts_engine.config.settings import settings
from shorts_engine.services.rendering.hwaccel import HardwareEncoderProber
from shorts_engine.services.transcription.engine import (
    SileroVADFilter,
    TranscriptionService,
)
from shorts_engine.services.transcription.formatter import FormattedTranscript, WordSegment
from shorts_engine.services.clip_selection.selector import (
    AcousticPeakDetector,
    SlidingWindowSegmenter,
    SelectedClip,
)
from shorts_engine.services.clip_selection.prompts import (
    compute_composite_virality_score,
)
from shorts_engine.services.rendering.compositor import (
    probe_aspect_ratio,
    VideoCompositor,
)
from shorts_engine.services.rendering.trimmer import ClipTrimmerService
from shorts_engine.services.audio.mastering import AudioMasteringEngine
from shorts_engine.services.execution.watchdog import ExecutionResult


# ==============================================================================
# Task 4.1: Adaptive Hardware Acceleration Engine Tests
# ==============================================================================


@pytest.fixture(autouse=True)
def reset_hwaccel_fixtures():
    """Resets hardware encoder singleton and cache before and after every test."""
    HardwareEncoderProber.reset_instance()
    yield
    HardwareEncoderProber.reset_instance()


def test_hwaccel_singleton_and_cache():
    """Verifies that HardwareEncoderProber maintains a singleton instance and probe cache."""
    prober1 = HardwareEncoderProber.get_instance()
    prober2 = HardwareEncoderProber.get_instance()
    assert prober1 is prober2

    prober1._probe_cache["test_encoder"] = True
    assert prober2._probe_cache.get("test_encoder") is True

    prober1.reset_cache()
    assert "test_encoder" not in prober1._probe_cache


def test_hwaccel_encoder_args_generation():
    """Verifies optimized encoder arguments for NVENC, QSV, AMF, and libx264 fallback."""
    prober = HardwareEncoderProber.get_instance()

    # NVIDIA NVENC
    nvenc_args = prober.get_encoder_args("h264_nvenc", crf=19, preset="p5", bitrate="12M")
    assert nvenc_args == ["-preset", "p5", "-cq", "19", "-b:v", "12M"]

    # Intel QuickSync QSV
    qsv_args = prober.get_encoder_args("h264_qsv", crf=20, preset="medium", bitrate="12M")
    assert qsv_args == ["-preset", "medium", "-global_quality", "20", "-b:v", "12M"]

    # AMD AMF
    amf_args = prober.get_encoder_args("h264_amf", crf=20, preset="balanced", bitrate="12M")
    assert amf_args == ["-quality", "balanced", "-rc", "cqp", "-qp_p", "20", "-b:v", "12M"]

    # CPU libx264 fallback
    cpu_args = prober.get_encoder_args("libx264", crf=19, preset="fast")
    assert "-preset" in cpu_args
    assert "-crf" in cpu_args
    assert "-threads" in cpu_args
    assert "0" in cpu_args


def test_hwaccel_hwaccel_args():
    """Verifies input hardware acceleration flags for decoders."""
    prober = HardwareEncoderProber.get_instance()
    assert prober.get_hwaccel_args("h264_nvenc") == ["-hwaccel", "cuda"]
    assert prober.get_hwaccel_args("h264_qsv") == ["-hwaccel", "qsv"]
    assert prober.get_hwaccel_args("h264_amf") == ["-hwaccel", "d3d11va"]
    assert prober.get_hwaccel_args("libx264") == []


def test_hwaccel_nvenc_priority_selection():
    """Verifies that NVENC is chosen first when present and passes probe test."""
    prober = HardwareEncoderProber.get_instance()

    with patch.object(prober, "get_available_ffmpeg_encoders", return_value="encoders: h264_nvenc h264_qsv h264_amf libx264"):
        with patch.object(prober, "test_encoder", return_value=True):
            enc, args = prober.get_optimal_encoder(force_refresh=True)
            assert enc == "h264_nvenc"
            assert "-cq" in args
            assert "-preset" in args


def test_hwaccel_qsv_fallback_when_nvenc_fails():
    """Verifies fallback to Intel QuickSync when NVENC probe fails."""
    prober = HardwareEncoderProber.get_instance()

    with patch.object(prober, "get_available_ffmpeg_encoders", return_value="encoders: h264_nvenc h264_qsv libx264"):
        # NVENC fails probe, QSV succeeds
        def mock_test(enc_name: str, timeout_sec: float = 2.0) -> bool:
            return enc_name == "h264_qsv"

        with patch.object(prober, "test_encoder", side_effect=mock_test):
            enc, args = prober.get_optimal_encoder(force_refresh=True)
            assert enc == "h264_qsv"
            assert "-global_quality" in args


def test_hwaccel_amf_fallback_when_nvenc_and_qsv_fail():
    """Verifies fallback to AMD AMF when NVENC and QSV fail or are unavailable."""
    prober = HardwareEncoderProber.get_instance()

    with patch.object(prober, "get_available_ffmpeg_encoders", return_value="encoders: h264_amf libx264"):
        with patch.object(prober, "test_encoder", return_value=True):
            enc, args = prober.get_optimal_encoder(force_refresh=True)
            assert enc == "h264_amf"
            assert "-quality" in args
            assert "-qp_p" in args


def test_hwaccel_cpu_fallback_when_all_fail():
    """Verifies multi-threaded CPU libx264 fallback when no hardware encoders work."""
    prober = HardwareEncoderProber.get_instance()

    with patch.object(prober, "get_available_ffmpeg_encoders", return_value="encoders: libx264"):
        with patch.object(prober, "test_encoder", return_value=False):
            enc, args = prober.get_optimal_encoder(force_refresh=True)
            assert enc == "libx264"
            assert "-crf" in args
            assert "-threads" in args


def test_hwaccel_settings_override():
    """Verifies HARDWARE_ENCODER_OVERRIDE takes precedence without probing."""
    prober = HardwareEncoderProber.get_instance()

    with patch.object(settings, "HARDWARE_ENCODER_OVERRIDE", "h264_nvenc"):
        with patch.object(prober, "test_encoder") as mock_test:
            enc, args = prober.get_optimal_encoder(force_refresh=True)
            assert enc == "h264_nvenc"
            mock_test.assert_not_called()


def test_hwaccel_disabled_by_settings():
    """Verifies HARDWARE_ACCELERATION_ENABLED=False immediately returns libx264."""
    prober = HardwareEncoderProber.get_instance()

    with patch.object(settings, "HARDWARE_ACCELERATION_ENABLED", False):
        with patch.object(prober, "test_encoder") as mock_test:
            enc, args = prober.get_optimal_encoder(force_refresh=True)
            assert enc == "libx264"
            mock_test.assert_not_called()


# ==============================================================================
# Task 4.2: Silero VAD Pre-Filter & Whisper Hallucination Shield Tests
# ==============================================================================


def test_silero_vad_filter_defaults():
    """Verifies default parameters and initialization for SileroVADFilter."""
    vad = SileroVADFilter(min_silence_duration_ms=400, threshold=0.5, speech_pad_ms=200)
    assert vad.min_silence_duration_ms == 400
    assert vad.threshold == 0.5
    assert vad.speech_pad_ms == 200


def test_silero_vad_speech_detection_ffmpeg_fallback(tmp_path):
    """Verifies fallback silencedetect parsing in SileroVADFilter when direct model is omitted."""
    vad = SileroVADFilter()
    dummy_audio = tmp_path / "test_audio.wav"
    dummy_audio.write_bytes(b"dummy")

    dummy_ffmpeg_stderr = """
    [silencedetect @ 0000021b] silence_start: 2.500
    [silencedetect @ 0000021b] silence_end: 4.800 | silence_duration: 2.300
    [silencedetect @ 0000021b] silence_start: 10.200
    [silencedetect @ 0000021b] silence_end: 12.000 | silence_duration: 1.800
    """
    exec_res = ExecutionResult(cmd=["ffmpeg"], exit_code=0, stdout="", stderr=dummy_ffmpeg_stderr, duration_ms=10.0)

    # Force model extraction to fail so it falls back to silencedetect
    with patch("shorts_engine.services.execution.watchdog.SubprocessWatchdog.run", return_value=exec_res):
        segments = vad.detect_speech_segments(dummy_audio)
        assert len(segments) >= 2
        # First speech chunk is before first silence: 0.0 to 2.5
        assert segments[0] == (0.0, 2.5)
        # Second chunk is between silences: 4.8 to 10.2
        assert segments[1] == (4.8, 10.2)


def test_silero_vad_mask_non_speech_audio(tmp_path):
    """Verifies that mask_non_speech_audio builds correct volume expression."""
    vad = SileroVADFilter()
    input_audio = tmp_path / "in.wav"
    input_audio.write_bytes(b"dummy")
    output_audio = tmp_path / "out.wav"

    speech_segments = [(1.0, 5.0), (7.0, 12.0)]
    recorded_cmds = []

    def mock_run(cmd, **kwargs):
        recorded_cmds.append([str(c) for c in cmd])
        return ExecutionResult(cmd=cmd, exit_code=0, stdout="", stderr="", duration_ms=10.0)

    with patch("shorts_engine.services.execution.watchdog.SubprocessWatchdog.run", side_effect=mock_run):
        out = vad.mask_non_speech_audio(input_audio, output_audio, speech_segments=speech_segments)
        assert out == output_audio
        assert len(recorded_cmds) > 0
        cmd_str = " ".join(recorded_cmds[0])
        assert "between(t,1.000,5.000)+between(t,7.000,12.000)" in cmd_str
        assert "volume=enable=" in cmd_str


def test_whisper_hallucination_shield_parameters():
    """Verifies that TranscriptionService exposes the required hallucination shield parameters."""
    params = TranscriptionService.get_hallucination_shield_params()

    assert params["vad_filter"] is True
    assert params["compression_ratio_threshold"] == 2.4
    assert params["no_speech_threshold"] == 0.6
    assert params["temperature"] == (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
    assert params["repetition_penalty"] == 1.2
    assert params["no_repeat_ngram_size"] == 3
    assert params["condition_on_previous_text"] is False
    assert params["vad_parameters"]["min_silence_duration_ms"] == 400


def test_transcription_service_applies_hallucination_shields(tmp_path):
    """Verifies that TranscriptionService passes hallucination shield parameters to faster_whisper."""
    service = TranscriptionService(model_size="tiny", device="cpu", compute_type="int8")

    dummy_audio = tmp_path / "sample.wav"
    dummy_audio.write_bytes(b"RIFF dummy wav header")

    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([], MagicMock(duration=5.0))
    service._model = mock_model

    # Disable Groq cloud whisper to test local pipeline
    with patch.object(settings, "USE_GROQ_WHISPER", False):
        service.transcribe(dummy_audio, apply_vad_mask=False)

    mock_model.transcribe.assert_called_once()
    _, kwargs = mock_model.transcribe.call_args

    assert kwargs.get("compression_ratio_threshold") == 2.4
    assert kwargs.get("no_speech_threshold") == 0.6
    assert kwargs.get("repetition_penalty") == 1.2
    assert kwargs.get("no_repeat_ngram_size") == 3
    assert kwargs.get("condition_on_previous_text") is False


# ==============================================================================
# Task 4.3: Multi-Pass AI Virality Hunter & Yield Maximizer Tests
# ==============================================================================


def test_acoustic_peak_detector_ebur128_parsing(tmp_path):
    """Verifies that AcousticPeakDetector parses momentary loudness spikes from ebur128 output."""
    dummy_media = tmp_path / "video.mp4"
    dummy_media.write_bytes(b"dummy")

    dummy_ffmpeg_output = """
    pts_time:1.000000
    lavfi.r128.M=-28.500000
    pts_time:2.500000
    lavfi.r128.M=-14.200000
    pts_time:3.000000
    lavfi.r128.M=-13.800000
    pts_time:7.200000
    lavfi.r128.M=-11.000000
    """
    exec_res = ExecutionResult(cmd=["ffmpeg"], exit_code=0, stdout=dummy_ffmpeg_output, stderr="", duration_ms=10.0)

    with patch("shorts_engine.services.execution.watchdog.SubprocessWatchdog.run", return_value=exec_res):
        peaks = AcousticPeakDetector.detect_acoustic_peaks(
            dummy_media,
            min_peak_gap_sec=2.0,
            lufs_threshold=-20.0,
        )
        assert len(peaks) == 2
        # First peak around 2.5s (-14.2 LUFS)
        assert peaks[0]["timestamp_sec"] == 2.5
        assert peaks[0]["momentary_lufs"] == -14.2
        assert peaks[0]["energy_score"] > 5.0
        # Second peak around 7.2s (-11.0 LUFS)
        assert peaks[1]["timestamp_sec"] == 7.2
        assert peaks[1]["momentary_lufs"] == -11.0


def test_acoustic_peak_detector_nonexistent_file():
    """Verifies that AcousticPeakDetector safely returns empty list for nonexistent file."""
    assert AcousticPeakDetector.detect_acoustic_peaks(Path("nonexistent_file.mp4")) == []


def test_sliding_window_segmenter():
    """Verifies that SlidingWindowSegmenter creates 50% overlapping windows and snaps boundaries."""
    # Build transcript with 100 seconds of speech
    words = []
    t = 0.0
    for i in range(100):
        words.append(WordSegment(word=f"word{i}", start=round(t, 2), end=round(t + 0.8, 2)))
        t += 1.0

    transcript = FormattedTranscript(
        words=words,
        language="en",
        full_text=" ".join(w.word for w in words),
    )

    acoustic_peaks = [
        {"timestamp_sec": 42.0, "momentary_lufs": -12.0, "energy_score": 9.0},
    ]

    windows = SlidingWindowSegmenter.generate_candidate_windows(
        transcript=transcript,
        target_min_sec=30.0,
        target_max_sec=60.0,
        overlap_ratio=0.50,
        acoustic_peaks=acoustic_peaks,
    )

    assert len(windows) >= 2
    # Verify windows have reasonable durations and overlap
    for w in windows:
        assert 30.0 <= w["duration_sec"] <= 62.0
        assert "text" in w
        assert len(w["words"]) > 0

    # Verify peak correlation
    peak_win = [w for w in windows if w["has_acoustic_peak"]]
    assert len(peak_win) > 0
    assert any(w["acoustic_score"] == 9.0 for w in peak_win)


def test_sliding_window_segmenter_short_transcript():
    """Verifies behavior when total transcript duration is less than target minimum."""
    words = [
        WordSegment(word="Hello", start=0.0, end=0.8),
        WordSegment(word="world", start=1.0, end=1.8),
    ]
    transcript = FormattedTranscript(words=words, language="en", full_text="Hello world")
    windows = SlidingWindowSegmenter.generate_candidate_windows(transcript, target_min_sec=30.0, target_max_sec=60.0)
    assert len(windows) == 1
    assert windows[0]["duration_sec"] == 1.8


def test_compute_composite_virality_score_weights():
    """Verifies that composite virality score weights match blueprint (0.35, 0.25, 0.25, 0.15)."""
    # Max scores across all dimensions
    score_max = compute_composite_virality_score(
        curiosity_gap=10.0,
        narrative_climax=10.0,
        standalone_coherence=10.0,
        loopability=10.0,
    )
    assert score_max == 1.0

    # Min scores
    score_min = compute_composite_virality_score(0.0, 0.0, 0.0, 0.0)
    assert score_min == 0.0

    # Check individual dimension contributions
    # Curiosity Gap: 35%
    assert compute_composite_virality_score(10.0, 0.0, 0.0, 0.0) == 0.35
    # Narrative Climax: 25%
    assert compute_composite_virality_score(0.0, 10.0, 0.0, 0.0) == 0.25
    # Standalone Coherence: 25%
    assert compute_composite_virality_score(0.0, 0.0, 10.0, 0.0) == 0.25
    # Loopability: 15%
    assert compute_composite_virality_score(0.0, 0.0, 0.0, 10.0) == 0.15

    # Boundary clamping
    assert compute_composite_virality_score(20.0, 20.0, 20.0, 20.0) == 1.0
    assert compute_composite_virality_score(-5.0, -5.0, -5.0, -5.0) == 0.0


def test_selected_clip_multi_criteria_fields():
    """Verifies SelectedClip data model preserves multi-criteria scores and metadata."""
    clip = SelectedClip(
        start_ms=1000,
        end_ms=31000,
        score=0.88,
        reason="[gemini:gemini-3.8-flash:120ms] High tension hook with surprise ending",
        hook_text="Did you know this secret?",
        payoff_text="And that's why it works.",
        suggested_title="Top Secret Exposed",
        suggested_description="Check this out #shorts",
        suggested_tags=["#shorts", "#tech"],
        segment_text="Did you know this secret? ... And that's why it works.",
        curiosity_gap_score=9.0,
        narrative_climax_score=8.5,
        standalone_coherence_score=9.0,
        loopability_score=7.0,
        composite_score=0.88,
    )
    d = clip.to_dict()
    assert d["curiosity_gap_score"] == 9.0
    assert d["narrative_climax_score"] == 8.5
    assert d["standalone_coherence_score"] == 9.0
    assert d["loopability_score"] == 7.0
    assert d["composite_score"] == 0.88
    assert d["ai_provider"] == "gemini"
    assert d["duration_sec"] == 30.0


# ==============================================================================
# Task 4.4: Multi-Aspect Ratio Compositor Tests
# ==============================================================================


def test_probe_aspect_ratio_categorization(tmp_path):
    """Verifies ffprobe categorization of 16:9, 9:16, 1:1, and 4:3 videos."""
    dummy_video = tmp_path / "sample.mp4"
    dummy_video.write_bytes(b"dummy")

    def make_ffprobe_res(w: int, h: int):
        data = {"streams": [{"codec_type": "video", "width": w, "height": h}]}
        return ExecutionResult(cmd=["ffprobe"], exit_code=0, stdout=json.dumps(data), stderr="", duration_ms=5.0)

    # 16:9 landscape
    with patch("shorts_engine.services.execution.watchdog.SubprocessWatchdog.run", return_value=make_ffprobe_res(1920, 1080)):
        w, h, mode = probe_aspect_ratio(dummy_video)
        assert (w, h, mode) == (1920, 1080, "16:9")

    # 9:16 vertical
    with patch("shorts_engine.services.execution.watchdog.SubprocessWatchdog.run", return_value=make_ffprobe_res(1080, 1920)):
        w, h, mode = probe_aspect_ratio(dummy_video)
        assert (w, h, mode) == (1080, 1920, "9:16")

    # 1:1 square
    with patch("shorts_engine.services.execution.watchdog.SubprocessWatchdog.run", return_value=make_ffprobe_res(1080, 1080)):
        w, h, mode = probe_aspect_ratio(dummy_video)
        assert (w, h, mode) == (1080, 1080, "1:1")

    # 4:3 standard fullscreen
    with patch("shorts_engine.services.execution.watchdog.SubprocessWatchdog.run", return_value=make_ffprobe_res(1440, 1080)):
        w, h, mode = probe_aspect_ratio(dummy_video)
        assert (w, h, mode) == (1440, 1080, "4:3")


def test_build_aspect_ratio_filter_widescreen(tmp_path):
    """Verifies 16:9 widescreen crop filter creation."""
    compositor = VideoCompositor(output_dir=tmp_path)
    dummy_source = tmp_path / "landscape.mp4"

    filter_str, is_complex = compositor.build_aspect_ratio_filter(
        source_path=dummy_source,
        crop_plan=None,
        aspect_ratio_mode="16:9",
    )
    assert not is_complex
    assert "crop=min(iw\\,ih*9/16):ih" in filter_str
    assert "scale=1080:1920" in filter_str


def test_build_aspect_ratio_filter_vertical_passthrough(tmp_path):
    """Verifies 9:16 vertical passthrough with proper center padding."""
    compositor = VideoCompositor(output_dir=tmp_path)
    dummy_source = tmp_path / "vertical.mp4"

    filter_str, is_complex = compositor.build_aspect_ratio_filter(
        source_path=dummy_source,
        aspect_ratio_mode="9:16",
    )
    assert not is_complex
    assert "force_original_aspect_ratio=decrease" in filter_str
    assert "pad=1080:1920:(ow-iw)/2:(oh-ih)/2" in filter_str


def test_build_aspect_ratio_filter_blurred_mirror(tmp_path):
    """Verifies 1:1 and 4:3 smart Gaussian-blurred background mirror filter."""
    compositor = VideoCompositor(output_dir=tmp_path)
    dummy_source = tmp_path / "square.mp4"

    for mode in ("1:1", "4:3", "blurred_mirror"):
        filter_str, is_complex = compositor.build_aspect_ratio_filter(
            source_path=dummy_source,
            aspect_ratio_mode=mode,
        )
        assert is_complex
        assert "split [main][bg]" in filter_str
        assert "gblur=sigma=25" in filter_str
        assert "overlay=(W-w)/2:(H-h)/2" in filter_str


# ==============================================================================
# Task 4.5: Sub-Frame AV Lip-Sync & Two-Pass -14 LUFS Mastering Tests
# ==============================================================================


def test_subframe_lipsync_flags_in_trimmer(tmp_path):
    """Verifies that ClipTrimmerService incorporates sub-frame lip-sync flags."""
    trimmer = ClipTrimmerService(db=MagicMock())
    source_video = tmp_path / "src.mp4"
    source_video.write_bytes(b"dummy")
    out_video = tmp_path / "subclip.mp4"

    recorded_cmds = []

    def mock_run(cmd, **kwargs):
        recorded_cmds.append([str(c) for c in cmd])
        return ExecutionResult(cmd=cmd, exit_code=0, stdout="", stderr="", duration_ms=10.0)

    with patch("shorts_engine.services.execution.watchdog.SubprocessWatchdog.run", side_effect=mock_run):
        trimmer.extract_subclip_with_lipsync(
            source_path=source_video,
            start_sec=10.5,
            end_sec=25.2,
            output_path=out_video,
        )
        assert len(recorded_cmds) > 0
        cmd = recorded_cmds[0]
        # Verify sub-frame timestamp elimination and audio sync flags
        assert "-avoid_negative_ts" in cmd
        assert cmd[cmd.index("-avoid_negative_ts") + 1] == "make_zero"
        assert "-async" in cmd
        assert cmd[cmd.index("-async") + 1] == "1"


def test_vocal_presence_in_audio_mastering():
    """Verifies that 3kHz vocal presence filter (+2.5dB boost) is incorporated into audio filters."""
    engine = AudioMasteringEngine(vocal_presence_freq_hz=3000, vocal_presence_gain_db=2.5)

    clean_filt = engine.build_speech_clean_filter(input_label="0:a")
    assert "equalizer=f=3000:t=q:w=1.0:g=2.5" in clean_filt

    duck_filt = engine.build_sidechain_ducking_filter_graph(speech_input_label="0:a", music_input_label="1:a")
    assert "equalizer=f=3000:t=q:w=1.0:g=2.5" in duck_filt

    single_filt = engine.build_single_stream_master_filter(input_label="0:a")
    assert "equalizer=f=3000:t=q:w=1.0:g=2.5" in single_filt


def test_two_pass_loudness_normalize(tmp_path):
    """Verifies that two_pass_loudness_normalize executes Pass 1 measurement and Pass 2 linear mastering."""
    engine = AudioMasteringEngine(target_lufs=-14.0, true_peak_dbfs=-1.0)
    in_audio = tmp_path / "in.wav"
    in_audio.write_bytes(b"dummy")
    out_audio = tmp_path / "out.wav"

    dummy_pass1_json = json.dumps({
        "input_i": "-22.40",
        "input_tp": "-3.10",
        "input_lra": "8.50",
        "input_thresh": "-33.20",
        "target_offset": "0.15",
    })
    dummy_pass2_json = json.dumps({
        "input_i": "-22.40",
        "input_tp": "-3.10",
        "input_lra": "8.50",
        "input_thresh": "-33.20",
        "output_i": "-14.05",
        "output_tp": "-1.02",
        "output_lra": "6.80",
        "output_thresh": "-24.20",
        "target_offset": "0.05",
    })

    recorded_cmds = []

    def mock_run(cmd, **kwargs):
        recorded_cmds.append([str(c) for c in cmd])
        stderr_json = dummy_pass1_json if len(recorded_cmds) == 1 else dummy_pass2_json
        return ExecutionResult(cmd=cmd, exit_code=0, stdout="", stderr=stderr_json, duration_ms=10.0)

    with patch("shorts_engine.services.execution.watchdog.SubprocessWatchdog.run", side_effect=mock_run):
        engine.two_pass_loudness_normalize(in_audio, out_audio, duration_sec=15.0)

        assert len(recorded_cmds) == 2
        # Command 1: Pass 1 measurement
        cmd1_str = " ".join(recorded_cmds[0])
        assert "loudnorm=I=-14.0:TP=-1.0:LRA=7:print_format=json" in cmd1_str

        # Command 2: Pass 2 linear application with measured parameters
        cmd2_str = " ".join(recorded_cmds[1])
        assert "measured_I=-22.4" in cmd2_str
        assert "measured_TP=-3.1" in cmd2_str
        assert "measured_LRA=8.5" in cmd2_str
        assert "measured_thresh=-33.2" in cmd2_str
        assert "linear=true" in cmd2_str


def test_two_pass_loudness_normalize_verification_probe_fallback(tmp_path):
    """When Pass 2 output JSON does not contain output_i, an explicit post-measurement probe command must execute."""
    engine = AudioMasteringEngine(target_lufs=-14.0, true_peak_dbfs=-1.0)
    in_audio = tmp_path / "in.wav"
    in_audio.write_bytes(b"dummy")
    out_audio = tmp_path / "out.wav"

    dummy_pass1_json = json.dumps({
        "input_i": "-22.40",
        "input_tp": "-3.10",
        "input_lra": "8.50",
        "input_thresh": "-33.20",
        "target_offset": "0.15",
    })

    recorded_cmds = []

    def mock_run(cmd, **kwargs):
        recorded_cmds.append([str(c) for c in cmd])
        return ExecutionResult(cmd=cmd, exit_code=0, stdout="", stderr=dummy_pass1_json, duration_ms=10.0)

    with patch("shorts_engine.services.execution.watchdog.SubprocessWatchdog.run", side_effect=mock_run):
        engine.two_pass_loudness_normalize(in_audio, out_audio, duration_sec=15.0)

        assert len(recorded_cmds) == 3
        # Command 3 must be the verification-probe on output audio
        cmd3_str = " ".join(recorded_cmds[2])
        assert str(out_audio) in cmd3_str
        assert "loudnorm" in cmd3_str
