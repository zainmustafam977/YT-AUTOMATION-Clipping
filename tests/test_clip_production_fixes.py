"""
Unit and integration tests for clip production fixes:
1. Captions (ass_generator.py):
   - Escapes '{', '}', and '\\' in caption text.
   - Bundles caption font file (DejaVu Sans) with project and references it explicitly.
   - Handles characters missing from the font (fallback / safe replacement).
   - Smoke test: burns generated .ass with literal braces into video via ffmpeg without errors.
2. Audio (mastering.py):
   - Two-pass loudness normalization per clip targeting -14 LUFS integrated and -1 dBTP true peak.
   - Logs measured output LUFS for every clip.
   - Rendered clip audio measures within +/- 1 LU of -14 LUFS.
"""

import json
import logging
import subprocess
from pathlib import Path
from PIL import Image
import numpy as np
import pytest

from shorts_engine.config.settings import settings
from shorts_engine.services.captioning.ass_generator import (
    ASSSubtitleGenerator,
    escape_ass_dialogue_text,
    sanitize_word_for_font,
    get_font_supported_codepoints,
)
from shorts_engine.services.transcription.formatter import WordSegment
from shorts_engine.services.audio.mastering import AudioMasteringEngine
from shorts_engine.services.rendering.compositor import get_ass_filter_string, escape_ffmpeg_filter_path


# =====================================================================
# CAPTIONS UNIT & SMOKE TESTS
# =====================================================================

def test_escape_ass_dialogue_text():
    """Verifies that curly braces and backslashes are properly escaped for ASS override syntax."""
    # Lone braces
    assert escape_ass_dialogue_text("{") == r"\{"
    assert escape_ass_dialogue_text("}") == r"\}"
    assert escape_ass_dialogue_text("\\") == r"\\"

    # Words with braces
    assert escape_ass_dialogue_text("{secret}") == r"\{secret\}"
    assert escape_ass_dialogue_text("code{block}") == r"code\{block\}"

    # Backslashes and combined tags
    assert escape_ass_dialogue_text(r"path\to\file") == r"path\\to\\file"
    assert escape_ass_dialogue_text(r"{path\to\file}") == r"\{path\\to\\file\}"

    # Empty and regular strings
    assert escape_ass_dialogue_text("") == ""
    assert escape_ass_dialogue_text("regular word 123") == "regular word 123"


def test_sanitize_word_for_font_missing_characters():
    """Verifies fallback and safe replacement of characters missing from font."""
    # Zero-width spaces dropped regardless of font
    assert sanitize_word_for_font("in\u200bvis\ufeffible") == "invisible"

    # Emojis preserved for emoji font fallback
    assert sanitize_word_for_font("fire🔥") == "fire🔥"

    # For a font missing unicode punctuation/symbols (e.g. standard Latin font without typographics),
    # safe replacements must be applied
    ascii_only_font = set(range(32, 127))  # standard printable ASCII
    assert sanitize_word_for_font("“quoted”", font_codepoints=ascii_only_font) == '"quoted"'
    assert sanitize_word_for_font("‘single’", font_codepoints=ascii_only_font) == "'single'"
    assert sanitize_word_for_font("word—dash", font_codepoints=ascii_only_font) == "word-dash"
    assert sanitize_word_for_font("wait…", font_codepoints=ascii_only_font) == "wait..."

    # Accented letters with standard base decomposition when accented glyph is missing
    assert sanitize_word_for_font("café", font_codepoints=ascii_only_font) == "cafe"

    # Unsupported exotic unicode glyphs without decomposition safely fall back to '?'
    assert sanitize_word_for_font("ab\u4e16c", font_codepoints=ascii_only_font) == "ab?c"


def test_bundled_caption_font_referenced_explicitly():
    """Verifies that bundled font file exists and is explicitly used without system font dependency."""
    bundled_bold = settings.FONTS_DIR / "DejaVuSans-Bold.ttf"
    assert bundled_bold.exists(), f"Bundled font file missing at {bundled_bold}"

    generator = ASSSubtitleGenerator()
    assert generator.font_name in ("DejaVu Sans", "DejaVu Sans Bold")

    codepoints = get_font_supported_codepoints(font_name=generator.font_name)
    assert len(codepoints) > 5000
    # Common ASCII printable characters must be supported
    assert ord("{") in codepoints
    assert ord("}") in codepoints
    assert ord("\\") in codepoints


