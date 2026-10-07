"""
Tests for Granular Data & Storage Reset Center and Configuration Paths Inspector.
Verifies inventory reporting, granular deletion, cascade safety, self-healing directories,
and zero-crash operation under all deletion presets.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.db.models import (
    Base, User, UserRole, Channel, SourceVideo, Transcript, ClipCandidate, Clip, ClipStatus, PublishLog, PlatformPublishLog
)
from shorts_engine.services.storage.reset_service import ResetService, ResetOptions
from shorts_engine.config.settings import settings


@pytest.fixture
def test_db_session(tmp_path):
    """Provide an isolated in-memory or SQLite file session for reset testing."""
    test_db_path = tmp_path / "test_reset.db"
    engine = create_engine(f"sqlite:///{test_db_path}")
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()

    # Seed admin user
    admin = User(username="admin", role=UserRole.ADMIN, password_hash="fakehashedpassword")
    session.add(admin)
    session.commit()

    yield session
    session.close()


def test_config_paths_inventory():
    """Verify that get_config_paths_inventory returns all canonical paths and required keys."""
    paths_inv = ResetService.get_config_paths_inventory()
    assert isinstance(paths_inv, dict)
    
    expected_keys = [
        "env_example",
        "runtime_settings",
        "sqlite_database",
        "encryption_vault_key",
        "raw_downloads_dir",
        "rendered_outputs_dir",
        "temp_scratch_dir",
        "watch_folder_dir",
        "fonts_dir",
    ]
    for key in expected_keys:
        assert key in paths_inv, f"Missing expected config path key: {key}"
        item = paths_inv[key]
        assert "name" in item
        assert "description" in item
        assert "path" in item
        assert "exists" in item
        assert "type" in item
        assert isinstance(item["path"], str)


def test_storage_inventory(test_db_session):
    """Verify storage inventory reporting on directories and database records."""
    inv = ResetService.get_storage_inventory(test_db_session)
    assert "storage" in inv
    assert "database" in inv
    assert "total_storage_mb" in inv

    # Storage section
    assert "downloads" in inv["storage"]
    assert "outputs" in inv["storage"]
    assert "temp" in inv["storage"]
    for s_key in ["downloads", "outputs", "temp"]:
        assert "file_count" in inv["storage"][s_key]
        assert "size_mb" in inv["storage"][s_key]

    # Database section
    db_inv = inv["database"]
    assert "users" in db_inv
    assert db_inv["users"] == 1  # our seeded admin
    assert "channels" in db_inv
    assert "source_videos" in db_inv
    assert "clips" in db_inv
    assert "total_records" in db_inv


def test_granular_reset_temp_files_only(test_db_session, tmp_path, monkeypatch):
    """Verify that resetting temp files removes scratch files and leaves directory intact."""
    test_temp = tmp_path / "temp_scratch"
    test_temp.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "TEMP_DIR", test_temp)

    # Create dummy temp files
    f1 = test_temp / "sample1.wav"
    f2 = test_temp / "sample2.ass"
    f1.write_text("dummy audio")
    f2.write_text("dummy ass")
    assert f1.exists() and f2.exists()

    admin_user = test_db_session.query(User).filter_by(username="admin").first()
    opts = ResetOptions(delete_temp_files=True)
    res = ResetService.execute_granular_reset(opts, test_db_session, admin_user)

    assert res["status"] == "success"
    assert "temp_files" in res["deleted_components"]
    assert res["total_files_deleted"] >= 2
    # Verify files are deleted and directory exists (self-healing)
    assert not f1.exists()
    assert not f2.exists()
    assert test_temp.exists()


def test_granular_reset_cascade_order(test_db_session):
    """Verify safe cascade deletion of children (PublishLogs, Clips) before parents."""
    # Seed full hierarchy
    ch = Channel(name="Test Channel", youtube_channel_id="UC1234567890123456789012")
    test_db_session.add(ch)
    test_db_session.flush()

    sv = SourceVideo(channel_id=ch.id, youtube_video_id="vid123", title="Video 1")
    test_db_session.add(sv)
    test_db_session.flush()

    tr = Transcript(source_video_id=sv.id, word_level_json="[]", language="en")
    test_db_session.add(tr)
    test_db_session.flush()

    cand = ClipCandidate(
        source_video_id=sv.id,
        start_ms=10000,
        end_ms=25000,
        score=0.85,
        reason="Good hook",
        hook_text="Hook"
    )
    test_db_session.add(cand)
    test_db_session.flush()

    cl = Clip(clip_candidate_id=cand.id, status=ClipStatus.APPROVED)
    test_db_session.add(cl)
    test_db_session.flush()

    pl = PublishLog(clip_id=cl.id, title="Short 1", description="Viral Short", status="SUCCESS")
    test_db_session.add(pl)
    test_db_session.flush()

    ppl = PlatformPublishLog(clip_id=cl.id, platform="youtube", status="SUCCESS")
    test_db_session.add(ppl)
    test_db_session.commit()

    admin_user = test_db_session.query(User).filter_by(username="admin").first()

    # 1. Delete clips & candidates only
    opts = ResetOptions(delete_clips_and_candidates=True, delete_publishing_logs=True)
    res = ResetService.execute_granular_reset(opts, test_db_session, admin_user)

    assert res["status"] == "success"
    assert test_db_session.query(PlatformPublishLog).count() == 0
    assert test_db_session.query(PublishLog).count() == 0
    assert test_db_session.query(Clip).count() == 0
    assert test_db_session.query(ClipCandidate).count() == 0
    # Channel and SourceVideo remain intact
    assert test_db_session.query(SourceVideo).count() == 1
    assert test_db_session.query(Channel).count() == 1


def test_granular_reset_preserve_admin(test_db_session):
    """Verify that deleting users with preserve_admin=True keeps the acting admin account."""
    extra_user = User(username="operator1", role=UserRole.OPERATOR, password_hash="pwd")
    test_db_session.add(extra_user)
    test_db_session.commit()
    assert test_db_session.query(User).count() == 2

    admin_user = test_db_session.query(User).filter_by(username="admin").first()
    opts = ResetOptions(delete_users=True, preserve_admin=True)
    ResetService.execute_granular_reset(opts, test_db_session, admin_user)

    users = test_db_session.query(User).all()
    assert len(users) == 1
    assert users[0].username == "admin"


def test_granular_reset_reseeds_admin(test_db_session):
    """Verify that deleting users with preserve_admin=False automatically re-seeds default admin."""
    admin_user = test_db_session.query(User).filter_by(username="admin").first()
    opts = ResetOptions(delete_users=True, preserve_admin=False)
    res = ResetService.execute_granular_reset(opts, test_db_session, admin_user)

    assert "reseeding_default_admin" in res["details"]
    users = test_db_session.query(User).all()
    assert len(users) == 1
    assert users[0].username == "admin"
    assert users[0].role == UserRole.ADMIN or str(users[0].role) == "admin"


def test_api_reset_endpoints_security(monkeypatch):
    """Verify that the FastAPI reset endpoint enforces safety code for total wipes."""
    from fastapi.testclient import TestClient
    from shorts_engine.dashboard.app import app
    from shorts_engine.dashboard.auth import require_operator, require_viewer

    # Mock user dependency
    dummy_admin = User(id="admin_id", username="admin", role=UserRole.ADMIN, password_hash="dummy")
    app.dependency_overrides[require_operator] = lambda: dummy_admin
    app.dependency_overrides[require_viewer] = lambda: dummy_admin

    client = TestClient(app)

    # 1. Config paths endpoint
    r_paths = client.get("/api/settings/config-paths")
    assert r_paths.status_code == 200
    assert "sqlite_database" in r_paths.json()

    # 2. Storage inventory endpoint
    r_inv = client.get("/api/settings/storage-inventory")
    assert r_inv.status_code == 200
    assert "storage" in r_inv.json()
    assert "database" in r_inv.json()

    # 3. Total wipe rejected without confirmation code
    r_wipe_fail = client.post("/api/settings/reset", json={
        "is_total_wipe": True,
        "delete_raw_downloads": True,
        "delete_rendered_outputs": True,
        "confirm_code": "INVALID_CODE"
    })
    assert r_wipe_fail.status_code == 400
    assert "Safety confirmation" in r_wipe_fail.json()["detail"]

    # 4. Config export and import test
    r_export = client.get("/api/settings/export")
    assert r_export.status_code == 200
    export_json = r_export.json()
    assert "TARGET_DURATION_MAX_SEC" in export_json

    # Test restoring configuration via import
    r_import = client.post("/api/settings/import", json={
        "settings": {
            "TARGET_DURATION_MAX_SEC": 55,
            "MAX_CLIPS_PER_VIDEO": 4,
        }
    })
    assert r_import.status_code == 200
    assert r_import.json()["status"] == "success"

    # Clean up overrides
    app.dependency_overrides.clear()
