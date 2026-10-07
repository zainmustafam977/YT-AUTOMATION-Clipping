"""
YouTube RFC-8628 Device Authorization Grant Flow & Encrypted Session Vault.
Implements:
1. Google Device Code OAuth2 initiation (google.com/device).
2. Non-blocking token polling with RFC-8628 error handling (pending, slow_down, expired, denied).
3. AES-256-GCM token encryption and storage in storage/youtube_oauth.json.
4. Autonomous access token refresh using refresh_token when expiration approaches.
"""

import os
import json
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
import httpx

from shorts_engine.config.settings import settings
from shorts_engine.db.security import encrypt_secret, decrypt_secret

logger = logging.getLogger(__name__)

GOOGLE_DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

# Default public Google TV / YouTube client ID if custom credentials not configured
DEFAULT_CLIENT_ID = "861556708465-bhdjh4688b139i06m7ihlh922e9nviha.apps.googleusercontent.com"
DEFAULT_CLIENT_SECRET = ""  # RFC-8628 public client doesn't require client secret
DEFAULT_SCOPES = "https://www.googleapis.com/auth/youtube.readonly"


class YouTubeDeviceOAuth:
    """Manages Google RFC-8628 Device Authorization Grant and AES-256-GCM token vault."""

    @classmethod
    def get_client_credentials(
        cls,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ) -> tuple[str, str]:
        """Resolves active Google OAuth2 client credentials from settings or defaults."""
        cid = client_id or getattr(settings, "YOUTUBE_OAUTH_CLIENT_ID", None) or os.getenv("YOUTUBE_OAUTH_CLIENT_ID") or DEFAULT_CLIENT_ID
        csecret = client_secret or getattr(settings, "YOUTUBE_OAUTH_CLIENT_SECRET", None) or os.getenv("YOUTUBE_OAUTH_CLIENT_SECRET") or DEFAULT_CLIENT_SECRET
        return cid.strip(), csecret.strip()

    @classmethod
    def initiate_device_auth(
        cls,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        scopes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Queries Google OAuth2 Device Code endpoint.
        Returns user_code, verification_url, device_code, expires_in, and interval.
        """
        cid, _ = cls.get_client_credentials(client_id, client_secret)
        req_scopes = scopes or DEFAULT_SCOPES

        payload = {
            "client_id": cid,
            "scope": req_scopes,
        }

        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.post(GOOGLE_DEVICE_CODE_URL, data=payload)
                if res.status_code != 200:
                    logger.error("Failed to initiate device auth: %d - %s", res.status_code, res.text)
                    raise RuntimeError(f"Google OAuth initiation failed ({res.status_code}): {res.text}")

                data = res.json()
                return {
                    "device_code": data["device_code"],
                    "user_code": data["user_code"],
                    "verification_url": data.get("verification_url", "https://www.google.com/device"),
                    "expires_in": int(data.get("expires_in", 1800)),
                    "interval": int(data.get("interval", 5)),
                    "client_id": cid,
                }
        except Exception as e:
            logger.error("Exception during Google Device OAuth initiation: %s", e)
            raise

    @classmethod
    def poll_device_token(
        cls,
        device_code: str,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        token_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """
        Polls Google token endpoint for authorization resolution.
        Handles RFC-8628 states:
        - authorization_pending
        - slow_down
        - expired_token
        - access_denied
        - success (saves encrypted token)
        """
        cid, csecret = cls.get_client_credentials(client_id, client_secret)
        payload = {
            "client_id": cid,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
        if csecret:
            payload["client_secret"] = csecret

        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.post(GOOGLE_TOKEN_URL, data=payload)
                data = res.json()

                if res.status_code == 200:
                    # Successfully authorized!
                    expires_in = int(data.get("expires_in", 3600))
                    token_record = {
                        "access_token": data.get("access_token"),
                        "refresh_token": data.get("refresh_token"),
                        "token_type": data.get("token_type", "Bearer"),
                        "scope": data.get("scope"),
                        "expires_in": expires_in,
                        "expires_at": int(time.time()) + expires_in,
                        "client_id": cid,
                        "created_at": datetime.utcnow().isoformat(),
                    }
                    cls.save_encrypted_token(token_record, token_path=token_path)
                    logger.info("Successfully received and encrypted YouTube OAuth2 token.")
                    return {
                        "status": "success",
                        "message": "Google Account linked successfully.",
                        "token_type": token_record["token_type"],
                        "expires_at": token_record["expires_at"],
                    }

                err = data.get("error", "unknown_error")
                if err == "authorization_pending":
                    return {
                        "status": "pending",
                        "message": "Waiting for user to enter code at google.com/device...",
                    }
                elif err == "slow_down":
                    return {
                        "status": "slow_down",
                        "message": "Polling rate slowed down by server.",
                    }
                elif err == "expired_token":
                    return {
                        "status": "expired",
                        "message": "Device authorization code has expired. Please initiate again.",
                    }
                elif err == "access_denied":
                    return {
                        "status": "denied",
                        "message": "Authorization request was declined by the operator.",
                    }
                else:
                    return {
                        "status": "error",
                        "error": err,
                        "message": data.get("error_description", f"OAuth error: {err}"),
                    }
        except Exception as e:
            logger.error("Exception during Google Device OAuth polling: %s", e)
            return {
                "status": "error",
                "error": str(e),
                "message": f"Network or polling error: {e}",
            }

    @classmethod
    def save_encrypted_token(
        cls,
        tokens: Dict[str, Any],
        token_path: Optional[Path] = None,
        master_key: Optional[str] = None,
    ) -> Path:
        """
        Encrypts tokens with AES-256-GCM via db.security and saves to token_path.
        """
        target_path = token_path or getattr(settings, "YOUTUBE_OAUTH_TOKEN_PATH", None) or (settings.STORAGE_DIR / "youtube_oauth.json")
        target_path.parent.mkdir(parents=True, exist_ok=True)

        raw_json = json.dumps(tokens)
        encrypted_payload = encrypt_secret(raw_json, master_key=master_key)

        vault_data = {
            "version": "1.0",
            "encrypted_data": encrypted_payload,
            "client_id": tokens.get("client_id", ""),
            "updated_at": datetime.utcnow().isoformat(),
        }

        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(vault_data, f, indent=2)

        logger.info("Persisted encrypted YouTube OAuth token vault to %s", target_path)
        return target_path

    @classmethod
    def load_encrypted_token(
        cls,
        token_path: Optional[Path] = None,
        master_key: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Loads and decrypts stored OAuth token from token_path.
        Returns deserialized token dictionary or None if absent/invalid.
        """
        target_path = token_path or getattr(settings, "YOUTUBE_OAUTH_TOKEN_PATH", None) or (settings.STORAGE_DIR / "youtube_oauth.json")
        if not target_path.exists() or target_path.stat().st_size == 0:
            return None

        try:
            with open(target_path, "r", encoding="utf-8") as f:
                vault_data = json.load(f)

            enc_payload = vault_data.get("encrypted_data")
            if not enc_payload:
                return None

            raw_json = decrypt_secret(enc_payload, master_key=master_key)
            return json.loads(raw_json)
        except Exception as e:
            logger.error("Failed to decrypt YouTube OAuth token from %s: %s", target_path, e)
            return None

    @classmethod
    def refresh_access_token(
        cls,
        tokens: Dict[str, Any],
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        token_path: Optional[Path] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Autonomously refreshes access token using refresh_token.
        Updates vault file on disk if refreshed.
        """
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            logger.warning("Cannot refresh token: no refresh_token found in vault.")
            return None

        cid, csecret = cls.get_client_credentials(
            client_id or tokens.get("client_id"),
            client_secret,
        )

        payload = {
            "client_id": cid,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        if csecret:
            payload["client_secret"] = csecret

        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.post(GOOGLE_TOKEN_URL, data=payload)
                if res.status_code != 200:
                    logger.error("Failed to refresh Google OAuth token: %d - %s", res.status_code, res.text)
                    return None

                data = res.json()
                new_access = data.get("access_token")
                if not new_access:
                    return None

                expires_in = int(data.get("expires_in", 3600))
                tokens["access_token"] = new_access
                tokens["expires_in"] = expires_in
                tokens["expires_at"] = int(time.time()) + expires_in
                tokens["updated_at"] = datetime.utcnow().isoformat()
                if "refresh_token" in data:
                    tokens["refresh_token"] = data["refresh_token"]

                cls.save_encrypted_token(tokens, token_path=token_path)
                logger.info("Successfully refreshed and re-encrypted YouTube OAuth access token.")
                return tokens
        except Exception as e:
            logger.error("Exception during token refresh: %s", e)
            return None

    @classmethod
    def get_valid_access_token(
        cls,
        token_path: Optional[Path] = None,
        master_key: Optional[str] = None,
    ) -> Optional[str]:
        """
        Returns a valid, non-expired access token string.
        Refreshes autonomously if expiration is within 300 seconds.
        """
        tokens = cls.load_encrypted_token(token_path=token_path, master_key=master_key)
        if not tokens:
            return None

        now = int(time.time())
        expires_at = int(tokens.get("expires_at", 0))

        # If token is still fresh (more than 300s left), return immediately
        if expires_at - now > 300:
            return tokens.get("access_token")

        # Expired or expiring soon -> attempt refresh
        logger.info("OAuth access token expiring (in %ds) or expired. Attempting refresh...", expires_at - now)
        refreshed = cls.refresh_access_token(tokens, token_path=token_path)
        if refreshed and refreshed.get("access_token"):
            return refreshed.get("access_token")

        # Fallback to current token if not completely expired yet
        if expires_at > now:
            return tokens.get("access_token")

        return None

    @classmethod
    def has_active_oauth(cls, token_path: Optional[Path] = None) -> bool:
        """Checks if a valid, decrypted OAuth session exists."""
        tokens = cls.load_encrypted_token(token_path=token_path)
        if not tokens:
            return False
        # Either has a valid unexpired access token or a refresh token
        now = int(time.time())
        has_refresh = bool(tokens.get("refresh_token"))
        is_fresh = int(tokens.get("expires_at", 0)) > now
        return has_refresh or is_fresh

    @classmethod
    def get_status(cls, token_path: Optional[Path] = None) -> Dict[str, Any]:
        """Returns diagnostic status of OAuth vault."""
        tokens = cls.load_encrypted_token(token_path=token_path)
        if not tokens:
            return {
                "connected": False,
                "status": "DISCONNECTED",
                "message": "No active Google OAuth session linked.",
            }

        now = int(time.time())
        expires_at = int(tokens.get("expires_at", 0))
        ttl = max(0, expires_at - now)
        has_refresh = bool(tokens.get("refresh_token"))

        return {
            "connected": True,
            "status": "CONNECTED",
            "expires_in_sec": ttl,
            "has_refresh_token": has_refresh,
            "scope": tokens.get("scope", DEFAULT_SCOPES),
            "created_at": tokens.get("created_at"),
            "token_masked": f"{tokens.get('access_token', '')[:6]}...{tokens.get('access_token', '')[-4:]}" if tokens.get('access_token') else None,
        }

    @classmethod
    def revoke_oauth(cls, token_path: Optional[Path] = None) -> bool:
        """Revokes token at Google and removes local vault file."""
        target_path = token_path or getattr(settings, "YOUTUBE_OAUTH_TOKEN_PATH", None) or (settings.STORAGE_DIR / "youtube_oauth.json")
        tokens = cls.load_encrypted_token(token_path=target_path)
        if tokens:
            tok = tokens.get("refresh_token") or tokens.get("access_token")
            if tok:
                try:
                    with httpx.Client(timeout=10.0) as client:
                        client.post(GOOGLE_REVOKE_URL, params={"token": tok})
                except Exception as e:
                    logger.debug("Google token revoke HTTP call skipped/failed: %s", e)

        if target_path.exists():
            try:
                target_path.unlink()
                logger.info("Removed YouTube OAuth vault file: %s", target_path)
                return True
            except Exception as e:
                logger.error("Failed to delete token file %s: %s", target_path, e)
                return False
        return True

    revoke_token = revoke_oauth


# Convenience singleton aliases
initiate_device_auth = YouTubeDeviceOAuth.initiate_device_auth
poll_device_token = YouTubeDeviceOAuth.poll_device_token
get_valid_access_token = YouTubeDeviceOAuth.get_valid_access_token
has_active_oauth = YouTubeDeviceOAuth.has_active_oauth
save_encrypted_token = YouTubeDeviceOAuth.save_encrypted_token
load_encrypted_token = YouTubeDeviceOAuth.load_encrypted_token
revoke_oauth = YouTubeDeviceOAuth.revoke_oauth