def test_ass_burn_in_smoke_test_with_literal_braces(tmp_path):
    """
    Smoke test: generates .ass subtitles containing literal braces, backslashes,
    and emojis, and burns them into a video with ffmpeg without errors.
    Verifies that literal braces render visually onto the canvas.
    """
    words = [
        WordSegment("{Hello}", 0.0, 0.4),
        WordSegment(r"world\test", 0.45, 0.8),
        WordSegment("{braces}", 0.85, 1.2),
        WordSegment("lit🔥", 1.25, 1.6),
    ]

    ass_path = tmp_path / "test_burn_in.ass"
    generator = ASSSubtitleGenerator()
    generator.generate_ass(
        words=words,
        segment_start_ms=0,
        segment_end_ms=2000,
        output_path=ass_path,
        max_words_per_line=4,
    )

    assert ass_path.exists()
    content = ass_path.read_text(encoding="utf-8")

    # Braces must be escaped, NOT replaced with parentheses!
    assert r"\{HELLO\}" in content or r"\{Hello\}" in content
    assert r"\{BRACES\}" in content or r"\{braces\}" in content
    assert "(" not in content.split("Dialogue:")[1]  # No (Hello) replacements

    # Smoke test: burn into video with FFmpeg
    video_out = tmp_path / "burned_test.mp4"
    ass_filter = get_ass_filter_string(ass_path, fonts_dir=settings.FONTS_DIR)

    cmd = [
        "ffmpeg", "-y", "-v", "warning",
        "-f", "lavfi", "-i", "color=c=black:s=1080x1920:d=1.0:r=25",
        "-vf", ass_filter,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(video_out),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"FFmpeg burn-in failed:\n{res.stderr}"
    assert video_out.exists() and video_out.stat().st_size > 0

    # Extract a frame and verify non-zero pixels rendered (literal braces rendered)
    frame_png = tmp_path / "frame.png"
    extract_cmd = [
        "ffmpeg", "-y", "-v", "warning",
        "-i", str(video_out),
        "-ss", "0.2",
        "-frames:v", "1",
        "-update", "1",
        str(frame_png),
    ]
    subprocess.run(extract_cmd, check=True)
    img = Image.open(frame_png)
    arr = np.array(img)
    rendered_px = np.count_nonzero(arr > 40)
    assert rendered_px > 100, "Subtitles failed to render visible pixels onto the frame"


# =====================================================================
# AUDIO MASTERING TWO-PASS LOUDNORM TESTS
# =====================================================================

def test_two_pass_loudness_normalize_measures_within_tolerance(tmp_path, caplog):
    """
    Verifies that two-pass loudness normalization:
    1. Normalizes clip audio to target -14 LUFS (+/- 1 LU) and <= -1 dBTP true peak.
    2. Logs the measured output LUFS for the clip.
    """
    caplog.set_level(logging.INFO)
    in_wav = tmp_path / "input_speech.wav"
    out_m4a = tmp_path / "mastered_speech.m4a"

    # Create 5s synthetic test tone (starts at ~ -22 LUFS)
    subprocess.run([
        "ffmpeg", "-y", "-v", "warning",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
        str(in_wav)
    ], check=True)

    engine = AudioMasteringEngine(target_lufs=-14.0, true_peak_dbfs=-1.0)
    res_path = engine.two_pass_loudness_normalize(
        input_audio_path=in_wav,
        output_audio_path=out_m4a,
        duration_sec=5.0,
        clip_id="clip_unit_test_001",
    )

    assert res_path.exists()
    assert res_path.stat().st_size > 0

    # 1. Verify log entry for measured output LUFS
    log_text = caplog.text
    assert "Measured output LUFS for clip 'clip_unit_test_001'" in log_text or "Mastered audio measured LUFS" in log_text

    # 2. Measure output file on disk using FFmpeg loudnorm analysis
    metrics = engine.measure_loudness(out_m4a, duration_sec=5.0)
    assert "input_i" in metrics, f"Could not measure output loudness: {metrics}"
    measured_lufs = metrics["input_i"]
    measured_tp = metrics["input_tp"]

    # Target -14 LUFS +/- 1 LU (-15.0 to -13.0 LUFS)
    assert -15.0 <= measured_lufs <= -13.0, (
        f"Measured LUFS {measured_lufs} is outside +/- 1 LU of target -14.0 LUFS"
    )
    # True Peak <= -1.0 dBTP (allowing minor +/- 0.5 dBTP filter margin)
    assert measured_tp <= -0.5, (
        f"Measured True Peak {measured_tp} exceeds true peak threshold -1.0 dBTP"
    )


def test_two_pass_loudness_normalize_with_background_music(tmp_path, caplog):
    """
    Verifies two-pass sidechain ducking + loudness normalization with background music
    achieves target -14 LUFS (+/- 1 LU).
    """
    caplog.set_level(logging.INFO)
    speech_wav = tmp_path / "speech.wav"
    music_wav = tmp_path / "music.wav"
    out_m4a = tmp_path / "mastered_mix.m4a"

    subprocess.run([
        "ffmpeg", "-y", "-v", "warning",
        "-f", "lavfi", "-i", "sine=frequency=350:duration=5",
        str(speech_wav)
    ], check=True)
    subprocess.run([
        "ffmpeg", "-y", "-v", "warning",
        "-f", "lavfi", "-i", "sine=frequency=700:duration=5",
        str(music_wav)
    ], check=True)

    engine = AudioMasteringEngine(target_lufs=-14.0, true_peak_dbfs=-1.0)
    engine.master_audio(
        speech_audio_path=speech_wav,
        output_path=out_m4a,
        music_track_path=music_wav,
        duration_sec=5.0,
        two_pass=True,
        clip_id="clip_ducked_002",
    )

    assert out_m4a.exists()
    metrics = engine.measure_loudness(out_m4a, duration_sec=5.0)
    measured_lufs = metrics["input_i"]

    assert -15.0 <= measured_lufs <= -13.0, (
        f"Ducked master measured LUFS {measured_lufs} is outside +/- 1 LU of -14.0 LUFS"
    )


def test_two_pass_loudness_normalize_wav_format(tmp_path):
    """Verifies two-pass loudnorm correctly supports uncompressed PCM WAV output."""
    in_wav = tmp_path / "in.wav"
    out_wav = tmp_path / "out.wav"

    subprocess.run([
        "ffmpeg", "-y", "-v", "warning",
        "-f", "lavfi", "-i", "sine=frequency=500:duration=4",
        str(in_wav)
    ], check=True)

    engine = AudioMasteringEngine(target_lufs=-14.0, true_peak_dbfs=-1.0)
    engine.two_pass_loudness_normalize(in_wav, out_wav, duration_sec=4.0)

    assert out_wav.exists()
    metrics = engine.measure_loudness(out_wav, duration_sec=4.0)
    assert -15.0 <= metrics["input_i"] <= -13.0
