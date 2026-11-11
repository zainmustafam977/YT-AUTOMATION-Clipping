"""SQLite DBAPI adapter wrapping a SQLAlchemy Connection.

Provides a sqlite3.Connection-compatible interface so existing services in
overlay/, search/, and triage/ can keep calling raw sqlite3 APIs without
being rewritten to use the ORM, while never exposing the raw driver connection
to direct close() (which causes the _sqlite() segfault under concurrency).
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from sqlalchemy import text


def _replace_placeholders(sql: str, params: Any) -> tuple:
    """Replace ``?`` placeholders in SQL with ``:_P0``, ``:_P1``, ... and
    return ``(new_sql, new_params)`` where ``new_params`` is a dict (or list
    of dicts for multi-row) keyed by the new names.
    """
    # Determine if multi-row.
    is_multi = isinstance(params, list) and params and isinstance(params[0], (list, tuple))
    rows: list = list(params) if is_multi else [params]
    # Infer column names from the first row.
    first = rows[0]
    if isinstance(first, dict):
        col_names = list(first.keys())
    else:
        col_names = [f"_P{i}" for i in range(len(first))]
    # Tokenizer-based replacement: only replace ? that are outside strings.
    out: list = []
    i = 0
    pc = 0  # placeholder counter

    def _flush(s: str) -> None:
        out.append(s)

    while i < len(sql):
        c = sql[i]
        if c == "'":
            # Single-quoted string: flush up to and including closing '.
            j = i + 1
            while j < len(sql):
                if sql[j] == "'":
                    # Check for escaped '' (sqlite doubles quotes inside strings).
                    if j + 1 < len(sql) and sql[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            out.append(sql[i:j])
            i = j
        elif c == '"':
            # Double-quoted identifier (sqlite allows "id" as identifier).
            j = i + 1
            while j < len(sql) and sql[j] != '"':
                j += 1
            if j < len(sql):
                j += 1
            out.append(sql[i:j])
            i = j
        elif c == "?":
            # Placeholder.
            name = col_names[pc % len(col_names)]
            pc += 1
            out.append(f":{name}")
            i += 1
        else:
            out.append(c)
            i += 1
    new_sql = "".join(out)
    # Build params dict(s).
    if len(rows) == 1:
        row = rows[0]
        if isinstance(row, dict):
            return new_sql, row
        if len(row) > len(col_names):
            col_names = [f"_P{i}" for i in range(len(row))]
        return new_sql, dict(zip(col_names, row))
    # Multi-row -> list of dicts.
    out_params: list = []
    for row in rows:
        if isinstance(row, dict):
            out_params.append(row)
        else:
            if len(row) > len(col_names):
                col_names = [f"_P{i}" for i in range(len(row))]
            out_params.append(dict(zip(col_names, row)))
    return new_sql, out_params


class SqliteAdapter:
    """Wraps a SQLAlchemy Connection as a sqlite3.Connection-like object.

    The ``close()`` method disposes the SQLAlchemy connection properly instead
    of closing the raw driver connection, so the pool can reuse it safely.

    SQLAlchemy 2.0's execution engine rejects raw tuples for ``?``-style
    statements via ``_distill_params_20``.  We convert positional ``?``
    placeholders to named ``:_P0, :_P1, ...`` placeholders and supply a dict
    of the same names, which the engine accepts and the DBAPI driver binds
    correctly.
    """

    def __init__(self, sa_conn: Any) -> None:
        self._sa_conn = sa_conn
        self._closed = False
        self._lastrowid: int = 0

    # ---- sqlite3 connection attributes ----

    def cursor(self) -> "CursorAdapter":
        return CursorAdapter(self._sa_conn, self)

    @property
    def total_changes(self) -> int:  # pragma: no cover
        return 0

    def commit(self) -> None:
        self._sa_conn.commit()

    def rollback(self) -> None:
        self._sa_conn.rollback()

    def close(self) -> None:
        """Dispose the SQLAlchemy connection instead of closing the raw driver."""
        if not self._closed:
            self._closed = True
            self._sa_conn.close()

    # ---- public API matching sqlite3.Connection ----

    def execute(self, sql: str, params: Optional[Any] = None) -> "CursorAdapter":
        stmt, distilled = self._prepare(sql, params)
        result = self._sa_conn.execute(stmt, distilled)
        pk: Any = None
        if getattr(result, "isinsert", False):
            pk = getattr(result, "inserted_primary_key", None)
        if pk is not None and len(pk) > 0:
            self._lastrowid = int(pk[0])
        else:
            # Fallback: query sqlite3's last_insert_rowid directly.
            try:
                cur = self._sa_conn.connection.cursor()
                cur.execute("SELECT last_insert_rowid()")
                self._lastrowid = int(cur.fetchone()[0])
                cur.close()
            except Exception:
                self._lastrowid = 0
        return CursorAdapter(result, self)

    def executemany(self, sql: str, seq_of_params: Sequence[Any]) -> None:
        if not seq_of_params:
            return
        stmt, distilled = self._prepare(sql, list(seq_of_params))
        self._sa_conn.execute(stmt, distilled)

    def create_function(
        self,
        name: str,
        num_args: int,
        func: Any,
        deterministic: bool = False,
    ) -> None:
        # Best-effort shim; not all dialects support func_register.
        try:
            self._sa_conn.func_register(name, func, deterministic)
        except Exception:
            pass

    # ---- convenience re-exports of cursor results ----

    def fetchone(self, sql: str, params: Optional[Any] = None) -> Optional[Any]:
        return self.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: Optional[Any] = None) -> list:
        return self.execute(sql, params).fetchall()

    @property
    def lastrowid(self) -> int:
        return self._lastrowid

    @lastrowid.setter
    def lastrowid(self, value: int) -> None:
        self._lastrowid = value

    @staticmethod
    def _prepare(
        sql: str, params: Optional[Any]
    ) -> tuple:  # -> (sqlalchemy.text, params dict or None)
        """Convert ``?``-style SQL + positional params to named-style for SA 2.0.

        SQLAlchemy 2.0's ``_distill_params_20`` rejects raw tuples for ``?``
        placeholder statements; it requires a dict (or a list of dicts for
        multi-row).  We rewrite the SQL to use named placeholders ``:_P0``,
        ``:_P1``, ... matching the positional params and supply a dict (or list
        of dicts) so the execution engine and DBAPI driver bind correctly.
        """
        if params is None:
            return text(sql), None
        if isinstance(params, dict):
            return text(sql), params
        if not isinstance(params, (list, tuple)):
            return text(sql), params
        # empty sequence -> no bindings (SQLAlchemy treats as None)
        if not params:
            return text(sql), None
        new_sql, new_params = _replace_placeholders(sql, params)
        return text(new_sql), new_params

    # ---- compat helpers (rarely used by consumers) ----

    def setparam(self, cursor: Any, name: str, value: Any) -> None:
        """Best-effort hook for consumers that call set_param on a cursor."""
        pass

    def iterdump(self) -> Any:
        """Return an iterator over SQL dump statements (sqlite3.Connection.iterdump)."""
        import sqlite3 as _sqlite3

        return _sqlite3.connect(":memory:").iterdump()

    def create_aggregate(
        self,
        name: str,
        num_args: int,
        aggregate_class: Any,
    ) -> None:
        """Register a sqlite3 aggregate class (best-effort, dialect dependent)."""
        try:
            self._sa_conn.registry.register_aggregate(
                name, num_args, aggregate_class
            )
        except Exception:
            pass


class CursorAdapter:
    """Wraps a SQLAlchemy Result as a sqlite3.Cursor-like object."""

    def __init__(self, sa_result: Any, conn: SqliteAdapter) -> None:
        self._sa_result = sa_result
        self._conn = conn
        self._closed = False

    # ---- cursor attributes ----

    @property
    def description(self) -> Optional[Sequence[Any]]:
        if self._closed:
            return None
        columns = self._sa_result.keys()
        if not columns:
            return None
        desc: list = []
        for col in columns:
            desc.append(
                (
                    col,
                    Any,  # type_code
                    None,  # display_size
                    None,  # internal_size
                    None,  # precision
                    None,  # scale
                    True,  # nullable
                )
            )
        return desc

    @property
    def keys(self) -> list:
        return list(self._sa_result.keys()) if self._sa_result.keys() else []

    @property
    def lastrowid(self) -> int:
        """Row id of the last successful INSERT, like ``sqlite3.Cursor.lastrowid``.

        The owning adapter records it in ``execute`` (from SQLAlchemy's
        ``inserted_primary_key`` or a ``last_insert_rowid()`` fallback), so
        callers that read ``cursor.lastrowid`` after an INSERT keep working.
        """
        return self._conn._lastrowid

    # ---- sqlite3 cursor methods ----

    def close(self) -> None:
        self._closed = True

    def fetchone(self) -> Optional[Any]:
        if self._closed:
            return None
        return self._sa_result.fetchone()

    def fetchall(self) -> list:
        if self._closed:
            return []
        return self._sa_result.fetchall()

    def fetchmany(self, size: Optional[int] = None) -> list:
        if self._closed:
            return []
        return self._sa_result.fetchmany(size=size)

    def execute(
        self,
        sql: str,
        params: Optional[Any] = None,
    ) -> "CursorAdapter":
        if self._closed:
            raise RuntimeError("Cursor is closed")
        # Defer to the owning SqliteAdapter so param conversion is applied.
        cur = self._conn.execute(sql, params)
        self._sa_result = cur._sa_result
        self._conn._lastrowid = cur._conn._lastrowid
        return self

    def executemany(
        self,
        sql: str,
        seq_of_params: Sequence[Any],
    ) -> None:
        if self._closed:
            raise RuntimeError("Cursor is closed")
        self._conn.executemany(sql, seq_of_params)

    def executescript(self, sql_script: str) -> None:
        if self._closed:
            raise RuntimeError("Cursor is closed")
        self._conn._sa_conn.execute(text(sql_script))

    def __iter__(self):
        return self._sa_result.__iter__()

    # ---- backwards compat: row access ----

    def __getitem__(self, idx: int) -> Any:
        row = self._sa_result.fetchone()
        if row is None:
            raise IndexError("no row available")
        return row[idx]

    def __len__(self) -> int:
        return len(self._sa_result.keys()) if self._sa_result.keys() else 0
