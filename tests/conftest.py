import os

# Prevent OpenMP and BLAS threading runtime conflicts between torch, ctranslate2, and pyav on Linux
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import json
import pytest
from datetime import date


@pytest.fixture(autouse=True)
def isolate_quota_state(monkeypatch, tmp_path):
    """
    Ensures each test execution starts with a clean, isolated YouTube quota state
    so that repeated local test runs never exhaust the daily 10,000 unit quota.
    """
    isolated_state_file = tmp_path / "test_quota_state.json"
    isolated_state_file.write_text(
        json.dumps({"date": date.today().isoformat(), "used_units": 0}),
        encoding="utf-8",
    )
    from shorts_engine.services.publishing.quota import QuotaTracker

    orig_init = QuotaTracker.__init__

    def patched_init(self, state_file=None):
        orig_init(self, state_file=state_file or isolated_state_file)

    monkeypatch.setattr(QuotaTracker, "__init__", patched_init)
    yield


# Obviously-fake Google OAuth client configuration. The real client_secret.json
# is a live credential and is deliberately not committed (see .gitignore), so
# OAuth tests previously had nothing to load and raised FileNotFoundError.
SYNTHETIC_CLIENT_SECRETS = {
    "installed": {
        "client_id": "746579743277-syntheticnotarealclient.apps.googleusercontent.com",
        "client_secret": "FAKE-TEST-ONLY-NOT-A-REAL-SECRET",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://127.0.0.1:8000/oauth2callback"],
    }
}


@pytest.fixture(autouse=True)
def synthetic_client_secrets(monkeypatch, tmp_path):
    """Supply a synthetic OAuth client-secrets file for tests that load one.

    Patched at `_find_client_secrets_file` (the discovery helper) rather than
    globally, so tests that pass an EXPLICIT path still observe genuine
    missing-file behaviour. `test_missing_client_secrets_raises_filenotfound`
    depends on that and must keep failing closed.
    """
    import importlib

    from shorts_engine.services.publishing.oauth_manager import ChannelOAuthManager

    path = tmp_path / "client_secret.json"
    path.write_text(json.dumps(SYNTHETIC_CLIENT_SECRETS), encoding="utf-8")

    # Managers constructed later pick this up through the discovery helper.
    monkeypatch.setattr(
        ChannelOAuthManager, "_find_client_secrets_file", lambda self: path
    )

    # Several dashboard routes build a module-level singleton at import time,
    # so their secrets_path is already frozen. Repoint those too.
    for mod_name in (
        "shorts_engine.dashboard.routes.channels",
        "shorts_engine.dashboard.routes.oauth",
        "shorts_engine.dashboard.routes.publish",
    ):
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        singleton = getattr(mod, "oauth_manager", None)
        if singleton is not None:
            monkeypatch.setattr(singleton, "secrets_path", path, raising=False)
    yield


@pytest.fixture
def in_memory_sqlite():
    """Bind `shorts_engine.db.session` to a private in-memory SQLite DB.

    Use this instead of setting DATABASE_URL in os.environ at module import
    time. `shorts_engine.db.session` builds its engine from DATABASE_URL when
    it is first imported, and pytest imports `tests/e2e/*` before the
    top-level test modules, so an env var set during collection leaked an
    empty in-memory database into every later test and surfaced as
    "no such table: ..." failures far away from the cause.

    Deliberately avoids `monkeypatch` (function-scoped) and restores state in
    a finally block, so this fixture can be requested at any scope and the
    in-memory binding can never outlive its consumer.
    """
    import os

    import shorts_engine.db.session as db_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import scoped_session, sessionmaker
    from sqlalchemy.pool import StaticPool

    saved = {
        "env": os.environ.get("DATABASE_URL"),
        "engine": db_session.engine,
        "SessionLocal": db_session.SessionLocal,
    }

    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    mem_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db_session.Base.metadata.create_all(mem_engine)
    db_session.engine = mem_engine
    db_session.SessionLocal = scoped_session(
        sessionmaker(autocommit=False, autoflush=False, bind=mem_engine)
    )
    try:
        yield mem_engine
    finally:
        if saved["env"] is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved["env"]
        db_session.engine = saved["engine"]
        db_session.SessionLocal = saved["SessionLocal"]
        mem_engine.dispose()


@pytest.fixture(autouse=True)
def default_test_master_key(monkeypatch):
    """Provides a valid test Fernet master key for test execution if not explicitly set."""
    if not os.environ.get("SECRETS_MASTER_KEY"):
        from shorts_engine.config.settings import settings
        env_key = getattr(settings, "SECRETS_MASTER_KEY", None)
        if env_key:
            monkeypatch.setenv("SECRETS_MASTER_KEY", env_key)
        else:
            from cryptography.fernet import Fernet
            test_key = Fernet.generate_key().decode()
            monkeypatch.setenv("SECRETS_MASTER_KEY", test_key)
    yield

