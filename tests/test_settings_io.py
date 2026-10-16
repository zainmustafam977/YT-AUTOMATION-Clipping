"""Tests for E-009 settings export/import."""
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.services.settings_io import (
    SettingsIOError,
    export_settings,
    import_settings,
    preview_import,
)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    yield db
    db.close()


def test_export_omits_secrets(monkeypatch):
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "supersecret123")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-secret")
    monkeypatch.setenv("ENABLE_YOUTUBE_PUBLISH", "true")
    doc = export_settings(master_key="k" * 32)
    blob = str(doc)
    assert "supersecret123" not in blob
    assert "gsk-secret" not in blob


def test_export_includes_fingerprint():
    doc = export_settings(master_key="k" * 32)
    assert "fingerprint" in doc
    assert "schema_version" in doc
    assert doc["schema_version"] == 1


def test_export_then_preview_round_trip(monkeypatch):
    monkeypatch.setenv("ENABLE_YOUTUBE_PUBLISH", "true")
    doc = export_settings(master_key="k" * 32)
    diff = preview_import(doc, master_key="k" * 32)
    # ENABLE_YOUTUBE_PUBLISH is already set so no change.
    assert "ENABLE_YOUTUBE_PUBLISH" not in diff.changed


def test_import_detects_change(monkeypatch):
    monkeypatch.setenv("ENABLE_YOUTUBE_PUBLISH", "false")
    doc = export_settings(master_key="k" * 32)
    doc["settings"]["ENABLE_YOUTUBE_PUBLISH"] = "true"
    diff = preview_import(doc, master_key="k" * 32)
    assert "ENABLE_YOUTUBE_PUBLISH" in diff.changed


def test_import_rejects_key_mismatch():
    doc = export_settings(master_key="k1" * 16)
    with pytest.raises(SettingsIOError, match="mismatch"):
        preview_import(doc, master_key="k2" * 16)


def test_import_rejects_bad_version():
    doc = export_settings(master_key="k" * 32)
    doc["schema_version"] = 999
    with pytest.raises(SettingsIOError, match="version"):
        preview_import(doc, master_key="k" * 32)


def test_import_rejects_missing_fingerprint():
    doc = export_settings(master_key="k" * 32)
    del doc["fingerprint"]
    with pytest.raises(SettingsIOError, match="fingerprint"):
        preview_import(doc, master_key="k" * 32)


def test_import_rejects_missing_settings():
    with pytest.raises(SettingsIOError, match="settings"):
        preview_import({"schema_version": 1, "fingerprint": "x"}, master_key="x")


def test_import_dry_run_does_not_apply(monkeypatch):
    monkeypatch.setenv("ENABLE_YOUTUBE_PUBLISH", "false")
    doc = export_settings(master_key="k" * 32)
    doc["settings"]["ENABLE_YOUTUBE_PUBLISH"] = "true"
    diff = import_settings(doc, master_key="k" * 32, dry_run=True)
    assert "ENABLE_YOUTUBE_PUBLISH" in diff.changed
    assert os.getenv("ENABLE_YOUTUBE_PUBLISH") == "false"


def test_import_apply_changes_env(monkeypatch):
    monkeypatch.delenv("ENABLE_YOUTUBE_PUBLISH", raising=False)
    doc = export_settings(master_key="k" * 32)
    doc["settings"]["ENABLE_YOUTUBE_PUBLISH"] = "true"
    import_settings(doc, master_key="k" * 32, dry_run=False)
    assert os.getenv("ENABLE_YOUTUBE_PUBLISH") == "true"


def test_import_blocks_secrets_in_payload(monkeypatch):
    monkeypatch.setenv("ENABLE_YOUTUBE_PUBLISH", "false")
    doc = export_settings(master_key="k" * 32)
    # Attempt to inject a secret key into the import.
    doc["settings"]["GROQ_API_KEY"] = "evil"
    diff = preview_import(doc, master_key="k" * 32)
    assert "GROQ_API_KEY" in diff.blocked
    assert os.getenv("GROQ_API_KEY") is None


def test_import_persists_audit_row(db_session):
    doc = export_settings(master_key="k" * 32)
    import_settings(doc, master_key="k" * 32, dry_run=False, db=db_session, actor="alice")
    from shorts_engine.db.models import SettingsExport
    rows = db_session.query(SettingsExport).all()
    assert len(rows) == 1
    assert rows[0].created_by == "alice"


def test_export_actor_recorded():
    doc = export_settings(master_key="k" * 32, actor="bob")
    assert doc["settings"]["_actor"] == "bob"
