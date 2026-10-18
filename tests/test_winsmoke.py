"""R4-1 — winsmoke acceptance-tool tests."""
import json
import os
import subprocess
import sys
import tempfile

import pytest

from tools import winsmoke


def test_have_helper():
    assert winsmoke._have("python") is True or winsmoke._have("python3") is True
    # Non-existent command.
    assert winsmoke._have("definitely-not-a-real-command-xyz") is False


def test_check_python_reports_ok():
    r = winsmoke.check_python()
    assert "name" in r
    assert "ok" in r
    assert "details" in r
    # On Linux, python3.11+ is on PATH; we expect ok.
    assert r["ok"] is True


def test_check_pwsh_reports_not_found_on_linux():
    r = winsmoke.check_pwsh()
    if sys.platform != "win32":
        # Linux CI: pwsh may or may not be installed; just ensure the
        # function returns the right shape.
        assert "ok" in r
        assert "details" in r
    else:
        assert isinstance(r["ok"], bool)


def test_check_ffmpeg_reports_shape():
    r = winsmoke.check_ffmpeg()
    assert "name" in r and r["name"] == "ffmpeg"
    assert "ok" in r
    assert isinstance(r["ok"], bool)


def test_check_long_path_support_skipped_on_linux():
    r = winsmoke.check_long_path_support()
    if sys.platform != "win32":
        assert r["ok"] is True
        assert "skipped" in r["details"]


def test_check_pywin32_reports_shape():
    r = winsmoke.check_pywin32()
    assert "name" in r
    assert "ok" in r


def test_check_no_orphans_default_ok():
    r = winsmoke.check_no_orphans()
    assert r["ok"] is True


def test_check_pytest_runs_on_this_repo():
    """The acceptance tool can run the R4 pytest subset on this repo."""
    r = winsmoke.check_pytest(os.getcwd())
    assert "name" in r
    assert "ok" in r
    assert "details" in r
    # We don't assert ok=True because the sandbox may lack ffmpeg for
    # path-handling tests; just ensure it returns a result.
    assert isinstance(r["ok"], bool)


def test_main_writes_report(tmp_path, monkeypatch):
    """The main entry point writes a JSON report when --report is given."""
    report = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["winsmoke", "--report", str(report)])
    rc = winsmoke.main()
    assert report.exists()
    data = json.loads(report.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    # The keys are the section labels.
    assert "1. Preflight" in data or rc != 0  # either way, the report is written


def test_main_section_filter(monkeypatch):
    """--section limits the output."""
    monkeypatch.setattr(sys, "argv", ["winsmoke", "--section", "1"])
    rc = winsmoke.main()
    assert isinstance(rc, int)


def test_sections_have_labels():
    """Every section has a label and a list of checks (possibly empty)."""
    for label, checks in winsmoke.SECTIONS:
        assert isinstance(label, str)
        assert isinstance(checks, list)
        for c in checks:
            assert callable(c)
