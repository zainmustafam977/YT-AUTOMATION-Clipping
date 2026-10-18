"""R4-1.3 — Clip-folder open endpoint + service tests."""
import os
import platform
import sys
import pytest

from shorts_engine.services.clip_folder import (
    reveal_command, run_reveal, describe_reveal,
)


@pytest.fixture
def tmp_video(tmp_path):
    """Create a small fake video file in tmp_path."""
    p = tmp_path / "clip_42.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100)
    return str(p)


def test_reveal_command_missing_returns_missing():
    kind, argv = reveal_command("/nonexistent/file.mp4")
    assert kind == "missing"
    assert argv == []


def test_reveal_command_empty_returns_unsupported():
    kind, argv = reveal_command("")
    assert kind == "unsupported"


def test_reveal_command_existing_file(tmp_video):
    kind, argv = reveal_command(tmp_video)
    assert kind in ("windows_explorer_select", "macos_finder_reveal", "linux_xdg_open")
    assert len(argv) >= 1
    # The file path or its parent must be in argv.
    if kind == "linux_xdg_open":
        # xdg-open takes the parent directory.
        assert any(os.path.dirname(tmp_video) in a for a in argv)
    else:
        assert any(tmp_video in a for a in argv)


def test_reveal_command_existing_dir(tmp_path):
    d = tmp_path / "clips"
    d.mkdir()
    kind, argv = reveal_command(str(d))
    assert kind != "missing"


def test_reveal_command_windows_select_uses_comma_syntax(tmp_video, monkeypatch):
    """On Windows, the command is explorer.exe /select,<path>."""
    monkeypatch.setattr("platform.system", lambda: "Windows")
    kind, argv = reveal_command(tmp_video)
    assert kind == "windows_explorer_select"
    assert argv[0] == "explorer.exe"
    assert argv[1].startswith("/select,")
    assert tmp_video in argv[1]


def test_reveal_command_macos_uses_open_r(tmp_video, monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    kind, argv = reveal_command(tmp_video)
    assert kind == "macos_finder_reveal"
    assert argv[0] == "open"
    assert argv[1] == "-R"
    assert tmp_video in argv[2]


def test_reveal_command_linux_uses_xdg_open_with_parent(tmp_video, monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    kind, argv = reveal_command(tmp_video)
    assert kind == "linux_xdg_open"
    assert argv[0] == "xdg-open"
    # The parent dir, not the file.
    assert argv[1] != tmp_video
    assert os.path.dirname(tmp_video) in argv[1]


def test_run_reveal_returns_tuple_for_missing():
    ok, detail = run_reveal("/nonexistent/foo.mp4")
    assert ok is False
    assert "missing" in detail


def test_run_reveal_empty_path():
    ok, detail = run_reveal("")
    assert ok is False


def test_describe_reveal_shape(tmp_video):
    desc = describe_reveal(tmp_video)
    assert "ok" in desc
    assert "kind" in desc
    assert "argv" in desc
    assert "argv_quoted" in desc
    assert "file_path" in desc
    assert "warnings" in desc


def test_describe_reveal_missing():
    desc = describe_reveal("/no/such/file.mp4")
    assert desc["ok"] is False
    assert "not found" in desc["reason"]


def test_describe_reveal_unicode_path(tmp_path):
    """Non-ASCII file names are supported."""
    p = tmp_path / "видео_клип.mp4"
    p.write_bytes(b"\x00")
    desc = describe_reveal(str(p))
    assert desc["ok"] is True
    # The file_path is preserved.
    assert "видео_клип" in desc["file_path"]


def test_describe_reveal_long_path(tmp_path):
    """Long paths should surface in the warnings list."""
    long_name = "a" * 250 + ".mp4"
    p = tmp_path / long_name
    p.write_bytes(b"\x00")
    desc = describe_reveal(str(p))
    assert any("MAX_PATH" in w or "long" in w.lower() for w in desc["warnings"])


def test_describe_reveal_quoted_argv(tmp_video):
    desc = describe_reveal(tmp_video)
    # argv_quoted must be present and equal length to argv.
    assert len(desc["argv_quoted"]) == len(desc["argv"])
    # Quoted args should be safe to embed in a shell command.
    for q in desc["argv_quoted"]:
        # The shlex.quote result for a safe path should not contain
        # unescaped backticks or $() which would allow code injection.
        assert "`" not in q
        assert "$(" not in q
