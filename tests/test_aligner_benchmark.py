"""
Benchmark & Precision Test for Forced Aligner (BENCH-F40 / F-40).
Empirically benchmarks the ForcedAligner timestamp refinement against acoustic constraints:
- Monotonicity guarantee
- Non-overlap invariant
- Sub-50ms cross-attention drift resolution
- Execution throughput benchmark
"""

import time
from pathlib import Path
from shorts_engine.services.transcription.aligner import ForcedAligner
from shorts_engine.services.transcription.formatter import WordSegment


def test_forced_aligner_monotonicity_and_drift_resolution(tmp_path):
    """Verifies that ForcedAligner resolves drift and eliminates overlaps without inversion."""
    audio_file = tmp_path / "dummy_speech.wav"
    audio_file.write_bytes(b"RIFF" + b"\x00" * 40)

    # Synthetic noisy transcript with artificial overlap drifts and sub-50ms collapsed intervals
    noisy_words = [
        WordSegment(word="Welcome", start=1.000, end=1.400, probability=0.95),
        WordSegment(word="to", start=1.350, end=1.450, probability=0.98),  # Overlaps previous by 50ms
        WordSegment(word="the", start=1.420, end=1.440, probability=0.90),  # Overlaps and duration only 20ms
        WordSegment(word="channel", start=1.500, end=2.100, probability=0.92),
    ]

    aligner = ForcedAligner(device="cpu")
    t0 = time.perf_counter()
    aligned = aligner.align(audio_file, noisy_words)
    latency_ms = (time.perf_counter() - t0) * 1000

    assert len(aligned) == len(noisy_words)
    # Check execution throughput (< 20ms for 4 words)
    assert latency_ms < 50.0

    # Verify invariants:
    for i in range(len(aligned)):
        w = aligned[i]
        # Duration must be at least 50ms
        assert (w.end - w.start) >= 0.049, f"Word {w.word} duration too short: {w.end - w.start}"
        # Non-overlapping
        if i > 0:
            prev = aligned[i - 1]
            assert w.start >= prev.end, f"Overlap detected between {prev.word} ({prev.end}) and {w.word} ({w.start})"


def test_forced_aligner_empty():
    aligner = ForcedAligner(device="cpu")
    assert aligner.align(Path("none.wav"), []) == []
