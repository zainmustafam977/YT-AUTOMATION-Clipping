"""
Tests for expanded 8-category SFX library, procedural audio synthesis fallback, and loud alerting on missing assets.
"""

import wave
import logging

from shorts_engine.services.transcription.formatter import WordSegment
from shorts_engine.services.captioning.sfx_mapper import (
    SFXMapper,
    KEYWORD_SFX_MAP,
    generate_synthetic_sfx,
    resolve_sfx_path,
)


def test_sfx_eight_categories_keyword_mapping():
    """Verifies that all 8 categories exist and map keywords to sound cues."""
    mapper = SFXMapper()
    assert "whoosh" in KEYWORD_SFX_MAP
    assert "impact" in KEYWORD_SFX_MAP
    assert "ding" in KEYWORD_SFX_MAP
    assert "pop" in KEYWORD_SFX_MAP
    assert "camera_click" in KEYWORD_SFX_MAP
    assert "glitch" in KEYWORD_SFX_MAP
    assert "sub_bass_drop" in KEYWORD_SFX_MAP
    assert "riser" in KEYWORD_SFX_MAP

    words = [
        WordSegment("Suddenly", 0.0, 0.5),      # whoosh
        WordSegment("the", 1.0, 1.2),
        WordSegment("glitch", 5.0, 5.5),        # glitch (after 4s interval)
        WordSegment("was", 6.0, 6.2),
        WordSegment("warning", 10.0, 10.5),     # sub_bass_drop
    ]
    cues = mapper.map_sfx_cues(words, segment_start_sec=0.0, min_interval_sec=4.0)
    tags = [c.tag for c in cues]
    assert tags == ["whoosh", "glitch", "sub_bass_drop"]


def test_procedural_synthetic_audio_generation(tmp_path):
    """Verifies that procedural audio generator outputs a valid 16-bit PCM WAV file."""
    for tag in ("whoosh", "impact", "ding", "pop", "camera_click", "glitch", "sub_bass_drop", "riser"):
        wav_file = generate_synthetic_sfx(tag=tag, duration_sec=0.2, output_dir=tmp_path)
        assert wav_file.exists()
        assert wav_file.stat().st_size > 0

        # Validate with stdlib wave parser
        with wave.open(str(wav_file), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2  # 16-bit
            assert wf.getframerate() == 44100
            assert wf.getnframes() > 0


def test_loud_alert_emitted_when_asset_missing(tmp_path, caplog):
    """Verifies that missing physical audio files emit a loud warning and track metrics."""
    empty_assets = tmp_path / "empty_assets"
    empty_assets.mkdir(parents=True, exist_ok=True)

    with caplog.at_level(logging.WARNING):
        sfx_path = resolve_sfx_path("impact", assets_dir=empty_assets)

    assert sfx_path.exists()  # Synthesized fallback returned
    assert any("LOUD ALERT" in record.message for record in caplog.records)
    assert any("impact" in record.message for record in caplog.records)


def test_existing_physical_asset_resolved_without_loud_alert(tmp_path, caplog):
    """Verifies that existing assets on disk are returned directly without synthetic fallback or alert."""
    assets_dir = tmp_path / "existing_assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    real_file = assets_dir / "ding.wav"
    real_file.write_bytes(b"RIFF_FAKE_WAV_BYTES")

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        sfx_path = resolve_sfx_path("ding", assets_dir=assets_dir)

    assert sfx_path == real_file
    assert not any("LOUD ALERT" in record.message for record in caplog.records)
