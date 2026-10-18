"""R4-6.4 — Build-Windows distribution tool tests."""
import sys
import zipfile
from pathlib import Path
import pytest

from tools import build_windows


def test_copy_tree_skips_excluded(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "ok.txt").write_text("ok", encoding="utf-8")
    (src / "skip.txt").write_text("no", encoding="utf-8")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "x.pyc").write_text("no", encoding="utf-8")
    dst = tmp_path / "dst"
    dst.mkdir()
    build_windows._copy_tree(src, dst, exclude={"skip.txt", "__pycache__"})
    assert (dst / "ok.txt").exists()
    assert not (dst / "skip.txt").exists()
    assert not (dst / "__pycache__").exists()


def test_build_produces_zip(tmp_path):
    """A zip is produced at output/YT-Shorts-Automation-<version>.zip."""
    zpath = build_windows.build(tmp_path, "9.9.9-test")
    assert zpath.exists()
    assert zpath.name == "YT-Shorts-Automation-9.9.9-test.zip"
    # Inspect the zip.
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
    assert any("INSTALL.txt" in n for n in names)


def test_zip_contains_setup_scripts(tmp_path):
    zpath = build_windows.build(tmp_path, "9.9.9-test")
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
    assert any("setup.bat" in n for n in names)
    assert any("setup.ps1" in n for n in names)


def test_verify_prints_steps_on_linux(tmp_path, capsys):
    zpath = build_windows.build(tmp_path, "9.9.9-test")
    rc = build_windows.verify(zpath)
    out = capsys.readouterr().out
    assert "Windows VM" in out
    assert rc == 0


def test_main_writes_zip(tmp_path, monkeypatch):
    out = tmp_path / "dist"
    monkeypatch.setattr(sys, "argv", ["build_windows", "--output", str(out), "--version", "9.9.9-main"])
    rc = build_windows.main()
    assert rc == 0
    assert (out / "YT-Shorts-Automation-9.9.9-main.zip").exists()
