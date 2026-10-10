"""
Unit and integration tests for YouTube RFC-8628 Device OAuth2 Flow & Encrypted Token Vault.
Verifies:
1. Google Device Code OAuth2 initiation.
2. Non-blocking token polling with RFC-8628 error handling (pending, success).
3. AES-256-GCM token encryption, persistence, and secure round-trip decryption.
4. Autonomous token refresh when approaching expiration.
5. Token revocation and clean vault purging.
"""

import time
import json
import pytest
from unittest.mock import patch, MagicMock

from shorts_engine.services.ingestion.oauth_flow import (
    YouTubeDeviceOAuth,
    GOOGLE_DEVICE_CODE_URL,
    GOOGLE_REVOKE_URL,
)


def test_initiate_device_auth_success():
    """Initiates device authorization flow and receives user/device codes."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "device_code": "dev_code_12345",
        "user_code": "ABCD-EFGH",
        "verification_url": "https://www.google.com/device",
        "expires_in": 1800,
        "interval": 5,
    }

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        result = YouTubeDeviceOAuth.initiate_device_auth(client_id="test_client_id")

        assert result["device_code"] == "dev_code_12345"
        assert result["user_code"] == "ABCD-EFGH"
        assert result["verification_url"] == "https://www.google.com/device"
        assert result["expires_in"] == 1800
        assert result["interval"] == 5
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == GOOGLE_DEVICE_CODE_URL
        assert call_args[1]["data"]["client_id"] == "test_client_id"


def test_initiate_device_auth_failure():
    """Fails cleanly when Google Device Code endpoint returns an error."""
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.text = "invalid_scope"

    with patch("httpx.Client.post", return_value=mock_resp):
        with pytest.raises(RuntimeError) as exc_info:
            YouTubeDeviceOAuth.initiate_device_auth(client_id="test_client_id")
        assert "Google OAuth initiation failed (400)" in str(exc_info.value)


def test_poll_device_token_pending(tmp_path):
    """Returns 'pending' status when user has not yet entered code."""
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json.return_value = {"error": "authorization_pending"}

    token_file = tmp_path / "youtube_oauth.json"
    with patch("httpx.Client.post", return_value=mock_resp):
        res = YouTubeDeviceOAuth.poll_device_token("dev_code_12345", token_path=token_file)
        assert res["status"] == "pending"
        assert "Waiting for user" in res["message"]


def test_poll_device_token_success(tmp_path):
    """Saves encrypted token vault when authorization completes successfully."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "access_token": "ya29.sample_access_token_12345",
        "refresh_token": "1//sample_refresh_token_67890",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": "https://www.googleapis.com/auth/youtube.readonly",
    }

    token_file = tmp_path / "youtube_oauth.json"
    with patch("httpx.Client.post", return_value=mock_resp):
        res = YouTubeDeviceOAuth.poll_device_token("dev_code_12345", token_path=token_file)

        assert res["status"] == "success"
        assert token_file.exists()

        # Vault file is encrypted JSON and does not leak raw secret in plaintext
        content = token_file.read_text(encoding="utf-8")
        parsed = json.loads(content)
        assert "encrypted_data" in parsed
        assert "ya29.sample_access_token_12345" not in content

        # Decrypts cleanly via load_encrypted_token
        loaded = YouTubeDeviceOAuth.load_encrypted_token(token_path=token_file)
        assert loaded is not None
        assert loaded["access_token"] == "ya29.sample_access_token_12345"
        assert loaded["refresh_token"] == "1//sample_refresh_token_67890"


def test_save_and_load_encrypted_token_roundtrip(tmp_path):
    """Verifies AES-256-GCM encryption round-trip."""
    token_file = tmp_path / "token_vault.json"
    tokens = {
        "access_token": "secret_access_token",
        "refresh_token": "secret_refresh_token",
        "expires_at": int(time.time()) + 3600,
        "client_id": "test_client",
    }

    YouTubeDeviceOAuth.save_encrypted_token(tokens, token_path=token_file)
    assert token_file.exists()

    decrypted = YouTubeDeviceOAuth.load_encrypted_token(token_path=token_file)
    assert decrypted is not None
    assert decrypted["access_token"] == "secret_access_token"
    assert decrypted["refresh_token"] == "secret_refresh_token"
    assert decrypted["client_id"] == "test_client"


def test_refresh_access_token_success(tmp_path):
    """Refreshes expired access token using refresh_token and updates vault."""
    token_file = tmp_path / "token_vault.json"
    initial_tokens = {
        "access_token": "old_expired_token",
        "refresh_token": "valid_refresh_token_123",
        "expires_at": int(time.time()) - 100,
        "client_id": "test_client",
    }
    YouTubeDeviceOAuth.save_encrypted_token(initial_tokens, token_path=token_file)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "access_token": "fresh_new_access_token_999",
        "expires_in": 3600,
        "token_type": "Bearer",
    }

    with patch("httpx.Client.post", return_value=mock_resp):
        refreshed = YouTubeDeviceOAuth.refresh_access_token(initial_tokens, token_path=token_file)
        assert refreshed is not None
        assert refreshed["access_token"] == "fresh_new_access_token_999"

        # Verify disk vault was updated
        on_disk = YouTubeDeviceOAuth.load_encrypted_token(token_path=token_file)
        assert on_disk["access_token"] == "fresh_new_access_token_999"


def test_get_valid_access_token_auto_refresh(tmp_path):
    """Automatically triggers token refresh when token is expiring soon."""
    token_file = tmp_path / "token_vault.json"
    expiring_tokens = {
        "access_token": "near_expiry_token",
        "refresh_token": "valid_refresh_token_123",
        "expires_at": int(time.time()) + 60,  # Within 300s window
        "client_id": "test_client",
    }
    YouTubeDeviceOAuth.save_encrypted_token(expiring_tokens, token_path=token_file)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "access_token": "auto_refreshed_access_token",
        "expires_in": 3600,
    }

    with patch("httpx.Client.post", return_value=mock_resp):
        token = YouTubeDeviceOAuth.get_valid_access_token(token_path=token_file)
        assert token == "auto_refreshed_access_token"


def test_revoke_token(tmp_path):
    """Revokes active token with Google and purges local encrypted vault file."""
    token_file = tmp_path / "token_vault.json"
    tokens = {
        "access_token": "token_to_revoke",
        "refresh_token": "refresh_to_revoke",
        "expires_at": int(time.time()) + 3600,
    }
    YouTubeDeviceOAuth.save_encrypted_token(tokens, token_path=token_file)
    assert token_file.exists()

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        success = YouTubeDeviceOAuth.revoke_token(token_path=token_file)
        assert success is True
        assert not token_file.exists(), "Revoked token vault file must be unlinked"
        mock_post.assert_called_once()
        assert mock_post.call_args[0][0] == GOOGLE_REVOKE_URL
