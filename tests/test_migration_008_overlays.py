"""Tests for the overlay migration (008_overlay_engine.sql) and rollback."""
import os
import re
from pathlib import Path


def _migration_files():
    base = Path(__file__).resolve().parent.parent / "migrations"
    return base / "008_overlay_engine.sql", base / "008_overlay_engine_rollback.sql"


def test_migration_file_exists():
    fwd, _ = _migration_files()
    assert fwd.exists(), f"missing migration file {fwd}"


def test_rollback_file_exists():
    _, rev = _migration_files()
    assert rev.exists(), f"missing rollback file {rev}"


def test_migration_creates_overlay_preset():
    fwd, _ = _migration_files()
    sql = fwd.read_text()
    assert "CREATE TABLE" in sql.upper()
    assert "overlay_preset" in sql.lower()


def test_migration_creates_overlay_render_audit():
    fwd, _ = _migration_files()
    sql = fwd.read_text()
    assert "overlay_render_audit" in sql.lower()


def test_migration_creates_indexes():
    fwd, _ = _migration_files()
    sql = fwd.read_text()
    assert "CREATE INDEX" in sql.upper()


def test_rollback_drops_tables():
    _, rev = _migration_files()
    sql = rev.read_text()
    assert "DROP TABLE" in sql.upper()
    assert "overlay_preset" in sql.lower()
    assert "overlay_render_audit" in sql.lower()


def test_rollback_is_symmetric():
    fwd, rev = _migration_files()
    fwd_sql = fwd.read_text().upper()
    rev_sql = rev.read_text().upper()
    # Every CREATE TABLE in fwd has a matching DROP TABLE in rev.
    fwd_tables = set(re.findall(r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+(\w+)", fwd_sql))
    rev_tables = set(re.findall(r"DROP TABLE(?:\s+IF EXISTS)?\s+(\w+)", rev_sql))
    assert fwd_tables == rev_tables, f"mismatch: fwd={fwd_tables} rev={rev_tables}"


def test_migration_is_idempotent():
    fwd, _ = _migration_files()
    sql = fwd.read_text()
    # CREATE TABLE / CREATE INDEX should use IF NOT EXISTS so re-runs are safe.
    assert "IF NOT EXISTS" in sql.upper()
