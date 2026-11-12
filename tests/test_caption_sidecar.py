from shorts_engine.services.captioning.sidecar import generate_srt, generate_vtt
from shorts_engine.services.transcription.formatter import WordSegment


def _words():
    return [
        WordSegment("hello", 0.0, 0.5),
        WordSegment("world", 0.5, 1.0),
        WordSegment("again", 1.2, 1.6),
    ]


def test_srt_contains_timestamps_and_text(tmp_path):
    out = generate_srt(_words(), tmp_path / "captions.srt")
    content = out.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:01,600" in content
    assert "hello world again" in content


def test_vtt_has_header_and_dot_separator(tmp_path):
    out = generate_vtt(_words(), tmp_path / "captions.vtt")
    content = out.read_text(encoding="utf-8")
    assert content.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:01.600" in content


def test_empty_words_produce_empty_files(tmp_path):
    srt = generate_srt([], tmp_path / "empty.srt")
    vtt = generate_vtt([], tmp_path / "empty.vtt")
    assert srt.read_text(encoding="utf-8") == ""
    assert vtt.read_text(encoding="utf-8").strip() == "WEBVTT"
