"""
Unit tests for captioning, ASS subtitle generation, contrast sampling, and SFX cues.
"""

from pathlib import Path
import numpy as np
from shorts_engine.services.transcription.formatter import WordSegment
from shorts_engine.services.captioning.ass_generator import ASSSubtitleGenerator, format_ass_timestamp
from shorts_engine.services.captioning.contrast_sampler import ContrastSampler
from shorts_engine.services.captioning.sfx_mapper import SFXMapper


def test_format_ass_timestamp():
    assert format_ass_timestamp(0.0) == "0:00:00.00"
    assert format_ass_timestamp(1.25) == "0:00:01.25"
    assert format_ass_timestamp(65.5) == "0:01:05.50"


def test_ass_generator_creates_valid_karaoke_dialogue(tmp_path):
    words = [
        WordSegment("The", 10.0, 10.3),
        WordSegment("secret", 10.35, 10.8),
        WordSegment("is", 10.85, 11.0),
        WordSegment("revealed.", 11.05, 11.5),
    ]

    generator = ASSSubtitleGenerator()
    out_file = tmp_path / "test.ass"
    generator.generate_ass(words, segment_start_ms=10000, segment_end_ms=12000, output_path=out_file)

    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "[Script Info]" in content
    assert "Style: ShortsStyle" in content
    assert "Dialogue:" in content
    assert "{\\c&H0000FFFF&}The{\\c&H00FFFFFF&}" in content
    assert "{\\c&H0000FFFF&}secret{\\c&H00FFFFFF&}" in content


def test_contrast_sampler_on_synthetic_frames():
    sampler = ContrastSampler()
    
    # Create pure white frame (luminance 255)
    white_frame = np.full((1920, 1080, 3), 255, dtype=np.uint8)
    lum_white = sampler.sample_background_luminance(white_frame)
    assert lum_white > 250.0
    style_white = sampler.compute_style_adjustments(lum_white)
    assert style_white["outline_width"] >= 4.0

    # Create dark frame (luminance 20)
    dark_frame = np.full((1920, 1080, 3), 20, dtype=np.uint8)
    lum_dark = sampler.sample_background_luminance(dark_frame)
    assert lum_dark < 30.0


def test_sfx_mapper_detects_keywords_and_enforces_interval():
    mapper = SFXMapper()
    words = [
        WordSegment("Suddenly", 1.0, 1.4),   # whoosh
        WordSegment("the", 1.5, 1.7),
        WordSegment("secret", 1.8, 2.2),    # ding - but occurs 0.8s later (< 4s interval) -> skipped
        WordSegment("was", 2.3, 2.5),
        WordSegment("revealed", 6.0, 6.5),  # reveal - occurs 5s later (>= 4s interval) -> included
    ]

    cues = mapper.map_sfx_cues(words, segment_start_sec=0.0, min_interval_sec=4.0)
    assert len(cues) == 2
    assert cues[0].tag == "whoosh"
    assert cues[1].tag == "reveal"
