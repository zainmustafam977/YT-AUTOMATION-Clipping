from unittest.mock import patch, MagicMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    Channel,
    ChannelCredential,
    OAuthState,
    PendingOAuthMismatch,
    PublishJob,
    PublishJobStatus,
    QuotaLedger,
    AuditLog,
)
from shorts_engine.services.publishing.oauth_manager import ChannelOAuthManager
from shorts_engine.db.security import encrypt_secret, decrypt_secret


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_oauth_state_opaque_single_use_pkce(test_db):
    mgr = ChannelOAuthManager()
    res = mgr.start_connect(intended_channel_id="chan_intended", expected_handle="@test", db=test_db)
    state = res["state"]
    assert len(state) >= 32
    assert "chan_intended" not in state  # State carries no channel data

    # Verify oauth_states row created
    state_row = test_db.query(OAuthState).filter_by(state=state).first()
    assert state_row is not None
    assert state_row.intended_channel_id == "chan_intended"
    assert state_row.expected_handle == "@test"
    assert state_row.pkce_verifier is not None


def test_oauth_state_reuse_fails(test_db):
    mgr = ChannelOAuthManager()
    res = mgr.start_connect(intended_channel_id="chan_intended", db=test_db)
    state = res["state"]

    mock_tokens = {
        "client_id": "cid",
        "client_secret": "csec",
        "refresh_token": "1//refresh_abc",
        "access_token": "acc_123",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/youtube.upload"],
    }
    mock_identity = {
        "channel_id": "chan_intended",
        "title": "Intended Channel",
        "handle": "@intended",
        "avatar_url": "http://avatar",
    }

    with patch.object(mgr, "exchange_code_for_tokens", return_value=mock_tokens), \
         patch.object(mgr, "fetch_channel_identity", return_value=mock_identity):

        # First consumption succeeds
        cb1 = mgr.handle_callback(code="code_123", state=state, db=test_db)
        assert cb1["status"] == "success"

        # State row was consumed and deleted
        assert test_db.query(OAuthState).filter_by(state=state).first() is None

        # Reusing the same state fails
        cb2 = mgr.handle_callback(code="code_123", state=state, db=test_db)
        assert cb2["status"] == "error"
        assert "invalid" in cb2["error"] or "expired" in cb2["error"]


def test_oauth_missing_refresh_token_fails_closed(test_db):
    mgr = ChannelOAuthManager()
    res = mgr.start_connect(intended_channel_id="chan_intended", db=test_db)
    state = res["state"]

    mock_tokens_no_refresh = {
        "client_id": "cid",
        "client_secret": "csec",
        "refresh_token": None,
        "access_token": "acc_123",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/youtube.upload"],
    }

    with patch.object(mgr, "exchange_code_for_tokens", return_value=mock_tokens_no_refresh):
        cb = mgr.handle_callback(code="code_123", state=state, db=test_db)
        assert cb["status"] == "error"
        assert cb["error"] == "missing_refresh_token"

        # Verify no credential saved
        assert test_db.query(ChannelCredential).count() == 0

        # Verify audit log created
        audit = test_db.query(AuditLog).filter_by(event="oauth_failed_no_refresh_token").first()
        assert audit is not None


def test_oauth_mismatch_creates_dialog_and_does_not_rebind(test_db):
    mgr = ChannelOAuthManager()

    # Pre-existing channel A
    chan_a = Channel(
        id="channel_a_uuid",
        youtube_channel_id="UC_CHANNEL_A",
        name="Channel A",
        title="Channel A",
    )
    test_db.add(chan_a)
    test_db.commit()

    # Operator intends to reconnect Channel A
    res = mgr.start_connect(intended_channel_id=chan_a.id, db=test_db)
    state = res["state"]

    # Google consent screen returns Channel B instead!
    mock_tokens = {
        "client_id": "cid",
        "client_secret": "csec",
        "refresh_token": "1//refresh_b",
        "access_token": "acc_b",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/youtube.upload"],
    }
    mock_identity_b = {
        "channel_id": "UC_CHANNEL_B",
        "title": "Channel B",
        "handle": "@channel_b",
        "avatar_url": "http://avatar_b",
    }

    with patch.object(mgr, "exchange_code_for_tokens", return_value=mock_tokens), \
         patch.object(mgr, "fetch_channel_identity", return_value=mock_identity_b):

        cb = mgr.handle_callback(code="code_b", state=state, db=test_db)

        # Mismatch detected!
        assert cb["status"] == "mismatch"
        assert "mismatch_id" in cb
        mismatch_id = cb["mismatch_id"]

        # Channel A MUST BE UNTOUCHED!
        test_db.refresh(chan_a)
        assert chan_a.encrypted_oauth_token is None
        assert chan_a.credential is None

        # Verify PendingOAuthMismatch created
        pending = test_db.query(PendingOAuthMismatch).filter_by(id=mismatch_id).first()
        assert pending is not None
        assert pending.authorized_channel_id == "UC_CHANNEL_B"

        # Test operator resolution: Cancel discards token
        res_cancel = mgr.resolve_mismatch(mismatch_id, action="cancel", db=test_db)
        assert res_cancel["ok"] is True
        assert res_cancel.get("cancelled") is True
        assert test_db.query(PendingOAuthMismatch).count() == 0
        assert test_db.query(Channel).filter_by(youtube_channel_id="UC_CHANNEL_B").first() is None


