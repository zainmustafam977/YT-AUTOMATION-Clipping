"""
Fernet Secret Box for Authenticated Symmetric Encryption at Rest.

Implements centralized, authenticated symmetric encryption using Fernet from the
`cryptography` package.

All persisted secrets (OAuth refresh tokens, API keys, client credentials)
are stored exclusively as Fernet ciphertext.

Master Key Policy:
- Loaded from SECRETS_MASTER_KEY environment variable.
- If unset, fails fast with a clear, actionable diagnostic error message.
- Key generation command:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

import os
import logging
import hashlib
import base64
from typing import Optional, Union
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

ENV_KEY_NAME = "SECRETS_MASTER_KEY"


def _normalize_fernet_key(key: Union[str, bytes]) -> bytes:
    """
    Normalizes a key input to a valid 32-byte url-safe base64 Fernet key.
    If the key is already a valid Fernet key, returns it encoded as bytes.
    If the key is an arbitrary string/passphrase (e.g. from legacy tests),
    derives a deterministic 32-byte Fernet key via SHA-256 + urlsafe base64.
    """
    key_bytes = key.encode("utf-8") if isinstance(key, str) else key
    try:
        Fernet(key_bytes)
        return key_bytes
    except Exception:
        digest = hashlib.sha256(key_bytes).digest()
        return base64.urlsafe_b64encode(digest)


def get_master_key() -> str:
    """
    Retrieves the master Fernet key from the SECRETS_MASTER_KEY environment variable.
    Fails fast with a clear diagnostic message if unset or empty.
    """
    key = os.environ.get(ENV_KEY_NAME)
    if not key:
        # Check settings if settings has loaded it from .env or environment
        try:
            from shorts_engine.config.settings import settings
            key = getattr(settings, "SECRETS_MASTER_KEY", None)
        except Exception:
            pass

    if not key:
        raise RuntimeError(
            f"{ENV_KEY_NAME} environment variable is not set. "
            "Please generate a 32-byte url-safe base64 Fernet key using:\n"
            '  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"\n'
            f"and set {ENV_KEY_NAME}=<key> in your environment before starting the application."
        )

    clean_key = key.strip()
    if not clean_key:
        raise RuntimeError(
            f"{ENV_KEY_NAME} environment variable cannot be empty. "
            "Please generate a valid Fernet key using:\n"
            '  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )

    return clean_key


def get_fernet(master_key: Optional[str] = None) -> Fernet:
    """
    Instantiates a Fernet cipher instance.
    Uses master_key if provided; otherwise loads from SECRETS_MASTER_KEY environment variable.
    """
    raw_key = master_key if master_key is not None else get_master_key()
    norm_key = _normalize_fernet_key(raw_key)
    return Fernet(norm_key)


def encrypt(plaintext: str, master_key: Optional[str] = None) -> str:
    """
    Encrypts a plaintext string into a base64-encoded Fernet ciphertext token.
    Returns empty string if plaintext is empty or None.
    """
    if not plaintext:
        return ""
    cipher = get_fernet(master_key)
    ciphertext_bytes = cipher.encrypt(plaintext.encode("utf-8"))
    return ciphertext_bytes.decode("utf-8")


def decrypt(ciphertext: str, master_key: Optional[str] = None) -> str:
    """
    Decrypts a base64-encoded Fernet ciphertext token back to plaintext.
    Verifies authentication and timestamp integrity.
    Raises ValueError on tampering, invalid key, or corrupted payload.
    """
    if not ciphertext:
        return ""
    cipher = get_fernet(master_key)
    try:
        plaintext_bytes = cipher.decrypt(ciphertext.encode("utf-8"))
        return plaintext_bytes.decode("utf-8")
    except InvalidToken as e:
        logger.error("Decryption failed: Invalid Fernet token or incorrect key")
        raise ValueError("Decryption failed: invalid key or corrupted payload") from e
    except Exception as e:
        logger.error("Decryption error: %s", e)
        raise ValueError(f"Decryption failed: {e}") from e


def rotate(ciphertext: str, old_key: str, new_key: str) -> str:
    """
    Re-encrypts a Fernet ciphertext from old_key to new_key in an atomic operation.
    """
    decrypted = decrypt(ciphertext, master_key=old_key)
    return encrypt(decrypted, master_key=new_key)


def decrypt_legacy_aes_gcm(encrypted_b64: str, legacy_key: str) -> str:
    """
    Decrypts legacy AES-256-GCM + PBKDF2-HMAC-SHA256 (600,000 iterations) payloads.
    Used strictly for migrating existing database and file secrets to Fernet.
    """
    if not encrypted_b64:
        return ""

    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        payload = base64.b64decode(encrypted_b64.encode("utf-8"))
        salt_size = 16
        nonce_size = 12
        if len(payload) < salt_size + nonce_size + 16:
            raise ValueError("Payload too short for legacy AES-256-GCM")

        salt = payload[:salt_size]
        nonce = payload[salt_size : salt_size + nonce_size]
        ciphertext = payload[salt_size + nonce_size :]

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=600_000,
        )
        derived_key = kdf.derive(legacy_key.encode("utf-8"))
        aesgcm = AESGCM(derived_key)
        plain_bytes = aesgcm.decrypt(nonce, ciphertext, None)
        return plain_bytes.decode("utf-8")
    except Exception as e:
        raise ValueError(f"Legacy AES-256-GCM decryption failed: {e}") from e
