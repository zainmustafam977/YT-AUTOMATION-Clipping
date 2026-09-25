"""
Test reproducing and verifying P0-03: Resumed Pipeline Transcript Normalization.
Verifies that FormattedTranscript.from_dict strictly normalizes both dicts and legacy lists
into typed WordSegment objects citing exact fields without silent 0.0 defaults,
and supports both object-like (.word) and dict-like (['word']) access.
"""

import json
import pytest
from shorts_engine.services.transcription.formatter import FormattedTranscript, WordSegment


def test_reproduce_p0_03_dict_attribute_error_in_eval_loop():
    """
    Demonstrates the exact crash in runner.py:678-681 when words are dicts
    instead of WordSegment objects.
    """
    raw_words = [
        {"word": "Never", "start": 0.0, "end": 0.5},
        {"word": "give", "start": 0.5, "end": 1.0},
        {"word": "up", "start": 1.0, "end": 1.5},
    ]

    class MockTranscriptWithDicts:
        words = raw_words

    transcript = MockTranscriptWithDicts()
    with pytest.raises(AttributeError, match="'dict' object has no attribute 'word'"):
        _ = " ".join([w.word for w in transcript.words])


def test_p0_03_normalization_from_legacy_list():
    """
    Verifies FormattedTranscript.from_dict handles a legacy flat list of word dictionaries.
    """
    legacy_list = [
        {"word": "Hello", "start": 0.5, "end": 1.0, "probability": 0.98},
        {"word": "world", "start": 1.0, "end": 1.5, "score": 0.95},
    ]

    ft = FormattedTranscript.from_dict(legacy_list)
    assert len(ft.words) == 2
    for w in ft.words:
        assert isinstance(w, WordSegment)
    assert ft.words[0].word == "Hello"
    assert ft.words[0].start == 0.5
    assert ft.words[0].end == 1.0
    assert ft.words[0].score == 0.98
    assert ft.words[1].score == 0.95


def test_p0_03_normalization_from_standard_dict():
    """
    Verifies FormattedTranscript.from_dict handles standard dictionary with 'words' key.
    """
    std_dict = {
        "language": "en",
        "full_text": "Hello world",
        "words": [
            {"word": "Hello", "start": 0.5, "end": 1.0},
            {"word": "world", "start": 1.0, "end": 1.5},
        ],
    }

    ft = FormattedTranscript.from_dict(std_dict)
    assert len(ft.words) == 2
    assert ft.language == "en"
    assert ft.full_text == "Hello world"
    assert ft.words[0].word == "Hello"


def test_p0_03_dual_access_contract():
    """
    Verifies that WordSegment supports both object-like (.word) and dict-like (['word'], .get()) access.
    """
    seg = WordSegment(word="test", start=1.5, end=2.5, probability=0.9, score=0.9)

    # Object-like access
    assert seg.word == "test"
    assert seg.start == 1.5
    assert seg.end == 2.5
    assert seg.probability == 0.9
    assert seg.score == 0.9

    # Dict-like access
    assert seg["word"] == "test"
    assert seg["start"] == 1.5
    assert seg["end"] == 2.5
    assert seg.get("word") == "test"
    assert seg.get("non_existent", "default") == "default"


def test_p0_03_rejects_missing_or_invalid_fields():
    """
    Verifies that WordSegment.from_dict raises ValueError or KeyError when required fields are missing
    or invalid, rather than silently defaulting start/end to 0.0.
    """
    # Missing 'word'
    with pytest.raises(KeyError):
        WordSegment.from_dict({"start": 0.0, "end": 1.0})

    # Missing 'start'
    with pytest.raises(KeyError):
        WordSegment.from_dict({"word": "hello", "end": 1.0})

    # Non-numeric 'start'
    with pytest.raises(ValueError):
        WordSegment.from_dict({"word": "hello", "start": "invalid_number", "end": 1.0})


def test_p0_03_skips_malformed_words_in_transcript():
    """
    Verifies that FormattedTranscript.from_dict logs and skips individual malformed words
    while preserving valid word entries.
    """
    mixed_data = {
        "language": "en",
        "words": [
            {"word": "Valid", "start": 0.0, "end": 0.5},
            {"word": "Malformed_no_start", "end": 1.0},
            {"word": "Malformed_bad_number", "start": "bad", "end": 1.5},
            {"word": "ValidAgain", "start": 1.5, "end": 2.0},
        ],
    }
    ft = FormattedTranscript.from_dict(mixed_data)
    assert len(ft.words) == 2
    assert ft.words[0].word == "Valid"
    assert ft.words[1].word == "ValidAgain"


def test_p0_03_raises_on_all_malformed_words():
    """
    Verifies fail-fast ValueError when transcript contains words but all of them are malformed.
    """
    all_bad = {
        "words": [
            {"word": "Bad1"},
            {"start": 1.0},
        ],
    }
    with pytest.raises(ValueError, match="zero valid words recovered"):
        FormattedTranscript.from_dict(all_bad)