def test_oauth_mismatch_resolve_connect_authorized(test_db):
    mgr = ChannelOAuthManager()

    chan_a = Channel(id="c_a", youtube_channel_id="UC_A", name="Channel A")
    test_db.add(chan_a)
    test_db.commit()

    res = mgr.start_connect(intended_channel_id=chan_a.id, db=test_db)
    state = res["state"]

    mock_tokens = {
        "client_id": "cid",
        "client_secret": "csec",
        "refresh_token": "1//refresh_b",
        "access_token": "acc_b",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/youtube.upload"],
    }
    mock_identity_b = {
        "channel_id": "UC_B",
        "title": "Channel B",
        "handle": "@b",
        "avatar_url": "http://avatar_b",
    }

    with patch.object(mgr, "exchange_code_for_tokens", return_value=mock_tokens), \
         patch.object(mgr, "fetch_channel_identity", return_value=mock_identity_b):

        cb = mgr.handle_callback(code="code_b", state=state, db=test_db)
        mismatch_id = cb["mismatch_id"]

        # Operator decides: "Connect B instead"
        res_resolve = mgr.resolve_mismatch(mismatch_id, action="connect_authorized", db=test_db)
        assert res_resolve["ok"] is True

        # Channel B is now created and active
        chan_b = test_db.query(Channel).filter_by(youtube_channel_id="UC_B").first()
        assert chan_b is not None
        assert chan_b.credential is not None
        assert chan_b.credential.status == "active"
        assert decrypt_secret(chan_b.credential.encrypted_refresh_token) == "1//refresh_b"

        # Channel A remains completely untouched!
        test_db.refresh(chan_a)
        assert chan_a.credential is None


def test_pre_upload_identity_guard_blocks_mismatch(test_db):
    mgr = ChannelOAuthManager()

    # Channel A in DB
    chan_a = Channel(id="c_a", youtube_channel_id="UC_A", name="Channel A")
    test_db.add(chan_a)
    test_db.flush()

    cred_a = ChannelCredential(
        channel_id=chan_a.id,
        encrypted_refresh_token=encrypt_secret("1//refresh_a"),
        status="active",
    )
    test_db.add(cred_a)

    job = PublishJob(
        clip_id="clip_1",
        target_youtube_channel_id="UC_A",
        title="Test Short",
        description="Desc",
        status=PublishJobStatus.DRAFT.value,
    )
    test_db.add(job)
    test_db.commit()

    # Mock YouTube channels.list returning Channel B!
    mock_item = {"id": "UC_B", "snippet": {"title": "Channel B"}}
    mock_service = MagicMock()
    mock_service.channels.return_value.list.return_value.execute.return_value = {"items": [mock_item]}

    with patch("googleapiclient.discovery.build", return_value=mock_service):
        ok, err = mgr.verify_identity_before_upload(job, db=test_db)

        assert ok is False
        assert "Identity mismatch" in err
        assert job.status == PublishJobStatus.PAUSED_NEEDS_ATTENTION.value
        assert cred_a.status == "identity_mismatch"

        # Verify ledger recorded only 1 unit (channels.list), NOT 1600 units!
        ledger_entries = test_db.query(QuotaLedger).all()
        assert len(ledger_entries) == 1
        assert ledger_entries[0].units == 1
        assert ledger_entries[0].operation == "youtube.channels.list"


def test_pre_upload_identity_guard_success(test_db):
    mgr = ChannelOAuthManager()

    chan_a = Channel(id="c_a", youtube_channel_id="UC_A", name="Channel A")
    test_db.add(chan_a)
    test_db.flush()

    cred_a = ChannelCredential(
        channel_id=chan_a.id,
        encrypted_refresh_token=encrypt_secret("1//refresh_a"),
        status="active",
    )
    test_db.add(cred_a)

    job = PublishJob(
        clip_id="clip_1",
        target_youtube_channel_id="UC_A",
        title="Test Short",
        description="Desc",
        status=PublishJobStatus.DRAFT.value,
    )
    test_db.add(job)
    test_db.commit()

    mock_item = {"id": "UC_A", "snippet": {"title": "Channel A"}}
    mock_service = MagicMock()
    mock_service.channels.return_value.list.return_value.execute.return_value = {"items": [mock_item]}

    with patch("googleapiclient.discovery.build", return_value=mock_service):
        ok, err = mgr.verify_identity_before_upload(job, db=test_db)
        assert ok is True
        assert err is None
        assert cred_a.status == "active"


def test_log_scrubbing_no_secrets_in_logs(test_db, caplog):
    """Asserts that client secrets, tokens, and PKCE verifiers are never logged."""
    import logging
    mgr = ChannelOAuthManager()

    sensitive_token = "1//super_secret_refresh_token_XYZ987"
    sensitive_secret = "GOCSPX-super_secret_client_key_123"

    with caplog.at_level(logging.DEBUG):
        ch = Channel(id="c_sec", youtube_channel_id="UC_SEC", name="Sec")
        test_db.add(ch)
        test_db.commit()

        mgr.bind_token_to_channel(
            "c_sec",
            {"refresh_token": sensitive_token, "client_secret": sensitive_secret},
            test_db,
        )

    log_text = caplog.text
    assert sensitive_token not in log_text
    assert sensitive_secret not in log_text
