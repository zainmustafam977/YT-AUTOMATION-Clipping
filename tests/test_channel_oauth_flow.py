"""
Unit and Integration Tests for YouTube Channel OAuth 2.0 Flow (Task 2).
Verifies:
1. Client secrets loading from storage/client_secret.json.
2. Authorization URL generation with offline refresh token & consent prompt.
3. OAuth code exchange and AES-256-GCM encryption.
4. Channel token binding and token preservation.
5. Live token verification via YouTube Data API (mocked & error handling).
6. OAuth API endpoints (/oauth2callback, /oauth/start, /oauth/exchange, /oauth/status, /oauth/revoke).
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from shorts_engine.services.publishing.oauth_manager import ChannelOAuthManager
from shorts_engine.db.models import Channel, User, UserRole
from shorts_engine.db.security import decrypt_secret
from shorts_engine.dashboard.app import app
from shorts_engine.dashboard.routes.queue import get_db
from shorts_engine.dashboard.auth import require_operator, require_viewer


def test_load_client_secrets_from_storage():
    """Verifies that client_secret.json in storage/ loads successfully and contains valid client_id."""
    mgr = ChannelOAuthManager()
    cfg = mgr.get_client_config()
    assert "client_id" in cfg
    assert "746579743277" in cfg["client_id"]
    assert "client_secret" in cfg


def test_missing_client_secrets_raises_filenotfound():
    """Verifies that a missing client secrets file raises FileNotFoundError."""
    mgr = ChannelOAuthManager(client_secrets_file=Path("non_existent_client_secret.json"))
    with pytest.raises(FileNotFoundError):
        mgr.load_client_secrets()


def test_get_authorization_url():
    """Verifies generation of Google OAuth 2.0 authorization URL with correct parameters."""
    mgr = ChannelOAuthManager()
    channel_id = "test-channel-uuid-123"
    auth_url = mgr.get_authorization_url(channel_id=channel_id, redirect_uri="http://127.0.0.1:8000/oauth2callback")

    assert "accounts.google.com" in auth_url
    assert "access_type=offline" in auth_url
    assert "prompt=consent" in auth_url
    assert f"state={channel_id}" in auth_url
    assert "youtube.upload" in auth_url


def test_exchange_code_and_bind_to_channel():
    """Verifies code exchange and AES-256-GCM encrypted binding to Channel model."""
    mgr = ChannelOAuthManager()

    mock_creds = MagicMock()
    mock_creds.client_id = "test-client-id"
    mock_creds.client_secret = "test-client-secret"
    mock_creds.refresh_token = "1//mock-refresh-token-xyz"
    mock_creds.token = "mock-access-token-abc"
    mock_creds.token_uri = "https://oauth2.googleapis.com/token"
    mock_creds.scopes = ["https://www.googleapis.com/auth/youtube.upload"]

    with patch("google_auth_oauthlib.flow.Flow.fetch_token"):
        with patch("google_auth_oauthlib.flow.Flow.credentials", new=mock_creds):
            # Test full callback URL parsing as code input
            raw_url = "http://127.0.0.1:8000/oauth2callback?code=4/0A_sample_code_123&state=chan1"
            tokens = mgr.exchange_code_for_tokens(code=raw_url)

            assert tokens["refresh_token"] == "1//mock-refresh-token-xyz"
            assert tokens["access_token"] == "mock-access-token-abc"

            mock_channel = Channel(
                id="chan1",
                name="Test Debateks",
                youtube_channel_id="UCBWVd_s5Gb6silTdeYaMqvA",
            )
            mock_db = MagicMock()
            mock_db.query.return_value.filter_by.return_value.first.return_value = mock_channel

            mgr.bind_token_to_channel("chan1", tokens, mock_db)

            assert mock_channel.encrypted_oauth_token is not None
            # Verify decryption works cleanly
            decrypted = decrypt_secret(mock_channel.encrypted_oauth_token)
            data = json.loads(decrypted)
            assert data["refresh_token"] == "1//mock-refresh-token-xyz"


def test_verify_channel_token_unconfigured_and_invalid():
    """Verifies error reporting for channels with missing or invalid tokens."""
    mgr = ChannelOAuthManager()

    # 1. No token
    ch_empty = Channel(id="c1", name="Empty", youtube_channel_id="UC1")
    res1 = mgr.verify_channel_token(ch_empty)
    assert res1["connected"] is False
    assert "No OAuth" in res1["error"]

    # 2. Corrupted token (like the raw client secret previously stored)
    from shorts_engine.db.security import encrypt_secret
    ch_raw = Channel(
        id="c2",
        name="Debateks",
        youtube_channel_id="UCBWVd_s5Gb6silTdeYaMqvA",
        encrypted_oauth_token=encrypt_secret("GOCSPX-raw-secret-not-json"),
    )
    res2 = mgr.verify_channel_token(ch_raw)
    assert res2["connected"] is False
    assert "valid JSON" in res2["error"]


def test_verify_channel_token_success():
    """Verifies live token verification returns channel title, avatar, and subscriber count."""
    mgr = ChannelOAuthManager()
    from shorts_engine.db.security import encrypt_secret

    token_blob = json.dumps({
        "client_id": "test_id",
        "client_secret": "test_secret",
        "refresh_token": "1//test_refresh",
        "access_token": "test_access",
    })
    ch = Channel(
        id="c3",
        name="Debateks",
        youtube_channel_id="UCBWVd_s5Gb6silTdeYaMqvA",
        encrypted_oauth_token=encrypt_secret(token_blob),
    )

    mock_yt = MagicMock()
    mock_yt.channels().list.return_value.execute.return_value = {
        "items": [{
            "id": "UCBWVd_s5Gb6silTdeYaMqvA",
            "snippet": {
                "title": "Debateks Live",
                "customUrl": "@Debateks",
                "thumbnails": {"default": {"url": "https://yt.com/avatar.jpg"}},
            },
            "statistics": {
                "subscriberCount": "15200",
                "videoCount": "42",
                "viewCount": "980000",
            },
        }]
    }

    with patch("googleapiclient.discovery.build", return_value=mock_yt):
        res = mgr.verify_channel_token(ch)
        assert res["connected"] is True
        assert res["title"] == "Debateks Live"
        assert res["subscriber_count"] == "15200"


def test_oauth_api_endpoints():
    """Verifies FastAPI OAuth endpoints: /oauth/start, /oauth/exchange, /oauth/status, /oauth/revoke, /oauth2callback."""
    mock_channel = Channel(
        id="chan_test_99",
        name="Debateks",
        youtube_channel_id="UCBWVd_s5Gb6silTdeYaMqvA",
    )
    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.first.return_value = mock_channel

    fake_user = User(id="u1", username="admin_operator", role=UserRole.ADMIN)

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[require_operator] = lambda: fake_user
    app.dependency_overrides[require_viewer] = lambda: fake_user

    client = TestClient(app)

    try:
        # 1. /api/channels/{id}/oauth/start
        res_start = client.get(f"/api/channels/{mock_channel.id}/oauth/start")
        assert res_start.status_code == 200
        assert "authorization_url" in res_start.json()
        assert "accounts.google.com" in res_start.json()["authorization_url"]

        # 2. /api/channels/{id}/oauth/exchange
        with patch.object(ChannelOAuthManager, "exchange_code_for_tokens") as mock_exchange:
            mock_exchange.return_value = {
                "client_id": "cid",
                "client_secret": "csec",
                "refresh_token": "1//refresh",
                "access_token": "access",
            }
            res_ex = client.post(
                f"/api/channels/{mock_channel.id}/oauth/exchange",
                json={"code": "4/0A_test_code_xyz"},
            )
            assert res_ex.status_code == 200
            assert res_ex.json()["status"] == "success"

        # 3. /api/channels/{id}/oauth/status
        with patch.object(ChannelOAuthManager, "verify_channel_token") as mock_verify:
            mock_verify.return_value = {"connected": True, "title": "Debateks", "subscriber_count": "100"}
            res_status = client.get(f"/api/channels/{mock_channel.id}/oauth/status")
            assert res_status.status_code == 200
            assert res_status.json()["connected"] is True

        # 4. /api/channels/{id}/oauth/revoke
        res_revoke = client.post(f"/api/channels/{mock_channel.id}/oauth/revoke")
        assert res_revoke.status_code == 200
        assert res_revoke.json()["status"] == "revoked"

        # 5. /oauth2callback 1-click redirect
        with patch.object(ChannelOAuthManager, "exchange_code_for_tokens") as mock_cb_exchange:
            mock_cb_exchange.return_value = {
                "client_id": "cid",
                "client_secret": "csec",
                "refresh_token": "1//refresh",
                "access_token": "access",
            }
            res_cb = client.get(
                "/oauth2callback?code=4/0A_good_code&state=chan_test_99",
                follow_redirects=False,
            )
            assert res_cb.status_code == 303
            assert "/queue" in res_cb.headers["location"]
            assert "studio=open" in res_cb.headers["location"]
            assert "success" in res_cb.headers["location"]

    finally:
        app.dependency_overrides.clear()
