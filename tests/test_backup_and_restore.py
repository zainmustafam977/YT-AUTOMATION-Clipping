"""
Unit & Integration Tests for Backup & Restore System:
- Channel export and upsert restore with zero-crash guarantee.
- Dual-mode API Keys export and restore (Plaintext vs PBKDF2-HMAC-SHA256 + AES-256-GCM Encrypted).
- Full System Snapshot export and restore.
- FastAPI REST endpoints for channels, API keys, and system snapshots.
"""

import json
import hashlib
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.db.models import Channel, ApiKey, User, UserRole
from shorts_engine.config.settings import settings
from shorts_engine.services.storage.backup_service import BackupService
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.routes import channels as channels_route
from shorts_engine.dashboard.auth import get_current_user, require_operator, require_viewer, require_admin


def _create_test_client_and_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = create_dashboard_app()
    app.dependency_overrides[channels_route.get_db] = override_get_db

    test_operator = User(
        id="test-operator-id",
        username="admin_test",
        password_hash="fakehash",
        role=UserRole.ADMIN,
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: test_operator
    app.dependency_overrides[require_viewer] = lambda: test_operator
    app.dependency_overrides[require_operator] = lambda: test_operator
    app.dependency_overrides[require_admin] = lambda: test_operator

    client = TestClient(app, raise_server_exceptions=True)
    db = TestSession()
    return client, db


@pytest.fixture(autouse=True)
def preserve_runtime_and_keys():
    """Preserves original keys and restores runtime_settings.json after each test."""
    orig_groq = list(settings.get_groq_keys())
    orig_gemini = list(settings.get_gemini_keys())
    orig_openrouter = list(settings.get_openrouter_keys())
    orig_openai = list(settings.get_openai_keys())

    runtime_path = settings.STORAGE_DIR / "config" / "runtime_settings.json"
    orig_runtime_content = runtime_path.read_text(encoding="utf-8") if runtime_path.exists() else None

    yield

    # Restore settings
    settings.GROQ_API_KEYS = orig_groq
    settings.GROQ_API_KEY = orig_groq[0] if orig_groq else None
    settings.GEMINI_API_KEYS = orig_gemini
    settings.GEMINI_API_KEY = orig_gemini[0] if orig_gemini else None
    settings.OPENROUTER_API_KEYS = orig_openrouter
    settings.OPENROUTER_API_KEY = orig_openrouter[0] if orig_openrouter else None
    settings.OPENAI_API_KEYS = orig_openai
    settings.OPENAI_API_KEY = orig_openai[0] if orig_openai else None

    if orig_runtime_content is not None:
        runtime_path.parent.mkdir(parents=True, exist_ok=True)
        runtime_path.write_text(orig_runtime_content, encoding="utf-8")


# =========================================================================
# 1. Channels Export & Restore Tests
# =========================================================================

def test_channels_export_and_import_upsert():
    """Verify channels export properly and import updates existing channels without crashing."""
    _, db = _create_test_client_and_db()

    # Seed 2 channels
    ch1 = Channel(
        youtube_channel_id="UC_alpha_1111111111111",
        name="Alpha Channel",
        watch_enabled=True,
        human_review_required=True,
        upload_defaults={"privacy_status": "unlisted", "daily_upload_quota": 4},
    )
    ch2 = Channel(
        youtube_channel_id="UC_beta_22222222222222",
        name="Beta Channel",
        watch_enabled=False,
        human_review_required=False,
        upload_defaults={"privacy_status": "public", "daily_upload_quota": 2},
    )
    db.add_all([ch1, ch2])
    db.commit()

    # 1. Export
    export_payload = BackupService.export_channels(db)
    assert export_payload["version"] == 1
    assert export_payload["type"] == "channels_backup"
    assert export_payload["count"] == 2
    assert len(export_payload["channels"]) == 2

    # 2. Modify one channel in payload and add a third channel
    export_payload["channels"][0]["name"] = "Alpha Channel Renamed"
    export_payload["channels"][0]["upload_defaults"]["daily_upload_quota"] = 8
    export_payload["channels"].append({
        "youtube_channel_id": "UC_gamma_333333333333",
        "name": "Gamma Channel",
        "watch_enabled": True,
        "human_review_required": False,
        "upload_defaults": {"privacy_status": "private"},
    })

    # 3. Import with update_existing=True
    res = BackupService.import_channels(export_payload, db, update_existing=True)
    assert res["status"] == "success"
    assert res["imported"] == 1  # Gamma
    assert res["updated"] == 2   # Alpha + Beta
    assert res["skipped"] == 0

    # Verify DB state
    updated_alpha = db.query(Channel).filter_by(youtube_channel_id="UC_alpha_1111111111111").first()
    assert updated_alpha is not None
    assert updated_alpha.name == "Alpha Channel Renamed"
    assert updated_alpha.upload_defaults.get("daily_upload_quota") == 8

    gamma = db.query(Channel).filter_by(youtube_channel_id="UC_gamma_333333333333").first()
    assert gamma is not None
    assert gamma.name == "Gamma Channel"


def test_channels_import_graceful_resilience():
    """Verify malformed channels are skipped without crashing or breaking the transaction."""
    _, db = _create_test_client_and_db()

    malformed_payload = {
        "version": 1,
        "channels": [
            {"name": "Missing ID Channel"},  # Missing youtube_channel_id
            "not a dict object",             # Bad type
            {"youtube_channel_id": "UC_valid_channel_44444444", "name": "Valid Channel"},
        ]
    }

    res = BackupService.import_channels(malformed_payload, db)
    assert res["status"] == "success"
    assert res["imported"] == 1
    assert res["skipped"] == 2
    assert len(res["errors"]) == 2

    valid = db.query(Channel).filter_by(youtube_channel_id="UC_valid_channel_44444444").first()
    assert valid is not None
    assert valid.name == "Valid Channel"


# =========================================================================
# 2. API Keys Backup & Restore (Plaintext vs Encrypted)
# =========================================================================

def test_api_keys_plaintext_export_and_import():
    """Verify plaintext export and restore correctly populates runtime settings and DB vault."""
    _, db = _create_test_client_and_db()

    test_key = "gsk_testkey_groq_1234567890abcdef"
    settings.add_api_key("groq", test_key)

    # 1. Plaintext Export
    export_data = BackupService.export_api_keys(mode="plaintext", db=db)
    assert export_data["type"] == "api_keys_plaintext"
    assert export_data["count"] >= 1
    found = any(k["key"] == test_key and k["provider"] == "groq" for k in export_data["keys"])
    assert found

    # 2. Remove key from runtime
    import hashlib
    key_hash = hashlib.sha256(test_key.encode("utf-8")).hexdigest()[:16]
    settings.remove_api_key("groq", key_hash)
    assert test_key not in settings.get_groq_keys()

    # 3. Import back
    import_res = BackupService.import_api_keys(export_data, db=db)
    assert import_res["status"] == "success"
    assert import_res["imported"] >= 1
    assert test_key in settings.get_groq_keys()

    # Verify DB persistence
    db_keys = db.query(ApiKey).filter_by(provider="groq").all()
    assert len(db_keys) >= 1


def test_api_keys_encrypted_export_and_import():
    """Verify AES-256-GCM password-protected encryption and decryption with PBKDF2HMAC."""
    _, db = _create_test_client_and_db()

    secret_key = "AIzaSySecretGeminiKeyForTesting99"
    settings.add_api_key("gemini", secret_key)
    passphrase = "CorrectHorseBatteryStaple2026!"

    # 1. Encrypted Export
    enc_export = BackupService.export_api_keys(mode="encrypted", passphrase=passphrase, db=db)
    assert enc_export["type"] == "api_keys_encrypted"
    assert "ciphertext_b64" in enc_export
    assert "salt_b64" in enc_export
    assert "nonce_b64" in enc_export
    # Ensure plaintext key is NOT leaked in the encrypted dictionary
    assert secret_key not in json.dumps(enc_export)

    # 2. Attempt import with wrong passphrase -> must raise ValueError
    with pytest.raises(ValueError, match="Decryption failed"):
        BackupService.import_api_keys(enc_export, passphrase="WrongPassword123", db=db)

    # 3. Attempt import with missing passphrase -> must raise ValueError
    with pytest.raises(ValueError, match="Passphrase is required"):
        BackupService.import_api_keys(enc_export, passphrase=None, db=db)

    # 4. Remove key from runtime settings
    sec_hash = hashlib.sha256(secret_key.encode("utf-8")).hexdigest()[:16]
    settings.remove_api_key("gemini", sec_hash)
    assert secret_key not in settings.get_gemini_keys()

    # 5. Restore with correct passphrase
    restore_res = BackupService.import_api_keys(enc_export, passphrase=passphrase, db=db)
    assert restore_res["status"] == "success"
    assert secret_key in settings.get_gemini_keys()


# =========================================================================
# 3. Full System Snapshot Roundtrip
# =========================================================================

def test_full_system_snapshot_roundtrip():
    """Verify full system snapshot packages settings, channels, and keys in one archive."""
    _, db = _create_test_client_and_db()

    # Setup state
    ch = Channel(youtube_channel_id="UC_snapshot_test_5555", name="Snapshot Channel")
    db.add(ch)
    db.commit()

    settings.add_api_key("groq", "gsk_snap_test_key_001")

    # Export Full Snapshot
    snapshot = BackupService.export_full_system_snapshot(db, mode="plaintext")
    assert snapshot["type"] == "full_system_snapshot"
    assert "settings" in snapshot
    assert "channels" in snapshot
    assert "keys_archive" in snapshot
    assert any(c["youtube_channel_id"] == "UC_snapshot_test_5555" for c in snapshot["channels"])

    # Mutate state
    ch.name = "Mutated Channel"
    db.commit()

    # Restore Snapshot
    res = BackupService.import_full_system_snapshot(snapshot, db=db)
    assert res["status"] == "success"
    assert res["settings_restored"] is True
    assert res["channels"]["updated"] >= 1

    # Verify channel restored
    restored_ch = db.query(Channel).filter_by(youtube_channel_id="UC_snapshot_test_5555").first()
    assert restored_ch.name == "Snapshot Channel"


# =========================================================================
# 4. REST Endpoints Tests
# =========================================================================

def test_channels_api_endpoints():
    """Test /api/channels/export and /api/channels/import HTTP endpoints."""
    client, db = _create_test_client_and_db()

    ch = Channel(youtube_channel_id="UC_api_test_6666666", name="API Test Channel")
    db.add(ch)
    db.commit()

    # 1. Export endpoint
    resp = client.get("/api/channels/export")
    assert resp.status_code == 200
    assert "attachment; filename=\"channels_backup.json\"" in resp.headers.get("content-disposition", "")
    data = resp.json()
    assert data["type"] == "channels_backup"

    # 2. Import endpoint
    import_body = {
        "channels": [
            {"youtube_channel_id": "UC_api_test_7777777", "name": "Imported Channel"}
        ],
        "update_existing": True
    }
    resp2 = client.post("/api/channels/import", json=import_body)
    assert resp2.status_code == 200
    res_data = resp2.json()
    assert res_data["imported"] == 1


def test_settings_keys_and_snapshot_api_endpoints():
    """Test API Keys and Snapshot export/import HTTP endpoints."""
    client, db = _create_test_client_and_db()

    settings.add_api_key("openai", "sk-proj-testkeyforhttpexport123")

    # 1. Plaintext Keys Export
    r1 = client.get("/api/settings/keys/export?mode=plaintext")
    assert r1.status_code == 200
    assert r1.json()["type"] == "api_keys_plaintext"

    # 2. Encrypted Keys Export
    r2 = client.get("/api/settings/keys/export?mode=encrypted&passphrase=TestingPassphrase123")
    assert r2.status_code == 200
    enc_data = r2.json()
    assert enc_data["type"] == "api_keys_encrypted"

    # 3. Keys Import API
    r3 = client.post("/api/settings/keys/import", json={
        "payload": enc_data,
        "passphrase": "TestingPassphrase123"
    })
    assert r3.status_code == 200
    assert r3.json()["status"] == "success"

    # 4. Snapshot Export
    r4 = client.get("/api/settings/snapshot/export?mode=plaintext")
    assert r4.status_code == 200
    snap_data = r4.json()
    assert snap_data["type"] == "full_system_snapshot"

    # 5. Snapshot Import API
    r5 = client.post("/api/settings/snapshot/import", json={
        "payload": snap_data
    })
    assert r5.status_code == 200
    assert r5.json()["status"] == "success"
