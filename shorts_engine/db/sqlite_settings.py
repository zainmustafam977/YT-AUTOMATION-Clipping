"""R4-2.1 — SQLite settings verification.

Reads the current connection's PRAGMA values and asserts that WAL mode,
foreign keys, busy_timeout, and synchronous=NORMAL are all active. This
runs at startup and surfaces in /health as well, so a misconfigured
database is caught immediately.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class SqliteSettings:
    journal_mode: str
    synchronous: int
    foreign_keys: int
    busy_timeout: int

    def to_dict(self) -> dict:
        return asdict(self)

    def issues(self) -> list[str]:
        """Returns a list of human-readable issues. Empty list = healthy."""
        issues: list[str] = []
        if self.journal_mode.lower() != "wal":
            issues.append(
                f"journal_mode is {self.journal_mode!r}, expected 'wal' "
                "(WAL mode is required for concurrent reader/writer access)"
            )
        if self.foreign_keys != 1:
            issues.append(
                f"foreign_keys is {self.foreign_keys}, expected 1 "
                "(FK enforcement is required for data integrity)"
            )
        if self.busy_timeout < 5000:
            issues.append(
                f"busy_timeout is {self.busy_timeout}ms, expected >= 5000ms "
                "(short timeouts cause SQLITE_BUSY errors under contention)"
            )
        if self.synchronous not in (1, 2):
            # 1=NORMAL, 2=FULL
            issues.append(
                f"synchronous is {self.synchronous}, expected 1 (NORMAL) or 2 (FULL)"
            )
        return issues

    def is_healthy(self) -> bool:
        return len(self.issues()) == 0


def read_settings(conn: sqlite3.Connection) -> SqliteSettings:
    """Read the current connection's PRAGMA values."""
    cur = conn.execute("PRAGMA journal_mode")
    journal_mode = str(cur.fetchone()[0])
    cur = conn.execute("PRAGMA synchronous")
    synchronous = int(cur.fetchone()[0])
    cur = conn.execute("PRAGMA foreign_keys")
    foreign_keys = int(cur.fetchone()[0])
    cur = conn.execute("PRAGMA busy_timeout")
    busy_timeout = int(cur.fetchone()[0])
    return SqliteSettings(
        journal_mode=journal_mode,
        synchronous=synchronous,
        foreign_keys=foreign_keys,
        busy_timeout=busy_timeout,
    )


def verify_health(conn: sqlite3.Connection) -> tuple[bool, list[str], SqliteSettings]:
    """Returns (healthy, issues, settings)."""
    s = read_settings(conn)
    issues = s.issues()
    return (len(issues) == 0, issues, s)


def apply_safe_defaults(conn: sqlite3.Connection) -> None:
    """Apply the recommended PRAGMA values to a connection. Idempotent."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
