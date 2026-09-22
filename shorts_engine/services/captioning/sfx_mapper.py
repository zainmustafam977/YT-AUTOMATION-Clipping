"""
Contextual SFX Mapper: maps keywords, rhetorical reveals, and emotional transitions
to audio cue tags across 8 expanded commercial sound categories.
Includes procedural audio fallback synthesis and loud alerting when physical asset files are missing.
"""

import math
import struct
import wave
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from shorts_engine.config.settings import settings
from shorts_engine.services.transcription.formatter import WordSegment

logger = logging.getLogger(__name__)

KEYWORD_SFX_MAP = {
    # 1. High-impact transitions
    "whoosh": ["suddenly", "however", "next", "change", "switch", "turned", "meanwhile", "fast", "speed"],
    # 2. Heavy impact / punchlines / stops
    "impact": ["boom", "crash", "stop", "never", "dead", "power", "smash", "hit"],
    # 3. Revelations / Key insights
    "ding": ["secret", "remember", "key", "tip", "rule", "truth", "solution", "hack"],
    # 4. Surprise / Quick highlights
    "pop": ["surprise", "quick", "look", "watch", "check", "notice"],
    # 5. Visual snapshots / Evidence / Examples
    "camera_click": ["picture", "photo", "see", "shown", "proof", "example"],
    # 6. Errors / Failures / Confusion
    "glitch": ["wrong", "mistake", "error", "broke", "glitch", "fail", "lost"],
    # 7. Cinematic gravity / Deep tension
    "sub_bass_drop": ["warning", "danger", "critical", "insane", "crazy", "huge", "shocking"],
    # 8. Anticipation / Build-up
    "riser": ["before", "until", "wait", "finally", "ready", "almost"],
    # Legacy compatibility
    "reveal": ["actually", "realized", "discovered", "unbelievable", "reveal", "revealed"],
}

# Alert metrics tracking missing physical asset requests
SFX_MISSING_ASSET_ALERT_COUNT = 0


class SoundCue:
    def __init__(self, tag: str, offset_sec: float, keyword: str):
        self.tag = tag
        self.offset_sec = round(offset_sec, 2)
        self.keyword = keyword

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tag": self.tag,
            "offset_sec": self.offset_sec,
            "keyword": self.keyword,
        }

    def __repr__(self) -> str:
        return f"<SoundCue tag={self.tag} at {self.offset_sec}s for '{self.keyword}'>"


def generate_synthetic_sfx(
    tag: str,
    duration_sec: float = 0.3,
    sample_rate: int = 44100,
    output_dir: Optional[Path] = None,
) -> Path:
    """
    Synthesizes a clean 16-bit mono 44.1kHz PCM WAV file in memory with category-specific
    frequency shaping and exponential decay. Guarantees valid audio playback even without physical asset files.
    """
    out_dir = output_dir or (settings.TEMP_DIR / "sfx")
    out_dir.mkdir(parents=True, exist_ok=True)
    output_file = out_dir / f"synthetic_{tag}.wav"

    num_samples = int(duration_sec * sample_rate)
    frames = bytearray()

    # Category frequency characteristics (start_freq, end_freq)
    freq_map = {
        "whoosh": (400, 100),
        "impact": (120, 40),
        "ding": (1800, 1800),
        "pop": (800, 300),
        "camera_click": (2400, 1200),
        "glitch": (600, 900),
        "sub_bass_drop": (90, 30),
        "riser": (200, 800),
        "reveal": (500, 1000),
    }
    start_f, end_f = freq_map.get(tag, (440, 440))

    for i in range(num_samples):
        t = i / sample_rate
        progress = i / max(1, num_samples)
        freq = start_f + (end_f - start_f) * progress
        # Exponential decay envelope
        envelope = math.exp(-3.0 * progress)
        sample = int(32767.0 * 0.4 * envelope * math.sin(2.0 * math.pi * freq * t))
        frames.extend(struct.pack("<h", max(-32768, min(32767, sample))))

    with wave.open(str(output_file), "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(frames)

    return output_file


def resolve_sfx_path(tag: str, assets_dir: Optional[Path] = None) -> Path:
    """
    Resolves the audio file for an SFX tag. If physical asset is missing on disk,
    emits a loud logger.warning(), increments the alert metric, and generates a procedural WAV.
    """
    global SFX_MISSING_ASSET_ALERT_COUNT
    base_assets = assets_dir or (settings.BASE_DIR / "assets" / "sfx")

    for ext in (".wav", ".mp3", ".ogg"):
        cand = base_assets / f"{tag}{ext}"
        if cand.exists():
            return cand

    # Asset is missing -> Loud alert
    SFX_MISSING_ASSET_ALERT_COUNT += 1
    logger.warning(
        f"LOUD ALERT: SFX physical asset for '{tag}' not found in {base_assets}. "
        f"Falling back to procedurally synthesized WAV audio. (Total missing alerts: {SFX_MISSING_ASSET_ALERT_COUNT})"
    )
    return generate_synthetic_sfx(tag)


class SFXMapper:
    def map_sfx_cues(
        self,
        words: List[WordSegment],
        segment_start_sec: float,
        min_interval_sec: float = 4.0,
    ) -> List[SoundCue]:
        """
        Maps transcript keywords to SFX cues with interval spacing to avoid audio clutter.
        """
        cues: List[SoundCue] = []
        last_cue_time = -10.0

        for w in words:
            clean_word = w.word.lower().strip(".,!?;:\"'")
            offset = max(0.0, w.start - segment_start_sec)

            # Check interval limit
            if offset - last_cue_time < min_interval_sec:
                continue

            matched_tag = None
            for tag, keywords in KEYWORD_SFX_MAP.items():
                if clean_word in keywords:
                    matched_tag = tag
                    break

            if matched_tag:
                cues.append(SoundCue(tag=matched_tag, offset_sec=offset, keyword=clean_word))
                last_cue_time = offset

        return cues
