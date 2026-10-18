"""R4-2.3 — PostgreSQL integration tests.

These tests connect to a real PostgreSQL instance (e.g. the one started
by the ``postgres`` service in agent.yml). They are skipped when no
``DATABASE_URL`` env var is set to a ``postgresql+psycopg2://`` URL.

Run with:
    DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/shorts_engine_test \
        pytest tests/test_pg_integration.py -v
"""
import os
import time
import uuid
import pytest


PG_URL = os.environ.get("DATABASE_URL", "")
PG_ENABLED = PG_URL.startswith("postgresql")

pytestmark = pytest.mark.skipif(
    not PG_ENABLED,
    reason="DATABASE_URL not set to a postgresql URL; PG not under test",
)


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy import create_engine
    eng = create_engine(PG_URL, future=True)
    yield eng
    eng.dispose()


@pytest.fixture()
def schema(engine):
    """Create the schema fresh for each test."""
    from shorts_engine.db.session import Base
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


def test_select_one(engine):
    """The connection works and PostgreSQL responds."""
    with engine.connect() as conn:
        from sqlalchemy import text
        r = conn.execute(text("SELECT 1 AS one")).one()
        assert r.one == 1


def test_insert_and_query(schema):
    """Insert a row into a model and read it back."""
    from sqlalchemy.orm import Session
    from shorts_engine.db import models  # noqa: F401  (registers models)
    from shorts_engine.db.models import Clip
    with Session(schema) as s:
        c = Clip(
            id=str(uuid.uuid4()),
            source_url="https://example.com/pg",
            status="queued",
            title="PG test",
        )
        s.add(c)
        s.commit()
        s.refresh(c)
        assert c.id
        got = s.get(Clip, c.id)
        assert got is not None
        assert got.title == "PG test"


def test_fk_enforced(schema):
    """Foreign keys are enforced by PostgreSQL."""
    from sqlalchemy.orm import Session
    from sqlalchemy import text
    with Session(schema) as s:
        s.execute(text(
            "INSERT INTO channels (id, title, handle) "
            "VALUES (1, 'C', '@c')"
        ))
        s.commit()
    # Inserting a clip with a non-existent channel_id should fail.
    with Session(schema) as s:
        from sqlalchemy import text
        with pytest.raises(Exception):
            s.execute(text(
                "INSERT INTO clips (id, source_url, status, channel_id) "
                "VALUES (:id, :u, :st, 999)"
            ), {"id": str(uuid.uuid4()), "u": "https://x", "st": "queued"})
            s.commit()


def test_cascade_delete(schema):
    """Deleting a parent cascades to children if configured."""
    from sqlalchemy.orm import Session
    from sqlalchemy import text
    with Session(schema) as s:
        s.execute(text(
            "INSERT INTO channels (id, title, handle) VALUES (1, 'C', '@c')"
        ))
        s.execute(text(
            "INSERT INTO clips (id, source_url, status, channel_id) "
            "VALUES (:id, :u, :st, 1)"
        ), {"id": str(uuid.uuid4()), "u": "https://x", "st": "queued"})
        s.commit()
    with Session(schema) as s:
        # Try cascade via raw DELETE; on PG this respects FK constraints.
        try:
            s.execute(text("DELETE FROM channels WHERE id = 1"))
            s.commit()
        except Exception:
            s.rollback()
            # If the FK is RESTRICT, the delete is blocked — that's fine.
            return
        rows = s.execute(text(
            "SELECT COUNT(*) FROM clips WHERE channel_id = 1"
        )).scalar()
        assert rows == 0


def test_concurrent_writers(engine):
    """10 writers × 10 inserts should all succeed without deadlock."""
    import threading
    from sqlalchemy.orm import Session

    results = []
    errors = []
    lock = threading.Lock()

    def writer(i: int):
        try:
            with Session(engine) as s:
                from shorts_engine.db.models import Clip
                for j in range(10):
                    s.add(Clip(
                        id=str(uuid.uuid4()),
                        source_url=f"https://x.com/{i}/{j}",
                        status="queued",
                        title=f"PG writer {i}-{j}",
                    ))
                s.commit()
            with lock:
                results.append(i)
        except Exception as e:
            with lock:
                errors.append((i, str(e)))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    elapsed = time.time() - t0
    assert not errors, f"errors: {errors}"
    assert len(results) == 10
    # Sanity: all 100 rows are in the DB.
    with engine.connect() as conn:
        from sqlalchemy import text
        n = conn.execute(text("SELECT COUNT(*) FROM clips")).scalar()
    # n may be > 100 if other tests left rows; just assert >= 100.
    assert n >= 100


def test_rollback_on_error(schema):
    """A failed transaction rolls back cleanly."""
    from sqlalchemy.orm import Session
    from sqlalchemy import text
    with Session(schema) as s:
        s.execute(text(
            "INSERT INTO channels (id, title, handle) VALUES (2, 'C2', '@c2')"
        ))
        s.commit()
    with Session(schema) as s:
        s.execute(text(
            "INSERT INTO channels (id, title, handle) VALUES (2, 'DUP', '@d')"
        ))
        with pytest.raises(Exception):
            s.commit()
        # After rollback, the session is usable again.
        s.rollback()
        rows = s.execute(text("SELECT COUNT(*) FROM channels")).scalar()
        assert rows == 1
