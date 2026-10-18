"""R4-8.3 — Key-rotation tool tests."""
import sqlite3
import sys
from pathlib import Path
import pytest

from tools import rotate_keys


def test_generate_key_is_64_hex():
    k = rotate_keys._generate_key()
    assert len(k) == 64
    int(k, 16)  # must parse as hex


def test_record_rotation(tmp_path):
    db = tmp_path / "test.db"
    # Create the DB so the table can be created.
    conn = sqlite3.connect(str(db))
    conn.close()
    rotate_keys._record_rotation(db, "tester", "unit test")
    # Confirm the row is in there.
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT rotated_by, notes FROM key_rotation_log"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("tester", "unit test")]


def test_record_rotation_skips_missing_db(tmp_path):
    db = tmp_path / "nope.db"
    # Should not raise.
    rotate_keys._record_rotation(db, "tester", "skip")
    assert not db.exists()


def test_main_generate_prints_key(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["rotate_keys", "--generate"])
    rc = rotate_keys.main()
    out = capsys.readouterr().out.strip()
    assert len(out) == 64
    assert rc == 0


def test_main_dry_run(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "rotate_keys", "--new-key", "a" * 64, "--dry-run", "--rotated-by", "tester",
    ])
    rc = rotate_keys.main()
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert rc == 0


def test_main_rejects_short_key(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["rotate_keys", "--new-key", "abc"])
    rc = rotate_keys.main()
    assert rc == 2


def test_main_rejects_missing_key(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["rotate_keys"])
    rc = rotate_keys.main()
    assert rc == 2


def test_main_happy_path(tmp_path, monkeypatch, capsys):
    db = tmp_path / "x.db"
    sqlite3.connect(str(db)).close()  # create empty DB
    monkeypatch.setattr(sys, "argv", [
        "rotate_keys", "--new-key", "b" * 64, "--db", str(db),
        "--rotated-by", "tester", "--notes", "happy path",
    ])
    rc = rotate_keys.main()
    out = capsys.readouterr().out
    assert "NEW MASTER KEY" in out
    assert rc == 0
    # Confirm the rotation was logged.
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute("SELECT rotated_by FROM key_rotation_log").fetchall()
    finally:
        conn.close()
    assert rows == [("tester",)]
