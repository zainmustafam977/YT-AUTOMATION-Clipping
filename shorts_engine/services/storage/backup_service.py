"""
Comprehensive Backup & Restore Service.
Provides robust, industrial-grade export and import capabilities for:
1. YouTube Channels & upload defaults (with automatic upsert and collision prevention).
2. API Key Vaults (dual-mode: Plaintext JSON or Password-Protected Encrypted JSON using PBKDF2-HMAC-SHA256 + AES-256-GCM).
3. Full System Snapshots (Settings + Channels + API Keys in a single unified atomic archive).

Guarantees zero-crash resilience: malformed files, wrong passwords, or duplicate keys
are handled gracefully with actionable diagnostics and never terminate or crash the server.
"""

import os
import json
import base64
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Union
from sqlalchemy.orm import Session

from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from shorts_engine.config.settings import settings
from shorts_engine.config.logging_config import save_runtime_settings, reload_runtime_settings
from shorts_engine.db.models import Channel, ApiKey
from shorts_engine.db.security import encrypt_secret, decrypt_secret

logger = logging.getLogger(__name__)

KDF_ITERATIONS = 100_000
SALT_SIZE = 16
NONCE_SIZE = 12
ALLOWED_KEY_PROVIDERS = {"groq", "gemini", "openrouter", "openai"}


