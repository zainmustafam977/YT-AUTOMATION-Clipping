"""
Cryptographic module for encrypting and decrypting sensitive tokens and secrets at rest.
Implements authenticated AES-256-GCM with PBKDF2-HMAC-SHA256 key derivation (600,000 iterations).
"""

import os
import base64
import logging
from typing import Optional
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from shorts_engine.config.settings import settings

logger = logging.getLogger(__name__)

PBKDF2_ITERATIONS = 600_000
SALT_SIZE = 16
NONCE_SIZE = 12


def _derive_key(master_key: str, salt: bytes) -> bytes:
    """Derive a 256-bit AES key using PBKDF2-HMAC-SHA256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,  # 256 bits
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(master_key.encode("utf-8"))


def encrypt_secret(plaintext: str, master_key: Optional[str] = None) -> str:
    """
    Encrypts a plaintext string into a base64-encoded payload containing:
    [16-byte salt] + [12-byte nonce] + [ciphertext with 16-byte GCM auth tag]
    """
    if not plaintext:
        return ""

    key_material = master_key or settings.MASTER_ENCRYPTION_KEY
    salt = os.urandom(SALT_SIZE)
    key = _derive_key(key_material, salt)
    
    aesgcm = AESGCM(key)
    nonce = os.urandom(NONCE_SIZE)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    
    # Pack: salt + nonce + ciphertext
    payload = salt + nonce + ciphertext
    return base64.b64encode(payload).decode("utf-8")


def decrypt_secret(encrypted_b64: str, master_key: Optional[str] = None) -> str:
    """
    Decrypts a base64-encoded AES-256-GCM payload.
    Verifies authentication tag; raises ValueError if tampered or invalid key.
    """
    if not encrypted_b64:
        return ""

    try:
        payload = base64.b64decode(encrypted_b64.encode("utf-8"))
        if len(payload) < SALT_SIZE + NONCE_SIZE + 16:
            raise ValueError("Encrypted payload too short to be valid AES-256-GCM")

        salt = payload[:SALT_SIZE]
        nonce = payload[SALT_SIZE : SALT_SIZE + NONCE_SIZE]
        ciphertext = payload[SALT_SIZE + NONCE_SIZE :]

        key_material = master_key or settings.MASTER_ENCRYPTION_KEY
        key = _derive_key(key_material, salt)

        aesgcm = AESGCM(key)
        plaintext_bytes = aesgcm.decrypt(nonce, ciphertext, None)
        return plaintext_bytes.decode("utf-8")
    except Exception as e:
        logger.error("Failed to decrypt secret: %s", str(e))
        raise ValueError("Decryption failed: invalid key or corrupted payload") from e


def rotate_secret(encrypted_b64: str, old_master_key: str, new_master_key: str) -> str:
    """Re-encrypts a payload under a new master key."""
    decrypted = decrypt_secret(encrypted_b64, master_key=old_master_key)
    return encrypt_secret(decrypted, master_key=new_master_key)


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
