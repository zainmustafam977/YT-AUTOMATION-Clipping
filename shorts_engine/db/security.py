"""
Cryptographic module for encrypting and decrypting sensitive tokens and secrets at rest.
Standardized on Fernet authenticated symmetric encryption via shorts_engine.security.secretbox.
"""

import logging
from typing import Optional

from shorts_engine.security.secretbox import (
    encrypt as secretbox_encrypt,
    decrypt as secretbox_decrypt,
    decrypt_legacy_aes_gcm,
)

logger = logging.getLogger(__name__)


def encrypt_secret(plaintext: str, master_key: Optional[str] = None) -> str:
    """
    Encrypts a plaintext string into a Fernet ciphertext token.
    Uses master_key if provided; otherwise loads from SECRETS_MASTER_KEY.
    """
    return secretbox_encrypt(plaintext, master_key=master_key)


def decrypt_secret(encrypted_b64: str, master_key: Optional[str] = None) -> str:
    """
    Decrypts a Fernet ciphertext token back to plaintext.
    Raises ValueError if decryption fails or authentication tag is invalid.
    """
    return secretbox_decrypt(encrypted_b64, master_key=master_key)


def rotate_secret(encrypted_b64: str, old_master_key: str, new_master_key: str) -> str:
    """Re-encrypts a payload under a new master key."""
    try:
        decrypted = secretbox_decrypt(encrypted_b64, master_key=old_master_key)
    except Exception:
        # Fallback to legacy AES-256-GCM for pre-migration payloads
        decrypted = decrypt_legacy_aes_gcm(encrypted_b64, old_master_key)
    return secretbox_encrypt(decrypted, master_key=new_master_key)


def rotate_all_channel_tokens(
    db_session,
    old_master_key: str,
    new_master_key: str,
    auto_backup: bool = True,
) -> int:
    """
    Bulk rotation orchestration: decrypts and re-encrypts all stored channel OAuth tokens
    in a single atomic transaction.
    Automatically creates a hot database backup before executing any mutations.
    Rolls back completely if any single row fails decryption or re-encryption.
    Returns the number of successfully rotated tokens.
    """
    from shorts_engine.db.models import Channel

    if auto_backup:
        try:
            from shorts_engine.services.storage.backup_manager import DatabaseBackupManager
            backup_mgr = DatabaseBackupManager()
            backup_meta = backup_mgr.create_backup(compress=True)
            logger.info("Pre-rotation database backup created: %s", backup_meta.get("filename"))
        except Exception as e:
            logger.error("Pre-rotation database backup failed: %s. Aborting secret rotation.", e)
            raise RuntimeError(f"Pre-rotation database backup failed: {e}") from e

    channels = db_session.query(Channel).filter(Channel.encrypted_oauth_token.isnot(None)).all()
    count = 0
    try:
        for ch in channels:
            if ch.encrypted_oauth_token:
                ch.encrypted_oauth_token = rotate_secret(
                    ch.encrypted_oauth_token,
                    old_master_key=old_master_key,
                    new_master_key=new_master_key,
                )
                count += 1
        db_session.commit()
        logger.info("Successfully rotated %d channel OAuth token(s) in a single transaction.", count)
        return count
    except Exception as e:
        db_session.rollback()
        logger.error("Bulk secret rotation failed, transaction rolled back: %s", e)
        raise


def mask_secret(secret: Optional[str], show_chars: int = 4) -> str:
    """Redacts secret strings for safe logging."""
    if not secret:
        return "<none>"
    if len(secret) <= show_chars * 2:
        return "*" * len(secret)
    return f"{secret[:show_chars]}...{secret[-show_chars:]}"
