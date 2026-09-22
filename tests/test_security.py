"""
Unit tests for AES-256-GCM encryption at rest.
"""

import pytest
from shorts_engine.db.security import encrypt_secret, decrypt_secret, rotate_secret, mask_secret


def test_encrypt_decrypt_roundtrip():
    secret = "my_super_secret_oauth_refresh_token_12345"
    encrypted = encrypt_secret(secret)
    assert encrypted != secret
    decrypted = decrypt_secret(encrypted)
    assert decrypted == secret


def test_decryption_with_wrong_key_fails():
    secret = "another_secret_token"
    encrypted = encrypt_secret(secret, master_key="correct_master_key_123")
    with pytest.raises(ValueError, match="Decryption failed"):
        decrypt_secret(encrypted, master_key="wrong_master_key_999")


def test_tampered_ciphertext_fails_auth():
    secret = "confidential_api_key"
    encrypted = encrypt_secret(secret)
    
    # Tamper with the base64 content
    raw_bytes = bytearray(encrypted.encode("utf-8"))
    raw_bytes[-4] = (raw_bytes[-4] + 1) % 256
    tampered = raw_bytes.decode("utf-8", errors="ignore")
    
    with pytest.raises(ValueError):
        decrypt_secret(tampered)


def test_key_rotation():
    secret = "token_to_rotate"
    old_key = "key_version_1"
    new_key = "key_version_2"
    
    encrypted_v1 = encrypt_secret(secret, master_key=old_key)
    encrypted_v2 = rotate_secret(encrypted_v1, old_master_key=old_key, new_master_key=new_key)
    
    assert encrypted_v1 != encrypted_v2
    assert decrypt_secret(encrypted_v2, master_key=new_key) == secret
    with pytest.raises(ValueError):
        decrypt_secret(encrypted_v2, master_key=old_key)


def test_mask_secret():
    assert mask_secret("short") == "*****"
    masked = mask_secret("AIzaSyD1234567890abcdef1234567890")
    assert masked.startswith("AIza...")
    assert masked.endswith("7890")


def test_bulk_rotate_all_channel_tokens_success():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from shorts_engine.db.session import Base
    from shorts_engine.db.models import Channel
    from shorts_engine.db.security import rotate_all_channel_tokens

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    old_key = "secret_master_key_v1_old"
    new_key = "secret_master_key_v2_new"

    ch1 = Channel(youtube_channel_id="UC_rot_1", name="Ch 1",
                  encrypted_oauth_token=encrypt_secret("token_alpha", master_key=old_key))
    ch2 = Channel(youtube_channel_id="UC_rot_2", name="Ch 2",
                  encrypted_oauth_token=encrypt_secret("token_beta", master_key=old_key))
    ch3 = Channel(youtube_channel_id="UC_rot_3", name="Ch 3", encrypted_oauth_token=None)
    db.add_all([ch1, ch2, ch3])
    db.commit()

    count = rotate_all_channel_tokens(db, old_master_key=old_key, new_master_key=new_key)
    assert count == 2

    # Verify both tokens are readable under new_key and no longer under old_key
    db.refresh(ch1)
    db.refresh(ch2)
    assert decrypt_secret(ch1.encrypted_oauth_token, master_key=new_key) == "token_alpha"
    assert decrypt_secret(ch2.encrypted_oauth_token, master_key=new_key) == "token_beta"
    with pytest.raises(ValueError):
        decrypt_secret(ch1.encrypted_oauth_token, master_key=old_key)


def test_bulk_rotate_all_channel_tokens_atomic_rollback():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from shorts_engine.db.session import Base
    from shorts_engine.db.models import Channel
    from shorts_engine.db.security import rotate_all_channel_tokens

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    old_key = "secret_master_key_v1_old"
    new_key = "secret_master_key_v2_new"

    original_token_1 = encrypt_secret("token_alpha", master_key=old_key)
    ch1 = Channel(youtube_channel_id="UC_fail_1", name="Ch 1", encrypted_oauth_token=original_token_1)
    # ch2 has corrupted ciphertext that will fail decryption
    ch2 = Channel(youtube_channel_id="UC_fail_2", name="Ch 2", encrypted_oauth_token="CORRUPTED_CIPHERTEXT_XYZ")
    db.add_all([ch1, ch2])
    db.commit()

    with pytest.raises(ValueError):
        rotate_all_channel_tokens(db, old_master_key=old_key, new_master_key=new_key)

    # Atomic rollback verification: ch1 token must remain under old_key
    db.refresh(ch1)
    assert ch1.encrypted_oauth_token == original_token_1
    assert decrypt_secret(ch1.encrypted_oauth_token, master_key=old_key) == "token_alpha"
