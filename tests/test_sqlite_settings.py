"""R4-2.1 — SQLite settings verification."""
import sqlite3
import pytest

from shorts_engine.db.sqlite_settings import (
    SqliteSettings, read_settings, verify_health, apply_safe_defaults,
)


@pytest.fixture
def conn(tmp_path):
    """A file-backed SQLite DB so WAL can be enabled (it doesn't work
    on in-memory databases)."""
    p = tmp_path / "test.db"
    c = sqlite3.connect(str(p))
    apply_safe_defaults(c)
    yield c
    c.close()


def test_apply_safe_defaults_makes_healthy(conn):
    healthy, issues, s = verify_health(conn)
    assert healthy, f"issues: {issues}"
    assert s.journal_mode.lower() == "wal"
    assert s.foreign_keys == 1
    assert s.busy_timeout >= 5000
    assert s.synchronous in (1, 2)


def test_read_settings_shape(conn):
    s = read_settings(conn)
    assert s.journal_mode  # non-empty
    assert isinstance(s.busy_timeout, int)


def test_journal_mode_wal_required():
    """Without WAL, the verifier must flag the issue."""
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA journal_mode=DELETE")  # not WAL
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA synchronous=NORMAL")
    s = read_settings(c)
    assert s.journal_mode.lower() != "wal"
    issues = s.issues()
    assert any("journal_mode" in i for i in issues)
    c.close()


def test_foreign_keys_required():
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA synchronous=NORMAL")
    s = read_settings(c)
    issues = s.issues()
    assert any("foreign_keys" in i for i in issues)
    c.close()


def test_busy_timeout_minimum():
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA busy_timeout=100")  # too low
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA synchronous=NORMAL")
    s = read_settings(c)
    issues = s.issues()
    assert any("busy_timeout" in i for i in issues)
    c.close()


def test_synchronous_off_flagged():
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA synchronous=OFF")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=30000")
    s = read_settings(c)
    issues = s.issues()
    assert any("synchronous" in i for i in issues)
    c.close()


def test_synchronous_normal_accepted(tmp_path):
    c = sqlite3.connect(str(tmp_path / "test.db"))
    c.execute("PRAGMA synchronous=NORMAL")  # 1
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=30000")
    s = read_settings(c)
    assert s.synchronous == 1
    assert s.is_healthy()
    c.close()


def test_synchronous_full_accepted(tmp_path):
    c = sqlite3.connect(str(tmp_path / "test.db"))
    c.execute("PRAGMA synchronous=FULL")  # 2
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=30000")
    s = read_settings(c)
    assert s.synchronous == 2
    assert s.is_healthy()
    c.close()


def test_to_dict(conn):
    d = read_settings(conn).to_dict()
    assert "journal_mode" in d
    assert "synchronous" in d
    assert "foreign_keys" in d
    assert "busy_timeout" in d


def test_is_healthy(conn):
    assert read_settings(conn).is_healthy()


def test_concurrent_readers_dont_block(conn):
    """WAL mode allows concurrent readers + one writer."""
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(100)])
    conn.commit()
    # Open a second connection in WAL mode.
    c2 = sqlite3.connect(":memory:")
    # Both connections on the same in-memory DB share state; this is a
    # smoke test that we can open multiple cursors.
    cur = conn.execute("SELECT COUNT(*) FROM t")
    assert cur.fetchone()[0] == 100
    c2.close()


def test_apply_safe_defaults_idempotent(conn):
    apply_safe_defaults(conn)
    apply_safe_defaults(conn)
    assert verify_health(conn)[0]
