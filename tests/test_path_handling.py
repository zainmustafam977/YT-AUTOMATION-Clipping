"""R4-1.2 — Path handling tests: drive letters, spaces, long paths, non-ASCII, ffmpeg escaping."""
import os
import sys
import pytest

from shorts_engine.services.paths import (
    escape_ffmpeg_path, escape_ass_path, ffmpeg_arg, ffmpeg_args,
    path_is_safe, path_warnings, _split_drive, enable_windows_long_paths,
)


# --- _split_drive -----------------------------------------------------------

def test_split_drive_windows_c():
    drive, rest = _split_drive("C:\\Users\\test\\file.png")
    assert drive == "C:"
    assert rest == "\\Users\\test\\file.png"


def test_split_drive_posix_unchanged():
    drive, rest = _split_drive("/home/user/file.png")
    assert drive == ""
    assert rest == "/home/user/file.png"


def test_split_drive_no_drive_letter():
    drive, rest = _split_drive("\\foo\\bar")
    assert drive == ""
    assert rest == "\\foo\\bar"


def test_split_drive_lowercase_drive():
    drive, rest = _split_drive("d:\\")
    assert drive == "d:"
    assert rest == "\\"


# --- escape_ffmpeg_path -----------------------------------------------------

def test_escape_preserves_drive_letter():
    """The C: of a drive letter must NOT be escaped — only the path colons are."""
    p = "C:\\Users\\Name\\file.png"
    out = escape_ffmpeg_path(p)
    assert out.startswith("C:"), f"drive letter should be preserved: {out!r}"
    # Backslashes converted to forward slashes for ffmpeg.
    assert "\\" not in out
    # No double-escaped colons (the drive colon is intact).
    assert "C\\:" not in out


def test_escape_posix_path():
    p = "/var/data/file with space.png"
    out = escape_ffmpeg_path(p)
    # Spaces inside ffmpeg filter are tricky but not strictly illegal; the
    # primary escaping is for backslash, colon, %, and special chars.
    assert "\\" not in out or "%" in out  # we don't expect a backslash at all
    assert "%" not in out or "%" in p  # the input had no %; output should have no unescaped %


def test_escape_percent_in_filename():
    p = "/tmp/100% done.mp4"
    out = escape_ffmpeg_path(p)
    assert "\\%" in out


def test_escape_quotes():
    p = "/tmp/it's a test.mp4"
    out = escape_ffmpeg_path(p)
    assert "\\'" in out


def test_escape_brackets():
    p = "/tmp/[bracketed].mp4"
    out = escape_ffmpeg_path(p)
    assert "\\[" in out
    assert "\\]" in out


def test_escape_parentheses():
    p = "/tmp/(copy).mp4"
    out = escape_ffmpeg_path(p)
    assert "\\(" in out
    assert "\\)" in out


def test_escape_semicolons():
    p = "/tmp/a;b.mp4"
    out = escape_ffmpeg_path(p)
    assert "\\;" in out


def test_escape_commas():
    p = "/tmp/a,b.mp4"
    out = escape_ffmpeg_path(p)
    assert "\\," in out


def test_escape_drive_letter_path_unchanged_drive():
    p = "D:\\renders\\final.mov"
    out = escape_ffmpeg_path(p)
    # Drive is preserved
    assert out.startswith("D:")


def test_escape_unicode_path():
    p = "/tmp/видео_файл.mp4"
    out = escape_ffmpeg_path(p)
    assert "видео" in out


# --- escape_ass_path --------------------------------------------------------

def test_ass_path_windows_separators():
    p = "C:\\Users\\test\\file.png"
    out = escape_ass_path(p)
    # ASS expects forward slashes
    assert "\\" not in out or "/" in out


def test_ass_path_escapes_braces():
    p = "/tmp/{weird}.ass"
    out = escape_ass_path(p)
    assert r"\{" in out
    assert r"\}" in out


# --- ffmpeg_arg / ffmpeg_args -----------------------------------------------

def test_ffmpeg_arg_plain():
    assert ffmpeg_arg("hello") == "hello"


def test_ffmpeg_arg_with_spaces():
    out = ffmpeg_arg("hello world")
    # Should be quoted.
    assert out.startswith('"') and out.endswith('"') or "'" in out


def test_ffmpeg_arg_special_chars():
    out = ffmpeg_arg("a$b;rm -rf")
    # Quoted because contains special chars.
    assert out != "a$b;rm -rf"


def test_ffmpeg_arg_already_safe():
    safe = "C:/Users/test/file.png"
    assert ffmpeg_arg(safe) == safe  # alphanumeric + safe punct


def test_ffmpeg_args_list():
    out = ffmpeg_args(["plain", "with space", "weird&chars"])
    assert len(out) == 3
    # First is plain; others are quoted.
    assert out[0] == "plain"
    assert out[1] != "with space"


# --- path_is_safe -----------------------------------------------------------

def test_path_safe_simple():
    ok, _ = path_is_safe("/tmp/file.mp4")
    assert ok is True


def test_path_unsafe_nul():
    ok, reason = path_is_safe("/tmp/\x00file.mp4")
    assert ok is False
    assert "NUL" in reason


def test_path_unsafe_reserved_chars():
    ok, reason = path_is_safe("/tmp/foo<bar.mp4")
    assert ok is False
    assert "reserved" in reason.lower()


def test_path_safe_unicode():
    ok, _ = path_is_safe("/tmp/файл.mp4")
    assert ok is True


# --- path_warnings ----------------------------------------------------------

def test_path_warnings_empty():
    assert len(path_warnings("")) > 0


def test_path_warnings_whitespace():
    assert any("empty" in w.lower() or "whitespace" in w.lower() for w in path_warnings("   "))


def test_path_warnings_long_path():
    long_p = "/" + "a" * 260
    ws = path_warnings(long_p)
    assert any("MAX_PATH" in w or "long" in w.lower() for w in ws)


def test_path_warnings_unicode():
    ws = path_warnings("/tmp/файл.mp4")
    assert any("non-ASCII" in w for w in ws)


def test_path_warnings_no_warnings():
    ws = path_warnings("/tmp/normal.mp4")
    # Short, no spaces (unless on Windows which it isn't in CI), no unicode.
    # May still have a "spaces" warning if Windows is detected via sys.platform.
    if sys.platform != "win32":
        assert ws == []


# --- enable_windows_long_paths ----------------------------------------------

def test_enable_windows_long_paths_noop_on_posix():
    if sys.platform != "win32":
        assert enable_windows_long_paths() is False


def test_enable_windows_long_paths_returns_bool():
    out = enable_windows_long_paths()
    assert isinstance(out, bool)
