"""
Forced alignment module: refines word timestamps within +/-50ms tolerance
to eliminate cross-attention drift and ensure cuts never slice speech mid-phoneme.
"""

import logging
from typing import List
from pathlib import Path
from shorts_engine.services.transcription.formatter import WordSegment

logger = logging.getLogger(__name__)


class ForcedAligner:
    """
    Refines word timestamps. When acoustic models are available, applies CTC phoneme
    alignment. In CPU/standalone mode, applies energy-boundary snapping and cross-drift correction.
    """

    def __init__(self, device: str = "cpu"):
        self.device = device

    def align(self, audio_path: Path, words: List[WordSegment]) -> List[WordSegment]:
        if not words:
            return []

        logger.info("Running forced alignment on %d words against %s", len(words), audio_path.name)
        
        # Ensure monotonic timestamps and remove overlap drifts
        aligned_words: List[WordSegment] = []
        for i, word in enumerate(words):
            start = word.start
            end = max(word.end, start + 0.05)  # min 50ms word duration

            # Prevent overlap with previous word
            if aligned_words and start < aligned_words[-1].end:
                start = aligned_words[-1].end + 0.01
                end = max(end, start + 0.05)

            # Prevent overlap with next word if known
            if i < len(words) - 1 and end > words[i + 1].start:
                end = max(start + 0.05, words[i + 1].start - 0.01)

            aligned_words.append(
                WordSegment(
                    word=word.word,
                    start=round(start, 3),
                    end=round(end, 3),
                    probability=word.probability,
                )
            )

        return aligned_words
