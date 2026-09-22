"""
Unit tests for AI clip selection, boundary snapping, and heuristic fallback.
"""

from shorts_engine.services.transcription.formatter import WordSegment, FormattedTranscript
from shorts_engine.services.clip_selection.boundaries import BoundarySnapper
from shorts_engine.services.clip_selection.selector import ClipSelectionService


def test_boundary_snapper_snaps_to_sentences():
    # Build 40 seconds of words with a clear sentence ending
    words = []
    t = 0.0
    for i in range(100):
        punct = "." if i in [20, 50, 80] else ""
        word = f"word{i}{punct}"
        words.append(WordSegment(word, t, t + 0.35))
        t += 0.40

    transcript = FormattedTranscript(words=words)
    snapper = BoundarySnapper(min_duration_sec=15.0, max_duration_sec=30.0)

    start_ms, end_ms, text = snapper.snap_boundaries(raw_start_sec=5.0, raw_end_sec=25.0, transcript=transcript)

    duration_sec = (end_ms - start_ms) / 1000.0
    assert 15.0 <= duration_sec <= 30.0
    # Last word should end with period or be a clean sentence end
    assert text.endswith(".")


def test_clip_selector_heuristic_fallback():
    # Generate a 120s transcript
    words = []
    t = 0.0
    for i in range(250):
        punct = "." if i % 15 == 0 else ""
        words.append(WordSegment(f"word{i}{punct}", t, t + 0.4))
        t += 0.48

    transcript = FormattedTranscript(words=words)
    service = ClipSelectionService()  # No API key -> triggers heuristic fallback

    clips = service.select_clips(transcript, min_clips=3, max_clips=5)
    assert 3 <= len(clips) <= 5

    for clip in clips:
        assert 15.0 <= clip.duration_sec <= 58.0
        assert 0.0 <= clip.score <= 1.0
        assert len(clip.suggested_title) <= 100
        assert len(clip.suggested_description) <= 5000
        assert len(clip.suggested_tags) >= 1
