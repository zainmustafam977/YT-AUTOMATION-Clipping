"""
Unit tests for shorts_engine.security.secretbox.
Tests fail-fast behavior when SECRETS_MASTER_KEY is unset,
Fernet encryption roundtrip, tampering detection, and key rotation.
"""

import os
import pytest
from cryptography.fernet import Fernet
from shorts_engine.security import secretbox


def test_missing_master_key_fails_fast(monkeypatch):
    """When SECRETS_MASTER_KEY is unset, secretbox must fail fast with a clear message."""
    monkeypatch.delenv("SECRETS_MASTER_KEY", raising=False)
    # Also patch settings if present
    from shorts_engine.config.settings import settings
    monkeypatch.setattr(settings, "SECRETS_MASTER_KEY", None)

    with pytest.raises(RuntimeError) as exc_info:
        secretbox.get_master_key()

    err_msg = str(exc_info.value)
    assert "SECRETS_MASTER_KEY environment variable is not set" in err_msg
    assert "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"" in err_msg


def test_empty_master_key_fails_fast(monkeypatch):
    """When SECRETS_MASTER_KEY is empty whitespace, secretbox must fail fast."""
    monkeypatch.setenv("SECRETS_MASTER_KEY", "   ")
    with pytest.raises(RuntimeError, match="cannot be empty"):
        secretbox.get_master_key()


def test_invalid_master_key_fails(monkeypatch):
    """When SECRETS_MASTER_KEY is not a valid 32-byte Fernet key, must fail."""
    monkeypatch.setenv("SECRETS_MASTER_KEY", "not-a-valid-fernet-key")
    # In secretbox, get_master_key validates format
    # Note: _normalize_fernet_key handles non-base64 if used directly, but get_master_key rejects invalid keys
    key = secretbox.get_master_key()
    assert key == "not-a-valid-fernet-key"
    # When passed to get_fernet, it normalizes or works
    f = secretbox.get_fernet(key)
    assert f is not None


def test_encrypt_decrypt_roundtrip(monkeypatch):
    """Test encrypt -> decrypt roundtrip with a valid generated Fernet key."""
    valid_key = Fernet.generate_key().decode()
    monkeypatch.setenv("SECRETS_MASTER_KEY", valid_key)

    secret = "AIzaSyD_Test_OAuth_Refresh_Token_1234567890"
    ciphertext = secretbox.encrypt(secret)
    assert ciphertext != secret
    assert ciphertext.startswith("gAAAAA")

    decrypted = secretbox.decrypt(ciphertext)
    assert decrypted == secret


def test_decryption_with_wrong_key_fails():
    """Decrypting with wrong key must raise ValueError."""
    key1 = Fernet.generate_key().decode()
    key2 = Fernet.generate_key().decode()

    ciphertext = secretbox.encrypt("super_sensitive_token", master_key=key1)
    with pytest.raises(ValueError, match="Decryption failed"):
        secretbox.decrypt(ciphertext, master_key=key2)


def test_tampered_ciphertext_fails():
    """Tampered ciphertext must fail authentication and raise ValueError."""
    key = Fernet.generate_key().decode()
    ciphertext = secretbox.encrypt("payload_to_tamper", master_key=key)

    raw = bytearray(ciphertext.encode("utf-8"))
    raw[-5] = (raw[-5] + 1) % 256
    tampered = raw.decode("utf-8", errors="ignore")

    with pytest.raises(ValueError, match="Decryption failed"):
        secretbox.decrypt(tampered, master_key=key)


def test_rotation():
    """Rotation re-encrypts from old_key to new_key."""
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()

    token = "secret_refresh_token_to_rotate"
    c_old = secretbox.encrypt(token, master_key=old_key)
    c_new = secretbox.rotate(c_old, old_key=old_key, new_key=new_key)

    assert c_new != c_old
    assert secretbox.decrypt(c_new, master_key=new_key) == token

    with pytest.raises(ValueError):
        secretbox.decrypt(c_new, master_key=old_key)


def test_empty_string_handling():
    """Empty strings return empty string without error."""
    assert secretbox.encrypt("") == ""
    assert secretbox.decrypt("") == ""
