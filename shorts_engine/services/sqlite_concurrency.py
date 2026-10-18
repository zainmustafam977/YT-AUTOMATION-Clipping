"""R4-2.2 — SQLite concurrency helpers: retry on SQLITE_BUSY, single-writer.

When many processes / threads write concurrently to SQLite, you get
``SQLITE_BUSY`` (database is locked) errors. The classic mitigation is:

  1. Use WAL mode (handled in sqlite_settings.py).
  2. Set busy_timeout (handled in sqlite_settings.py).
  3. Short transactions (this module's ``with_write_lock`` enforces it).
  4. Retry with exponential backoff on SQLITE_BUSY (``retry_on_busy``).
  5. Optional: a single-writer queue for processes (handled in the worker
     daemon, not here).

This module also exposes ``concurrency_smoke`` which runs N writer threads
on the same DB and reports how many succeeded, how many retried, and how
many gave up. Used by the health check + CI.
"""
from __future__ import annotations

import contextlib
import random
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional, TypeVar


T = TypeVar("T")


# Default backoff parameters.
DEFAULT_MAX_RETRIES = 5
DEFAULT_BASE_DELAY = 0.05  # seconds
DEFAULT_MAX_DELAY = 2.0


def _do_write(c: sqlite3.Connection, table: str, worker_id: int, i: int, lock_ctx) -> None:
    with lock_ctx(c):
        c.execute(
            f"INSERT INTO {table} (k, t) VALUES (?, ?)",
            (worker_id, f"w{worker_id}-i{i}"),
        )


def is_busy_error(exc: BaseException) -> bool:
    """Returns True if the exception is a SQLITE_BUSY (or SQLITE_LOCKED)."""
    if isinstance(exc, sqlite3.OperationalError):
        msg = str(exc).lower()
        return "database is locked" in msg or "database table is locked" in msg
    return False


def retry_on_busy_call(
    fn: Callable[[], T],
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    jitter: bool = True,
) -> T:
    """Run ``fn`` and retry on SQLITE_BUSY with exponential backoff.

    Use this when the operation is a single function call. For multi-
    statement transactions, use the ``retry_on_busy`` context manager
    (which the legacy single-attempt ``with`` form is preserved for).
    """
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:
            if not is_busy_error(e) or attempt >= max_retries:
                raise
            attempt += 1
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            if jitter:
                delay = delay * (0.5 + random.random() * 0.5)
            time.sleep(delay)


@contextlib.contextmanager
def retry_on_busy(
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    jitter: bool = True,
):
    """Context manager that runs the wrapped block once. The block is
    responsible for catching SQLITE_BUSY and re-entering via
    ``retry_on_busy_call`` (or re-raising to let this CM catch it on
    a single retry, then giving up after ``max_retries``).

    For most use-cases, prefer the explicit ``retry_on_busy_call(fn)``
    form. The CM is provided for legacy compatibility and short blocks
    where the body is a single statement.
    """
    # Suppress a single OperationalError and re-raise, with a sleep.
    attempt = [0]

    class _SingleAttemptCM:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            if exc_type is None:
                return False
            if not is_busy_error(exc_val):
                return False
            if attempt[0] >= max_retries:
                return False
            attempt[0] += 1
            delay = min(base_delay * (2 ** (attempt[0] - 1)), max_delay)
            if jitter:
                delay = delay * (0.5 + random.random() * 0.5)
            time.sleep(delay)
            # Returning False re-raises; the user is expected to re-enter
            # the with-block themselves.
            return False

    cm = _SingleAttemptCM()
    with cm as c:
        yield c


@contextlib.contextmanager
def with_write_lock(conn: sqlite3.Connection, *, timeout: float = 30.0):
    """Acquire SQLite's write lock and run a short transaction.

    Uses ``BEGIN IMMEDIATE`` so the write lock is taken at BEGIN time
    (not deferred until first write), so we never hold a read lock
    while trying to upgrade. The block must commit or rollback before
    exiting the context.
    """
    deadline = time.time() + timeout
    while True:
        try:
            conn.execute("BEGIN IMMEDIATE")
            break
        except sqlite3.OperationalError as e:
            if not is_busy_error(e):
                raise
            if time.time() >= deadline:
                raise
            time.sleep(0.01)
    try:
        yield
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


@dataclass
class ConcurrencyReport:
    workers: int
    writes_attempted: int = 0
    writes_succeeded: int = 0
    writes_retried: int = 0
    writes_failed: int = 0
    total_duration_sec: float = 0.0
    max_concurrency: int = 0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "workers": self.workers,
            "writes_attempted": self.writes_attempted,
            "writes_succeeded": self.writes_succeeded,
            "writes_retried": self.writes_retried,
            "writes_failed": self.writes_failed,
            "total_duration_sec": self.total_duration_sec,
            "max_concurrency": self.max_concurrency,
            "errors": self.errors[:10],  # truncate
        }

    def is_healthy(self) -> bool:
        return self.writes_failed == 0 and self.writes_succeeded == self.writes_attempted


def concurrency_smoke(
    db_path: str,
    *,
    workers: int = 4,
    writes_per_worker: int = 25,
    table: str = "concurrency_smoke",
) -> ConcurrencyReport:
    """Spawn ``workers`` threads, each performing ``writes_per_worker``
    INSERTs on the same DB. Returns a report.

    Run on a file-backed DB (WAL mode required for any meaningful
    concurrency). The test does NOT set up WAL — the caller is expected
    to have done that already via ``apply_safe_defaults``.
    """
    # Initialise the table once.
    init = sqlite3.connect(db_path)
    try:
        init.execute(f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY AUTOINCREMENT, k INTEGER, t TEXT)")
        init.commit()
    finally:
        init.close()

    report = ConcurrencyReport(workers=workers)
    concurrency = 0
    concurrency_lock = threading.Lock()
    barrier = threading.Barrier(workers)
    start = time.time()

    def worker(worker_id: int):
        nonlocal concurrency
        c = sqlite3.connect(db_path, timeout=30.0)
        try:
            # Synchronise start: try to enter the busy region all at once.
            barrier.wait(timeout=10.0)
            for i in range(writes_per_worker):
                report.writes_attempted += 1
                try:
                    retry_on_busy_call(
                        lambda: _do_write(c, table, worker_id, i, with_write_lock)
                    )
                    report.writes_succeeded += 1
                except Exception as e:
                    report.writes_failed += 1
                    report.errors.append(f"worker {worker_id} iter {i}: {e}")
                # Track approximate concurrency (in-flight threads).
                with concurrency_lock:
                    concurrency += 1
                    if concurrency > report.max_concurrency:
                        report.max_concurrency = concurrency
                with concurrency_lock:
                    concurrency -= 1
        finally:
            c.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60.0)
    report.total_duration_sec = time.time() - start
    return report