def _derive_passphrase_key(passphrase: str, salt: bytes, iterations: int = KDF_ITERATIONS) -> bytes:
    """Derives a 256-bit AES key from a user-supplied passphrase using PBKDF2-HMAC-SHA256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def _encrypt_payload_with_passphrase(data_bytes: bytes, passphrase: str) -> Dict[str, Any]:
    """Encrypts raw bytes using AES-256-GCM keyed by a PBKDF2-derived passphrase key."""
    if not passphrase or len(passphrase.strip()) < 4:
        raise ValueError("Passphrase must be at least 4 characters for encrypted backups.")

    salt = os.urandom(SALT_SIZE)
    key = _derive_passphrase_key(passphrase.strip(), salt)
    nonce = os.urandom(NONCE_SIZE)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, data_bytes, None)

    return {
        "kdf": "PBKDF2HMAC-SHA256",
        "iterations": KDF_ITERATIONS,
        "salt_b64": base64.b64encode(salt).decode("utf-8"),
        "nonce_b64": base64.b64encode(nonce).decode("utf-8"),
        "ciphertext_b64": base64.b64encode(ciphertext).decode("utf-8"),
    }


def _decrypt_payload_with_passphrase(payload: Dict[str, Any], passphrase: str) -> bytes:
    """Decrypts AES-256-GCM encrypted payload using the provided passphrase."""
    if not passphrase:
        raise ValueError("Passphrase is required to decrypt this backup archive.")

    try:
        salt = base64.b64decode(payload["salt_b64"].encode("utf-8"))
        nonce = base64.b64decode(payload["nonce_b64"].encode("utf-8"))
        ciphertext = base64.b64decode(payload["ciphertext_b64"].encode("utf-8"))
        iterations = int(payload.get("iterations", KDF_ITERATIONS))
    except Exception as e:
        raise ValueError(f"Corrupted or malformed encrypted backup header: {e}") from e

    key = _derive_passphrase_key(passphrase.strip(), salt, iterations)
    aesgcm = AESGCM(key)

    try:
        decrypted = aesgcm.decrypt(nonce, ciphertext, None)
        return decrypted
    except Exception as e:
        logger.warning("Decryption failed for backup archive: incorrect password or corrupted ciphertext.")
        raise ValueError("Decryption failed. Please check your passphrase and try again.") from e


class BackupService:
    """Centralized backup and restore management service."""

    # =========================================================================
    # 1. Channels Export & Restore
    # =========================================================================

    @staticmethod
    def export_channels(db: Session) -> Dict[str, Any]:
        """
        Exports all monitored channels, their configurations, and upload defaults into a portable JSON structure.
        """
        channels = db.query(Channel).order_by(Channel.created_at.asc()).all()
        exported_list = []

        for ch in channels:
            exported_list.append({
                "youtube_channel_id": ch.youtube_channel_id,
                "name": ch.name,
                "watch_enabled": bool(ch.watch_enabled),
                "human_review_required": bool(ch.human_review_required),
                "publish_target_channel_id": ch.publish_target_channel_id,
                "last_seen_video_id": ch.last_seen_video_id,
                "upload_defaults": ch.upload_defaults,
            })

        return {
            "version": 1,
            "type": "channels_backup",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "count": len(exported_list),
            "channels": exported_list,
        }

    @staticmethod
    def import_channels(
        payload: Union[Dict[str, Any], List[Dict[str, Any]]],
        db: Session,
        update_existing: bool = True,
    ) -> Dict[str, Any]:
        """
        Imports channels from backup data with automatic upsert.
        Ensures existing channels can be updated without crashing or throwing unique constraint violations.
        """
        if isinstance(payload, dict):
            raw_channels = payload.get("channels", [])
        elif isinstance(payload, list):
            raw_channels = payload
        else:
            raise ValueError("Invalid channels backup format: expected JSON object or list.")

        if not isinstance(raw_channels, list):
            raise ValueError("Malformed 'channels' payload: expected array of channel objects.")

        imported_count = 0
        updated_count = 0
        skipped_count = 0
        errors: List[str] = []

        for idx, item in enumerate(raw_channels):
            if not isinstance(item, dict):
                errors.append(f"Item #{idx}: not a valid channel object.")
                skipped_count += 1
                continue

            yt_id = str(item.get("youtube_channel_id", "")).strip()
            name = str(item.get("name", "")).strip()

            if not yt_id:
                errors.append(f"Item #{idx}: missing youtube_channel_id.")
                skipped_count += 1
                continue

            if not name:
                name = f"Channel {yt_id}"

            existing = db.query(Channel).filter_by(youtube_channel_id=yt_id).first()
            if existing:
                if update_existing:
                    existing.name = name
                    if "watch_enabled" in item:
                        existing.watch_enabled = bool(item["watch_enabled"])
                    if "human_review_required" in item:
                        existing.human_review_required = bool(item["human_review_required"])
                    if "publish_target_channel_id" in item:
                        existing.publish_target_channel_id = item["publish_target_channel_id"]
                    if "upload_defaults" in item and isinstance(item["upload_defaults"], dict):
                        existing.upload_defaults = item["upload_defaults"]
                    updated_count += 1
                else:
                    skipped_count += 1
            else:
                new_channel = Channel(
                    youtube_channel_id=yt_id,
                    name=name,
                    watch_enabled=bool(item.get("watch_enabled", True)),
                    human_review_required=bool(item.get("human_review_required", True)),
                    publish_target_channel_id=item.get("publish_target_channel_id"),
                    last_seen_video_id=item.get("last_seen_video_id"),
                )
                if "upload_defaults" in item and isinstance(item["upload_defaults"], dict):
                    new_channel.upload_defaults = item["upload_defaults"]
                db.add(new_channel)
                imported_count += 1

        try:
            db.commit()
            logger.info("Channel backup import completed: %d added, %d updated, %d skipped", imported_count, updated_count, skipped_count)
        except Exception as e:
            db.rollback()
            logger.error("Failed to commit channel import transaction: %s", e)
            raise ValueError(f"Database error during channel import: {e}") from e

        return {
            "status": "success",
            "imported": imported_count,
            "updated": updated_count,
            "skipped": skipped_count,
            "total_processed": len(raw_channels),
            "errors": errors,
        }

    # =========================================================================
    # 2. API Keys Export & Restore (Dual Mode)
    # =========================================================================

    @staticmethod
    def get_all_active_keys(db: Optional[Session] = None) -> List[Dict[str, str]]:
        """
        Collects all active configured API keys across settings and the persistent DB vault.
        Returns a deduplicated list of {provider, key, nickname}.
        """
        reload_runtime_settings()
        collected_keys: List[Dict[str, str]] = []
        seen = set()

        # 1. From settings
        for prov in ALLOWED_KEY_PROVIDERS:
            getter = getattr(settings, f"get_{prov}_keys", None)
            if getter:
                for k in getter():
                    clean_k = k.strip()
                    if clean_k and (prov, clean_k) not in seen:
                        seen.add((prov, clean_k))
                        collected_keys.append({
                            "provider": prov,
                            "key": clean_k,
                            "nickname": f"{prov.title()} Key ({clean_k[:4]}...{clean_k[-4:]})",
                        })

        # 2. From ApiKey DB table if db session provided
        if db:
            try:
                db_keys = db.query(ApiKey).all()
                for ak in db_keys:
                    prov = ak.provider.lower().strip()
                    if prov in ALLOWED_KEY_PROVIDERS and ak.api_key_encrypted:
                        try:
                            decrypted = decrypt_secret(ak.api_key_encrypted)
                            if decrypted and (prov, decrypted) not in seen:
                                seen.add((prov, decrypted))
                                collected_keys.append({
                                    "provider": prov,
                                    "key": decrypted,
                                    "nickname": ak.nickname or f"{prov.title()} Vault Key",
                                })
                        except Exception as e:
                            logger.warning("Could not decrypt DB API key %s: %s", ak.id, e)
            except Exception as e:
                logger.warning("Could not query DB ApiKey table: %s", e)

        return collected_keys

    @classmethod
    def export_api_keys(
        cls,
        mode: str = "plaintext",
        passphrase: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        """
        Exports API keys either as plaintext JSON or as a password-protected AES-256-GCM encrypted package.
        """
        keys_list = cls.get_all_active_keys(db)

        if mode.lower() == "encrypted":
            if not passphrase or len(passphrase.strip()) < 4:
                raise ValueError("A passphrase of at least 4 characters is required to generate an encrypted backup.")

            inner_data = json.dumps(keys_list).encode("utf-8")
            enc_meta = _encrypt_payload_with_passphrase(inner_data, passphrase)

            return {
                "version": 1,
                "type": "api_keys_encrypted",
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "count": len(keys_list),
                **enc_meta,
            }
        else:
            return {
                "version": 1,
                "type": "api_keys_plaintext",
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "count": len(keys_list),
                "keys": keys_list,
            }

    @classmethod
    def import_api_keys(
        cls,
        payload: Dict[str, Any],
        passphrase: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        """
        Imports API keys from a plaintext or password-protected JSON backup archive.
        Registers keys immediately into runtime settings and persists to the database vault.
        """
        if not isinstance(payload, dict):
            raise ValueError("Invalid backup format: expected JSON object.")

        b_type = payload.get("type", "api_keys_plaintext")

        if b_type == "api_keys_encrypted" or "ciphertext_b64" in payload:
            decrypted_bytes = _decrypt_payload_with_passphrase(payload, passphrase or "")
            try:
                keys_list = json.loads(decrypted_bytes.decode("utf-8"))
            except Exception as e:
                raise ValueError("Corrupted decrypted key data: failed to parse JSON.") from e
        elif "keys" in payload:
            keys_list = payload["keys"]
        elif any(k in payload for k in ALLOWED_KEY_PROVIDERS):
            # Format: { "groq": ["k1"], "gemini": ["k2"] }
            keys_list = []
            for prov in ALLOWED_KEY_PROVIDERS:
                val = payload.get(prov)
                if isinstance(val, list):
                    for k in val:
                        keys_list.append({"provider": prov, "key": str(k)})
                elif isinstance(val, str) and val.strip():
                    keys_list.append({"provider": prov, "key": val.strip()})
        else:
            raise ValueError("Unrecognized API keys backup archive format.")

        if not isinstance(keys_list, list):
            raise ValueError("Expected list of API keys in decrypted archive.")

        imported_count = 0
        skipped_count = 0
        updated_providers = set()

        for item in keys_list:
            if not isinstance(item, dict):
                skipped_count += 1
                continue

            prov = str(item.get("provider", "")).lower().strip()
            raw_key = str(item.get("key", "")).strip()
            nickname = str(item.get("nickname", "")).strip() or f"{prov.title()} Imported Key"

            if not raw_key or prov not in ALLOWED_KEY_PROVIDERS or raw_key.startswith("••••"):
                skipped_count += 1
                continue

            # Add to settings
            added = settings.add_api_key(prov, raw_key)
            if added:
                imported_count += 1
                updated_providers.add(prov)
            else:
                skipped_count += 1

            # Sync to DB ApiKey vault if session provided
            if db:
                try:
                    enc_key = encrypt_secret(raw_key)
                    existing_ak = db.query(ApiKey).filter_by(provider=prov, nickname=nickname).first()
                    if existing_ak:
                        existing_ak.api_key_encrypted = enc_key
                    else:
                        new_ak = ApiKey(
                            provider=prov,
                            api_key_encrypted=enc_key,
                            nickname=nickname,
                            status="ACTIVE",
                        )
                        db.add(new_ak)
                except Exception as e:
                    logger.warning("Could not persist key to DB vault: %s", e)

        # Save settings to runtime_settings.json
        updates: Dict[str, Any] = {}
        for prov in ALLOWED_KEY_PROVIDERS:
            keys_for_prov = getattr(settings, f"get_{prov}_keys")()
            updates[f"{prov.upper()}_API_KEYS"] = keys_for_prov
            updates[f"{prov.upper()}_API_KEY"] = keys_for_prov[0] if keys_for_prov else None

        save_runtime_settings(updates)

        if db:
            try:
                db.commit()
            except Exception as e:
                db.rollback()
                logger.error("DB commit failed during API key import: %s", e)

        return {
            "status": "success",
            "imported": imported_count,
            "skipped": skipped_count,
            "total_processed": len(keys_list),
            "updated_providers": sorted(list(updated_providers)),
        }

    # =========================================================================
    # 3. Full System Snapshot
    # =========================================================================

    @classmethod
    def export_full_system_snapshot(
        cls,
        db: Session,
        mode: str = "plaintext",
        passphrase: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Creates a complete system snapshot containing:
        - Runtime Settings configuration
        - Monitored Channels & upload profiles
        - API Key Vault (plaintext or password-encrypted)
        """
        reload_runtime_settings()
        from shorts_engine.config.dynamic_store import DynamicConfigStore
        store = DynamicConfigStore.get_instance()
        store.load_from_db(db)

        settings_snapshot = {
            "LLM_PROVIDER": getattr(settings, "LLM_PROVIDER", "auto"),
            "LLM_MODEL_NAME": getattr(settings, "LLM_MODEL_NAME", "gemini-3.8-flash"),
            "GROQ_MODEL": getattr(settings, "GROQ_MODEL_NAME", "openai/gpt-oss-120b"),
            "TARGET_DURATION_MIN_SEC": getattr(settings, "TARGET_DURATION_MIN_SEC", 15),
            "TARGET_DURATION_MAX_SEC": getattr(settings, "TARGET_DURATION_MAX_SEC", 58),
            "MAX_CLIPS_PER_VIDEO": getattr(settings, "MAX_SEGMENTS_PER_VIDEO", 5),
            "MIN_CLIPS_PER_VIDEO": getattr(settings, "MIN_SEGMENTS_PER_VIDEO", 3),
            "VIDEO_CRF": getattr(settings, "VIDEO_ENCODING_CRF", 18),
            "VIDEO_PRESET": getattr(settings, "VIDEO_ENCODING_PRESET", "medium"),
            "VIDEO_SCALE_ALGORITHM": getattr(settings, "VIDEO_SCALE_ALGORITHM", "lanczos"),
            "TARGET_FPS": getattr(settings, "VIDEO_TARGET_FPS", 30),
            "AUDIO_BITRATE": getattr(settings, "AUDIO_BITRATE", "320k"),
            "AUDIO_SAMPLE_RATE": getattr(settings, "AUDIO_SAMPLE_RATE", 48000),
            "DRY_RUN_PUBLISHING": getattr(settings, "DRY_RUN_PUBLISHING", True),
            "DRIP_FEED_ENABLED": getattr(settings, "DRIP_FEED_ENABLED", True),
            "DRIP_FEED_POLL_INTERVAL_SECONDS": getattr(settings, "DRIP_FEED_POLL_INTERVAL_SECONDS", 60),
            "MAX_RETRY_COUNT": getattr(settings, "MAX_RETRY_COUNT", 3),
            "WHISPER_MODEL_SIZE": getattr(settings, "WHISPER_MODEL_SIZE", "base"),
            "WHISPER_BEAM_SIZE": getattr(settings, "WHISPER_BEAM_SIZE", 5),
            "SILERO_VAD_FILTER": getattr(settings, "WHISPER_VAD_FILTER", True),
            "WHISPER_LANGUAGE": getattr(settings, "WHISPER_LANGUAGE", "en"),
        }

        channels_snapshot = cls.export_channels(db)
        keys_snapshot = cls.export_api_keys(mode=mode, passphrase=passphrase, db=db)

        return {
            "version": 1,
            "type": "full_system_snapshot",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "settings": settings_snapshot,
            "channels": channels_snapshot.get("channels", []),
            "keys_archive": keys_snapshot,
        }

    @classmethod
    def import_full_system_snapshot(
        cls,
        payload: Dict[str, Any],
        db: Session,
        passphrase: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Restores a full system snapshot with zero downtime or restarts.
        """
        if not isinstance(payload, dict):
            raise ValueError("Invalid full system snapshot format: expected JSON object.")

        results: Dict[str, Any] = {
            "status": "success",
            "settings_restored": False,
            "channels": None,
            "keys": None,
        }

        # 1. Restore Settings
        if "settings" in payload and isinstance(payload["settings"], dict):
            from shorts_engine.config.dynamic_store import DynamicConfigStore
            settings_data = payload["settings"]
            save_runtime_settings(settings_data)
            store = DynamicConfigStore.get_instance()
            for k, v in settings_data.items():
                if isinstance(v, (str, int, float, bool)):
                    store.set(k, v, db)
            results["settings_restored"] = True

        # 2. Restore Channels
        if "channels" in payload:
            results["channels"] = cls.import_channels(payload["channels"], db, update_existing=True)

        # 3. Restore API Keys
        keys_data = payload.get("keys_archive") or payload.get("keys")
        if keys_data and isinstance(keys_data, dict):
            results["keys"] = cls.import_api_keys(keys_data, passphrase=passphrase, db=db)

        return results
