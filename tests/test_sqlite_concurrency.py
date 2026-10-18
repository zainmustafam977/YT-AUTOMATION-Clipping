"""R4-2.2 — SQLite concurrency tests (retry on SQLITE_BUSY, single-writer, smoke)."""
import sqlite3
import threading
import time
import pytest

from shorts_engine.db.sqlite_settings import apply_safe_defaults
from shorts_engine.services.sqlite_concurrency import (
    is_busy_error, retry_on_busy, retry_on_busy_call, with_write_lock,
    concurrency_smoke, ConcurrencyReport,
)


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "concurrency.db"
    # Initialise the DB with safe defaults (WAL etc.).
    c = sqlite3.connect(str(p))
    apply_safe_defaults(c)
    c.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
    c.commit()
    c.close()
    return str(p)


# --- is_busy_error ----------------------------------------------------------

def test_is_busy_error_operational_locked():
    e = sqlite3.OperationalError("database is locked")
    assert is_busy_error(e) is True


def test_is_busy_error_operational_other():
    e = sqlite3.OperationalError("syntax error")
    assert is_busy_error(e) is False


def test_is_busy_error_other_type():
    e = ValueError("not busy")
    assert is_busy_error(e) is False


# --- retry_on_busy ----------------------------------------------------------

def test_retry_on_busy_succeeds_immediately():
    calls = [0]
    def fn():
        calls[0] += 1
        return "ok"
    assert retry_on_busy_call(fn) == "ok"
    assert calls[0] == 1


def test_retry_on_busy_recovers_after_busy(monkeypatch):
    """The function raises OperationalError on the first call, then succeeds."""
    state = {"calls": 0}

    def fake_sleep(_):
        return None  # no actual sleep in tests

    monkeypatch.setattr("time.sleep", fake_sleep)
    def fn():
        state["calls"] += 1
        if state["calls"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return "ok"
    assert retry_on_busy_call(fn) == "ok"
    assert state["calls"] == 2


def test_retry_on_busy_gives_up_after_max(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    calls = [0]
    def fn():
        calls[0] += 1
        raise sqlite3.OperationalError("database is locked")
    with pytest.raises(sqlite3.OperationalError):
        retry_on_busy_call(fn, max_retries=2, base_delay=0)
    # 1 initial + 2 retries = 3
    assert calls[0] == 3


def test_retry_on_busy_no_retry_for_non_busy():
    """Non-busy errors are re-raised immediately, not retried."""
    calls = [0]
    def fn():
        calls[0] += 1
        raise ValueError("not busy")
    with pytest.raises(ValueError):
        retry_on_busy_call(fn)
    assert calls[0] == 1


def test_retry_on_busy_backoff_increases(monkeypatch):
    delays = []
    monkeypatch.setattr("time.sleep", lambda d: delays.append(d))
    def fn():
        raise sqlite3.OperationalError("database is locked")
    with pytest.raises(sqlite3.OperationalError):
        retry_on_busy_call(fn, max_retries=3, base_delay=0.1, max_delay=10.0, jitter=False)
    # First retry: 0.1, second: 0.2, third: 0.4 (without jitter)
    assert delays[0] == pytest.approx(0.1)
    assert delays[1] == pytest.approx(0.2)
    assert delays[2] == pytest.approx(0.4)


# --- with_write_lock --------------------------------------------------------

def test_with_write_lock_commits(db_path):
    c = sqlite3.connect(db_path)
    try:
        with with_write_lock(c):
            c.execute("INSERT INTO t (x) VALUES (?)", (42,))
        cur = c.execute("SELECT x FROM t WHERE x = 42")
        assert cur.fetchone()[0] == 42
    finally:
        c.close()


def test_with_write_lock_rolls_back_on_exception(db_path):
    c = sqlite3.connect(db_path)
    try:
        with pytest.raises(RuntimeError):
            with with_write_lock(c):
                c.execute("INSERT INTO t (x) VALUES (?)", (99,))
                raise RuntimeError("boom")
        cur = c.execute("SELECT x FROM t WHERE x = 99")
        assert cur.fetchone() is None
    finally:
        c.close()


def test_with_write_lock_timeout(monkeypatch, db_path):
    """If the lock can't be acquired within timeout, raises."""
    # Acquire the lock from one connection.
    holder = sqlite3.connect(db_path)
    holder.execute("BEGIN IMMEDIATE")
    monkeypatch.setattr("time.sleep", lambda _: None)  # no actual sleep
    try:
        c = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.OperationalError):
                with with_write_lock(c, timeout=0.05):
                    c.execute("INSERT INTO t (x) VALUES (1)")
        finally:
            c.close()
    finally:
        holder.rollback()
        holder.close()


# --- concurrency_smoke ------------------------------------------------------

def test_concurrency_smoke_all_succeed(db_path):
    report = concurrency_smoke(db_path, workers=4, writes_per_worker=25)
    assert report.writes_attempted == 4 * 25
    assert report.is_healthy(), f"errors: {report.errors}"


def test_concurrency_smoke_serialised_correctly(db_path):
    """Even under contention, all rows must be present at the end."""
    concurrency_smoke(db_path, workers=3, writes_per_worker=10)
    c = sqlite3.connect(db_path)
    try:
        cur = c.execute("SELECT COUNT(*) FROM concurrency_smoke")
        assert cur.fetchone()[0] == 3 * 10
    finally:
        c.close()


def test_concurrency_smoke_high_contention(db_path):
    report = concurrency_smoke(db_path, workers=8, writes_per_worker=50)
    assert report.writes_attempted == 8 * 50
    # The retry helper should make this healthy; if not, we have a problem.
    assert report.is_healthy(), f"writes_failed={report.writes_failed} errors={report.errors[:3]}"


def test_concurrency_smoke_report_shape(db_path):
    report = concurrency_smoke(db_path, workers=2, writes_per_worker=5)
    d = report.to_dict()
    for k in ("workers", "writes_attempted", "writes_succeeded", "writes_retried",
              "writes_failed", "total_duration_sec", "max_concurrency", "errors"):
        assert k in d
    assert d["workers"] == 2
    assert d["writes_attempted"] == 10
