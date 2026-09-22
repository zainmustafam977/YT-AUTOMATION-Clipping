"""
Unit tests for transcription formatting, forced alignment, and silence detection.
"""

from pathlib import Path
from shorts_engine.services.transcription.formatter import WordSegment, FormattedTranscript
from shorts_engine.services.transcription.aligner import ForcedAligner


def test_word_segment_and_silence_detection():
    words = [
        WordSegment("Welcome", 0.0, 0.4),
        WordSegment("to", 0.45, 0.6),
        WordSegment("the", 0.65, 0.8),
        WordSegment("show.", 0.85, 1.2),
        # 0.5s pause
        WordSegment("Today", 1.7, 2.1),
        WordSegment("we", 2.15, 2.3),
        WordSegment("discuss", 2.35, 2.8),
        WordSegment("AI.", 2.85, 3.1),
    ]

    transcript = FormattedTranscript(words=words, language="en")
    assert transcript.full_text == "Welcome to the show. Today we discuss AI."

    silences = transcript.find_silences(min_silence_sec=0.3)
    assert len(silences) == 1
    assert silences[0]["after_word"] == "show."
    assert silences[0]["before_word"] == "Today"
    assert round(silences[0]["duration"], 2) == 0.5


def test_forced_aligner_eliminates_overlap_and_drift():
    aligner = ForcedAligner(device="cpu")
    # Simulate overlapping / drifting raw whisper outputs
    drifting_words = [
        WordSegment("First", 1.0, 1.6),
        WordSegment("Second", 1.5, 2.0),  # Overlaps previous word
        WordSegment("Third", 1.9, 2.4),   # Overlaps previous word
    ]

    aligned = aligner.align(Path("dummy.wav"), drifting_words)
    assert len(aligned) == 3

    # Check strictly monotonic non-overlapping
    assert aligned[0].end <= aligned[1].start
    assert aligned[1].end <= aligned[2].start
    for w in aligned:
        assert w.end > w.start
        assert (w.end - w.start) >= 0.05
