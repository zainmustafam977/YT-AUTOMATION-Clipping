from unittest.mock import patch, MagicMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.models import Base, Channel
from shorts_engine.db.security import (
    encrypt_secret, decrypt_secret, rotate_all_channel_tokens
)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    yield session
    session.close()


def test_rotate_secrets_creates_backup_and_rolls_back_on_error(db_session):
    """
    1. Pre-rotation automated database backup must be triggered.
    2. If any channel token fails during rotation, transaction must roll back completely
       and leave original tokens intact.
    """
    old_key = "a" * 32
    new_key = "b" * 32

    # Create channel 1 with valid encrypted token
    tok1 = encrypt_secret("token_one", master_key=old_key)
    c1 = Channel(
        id="c1",
        youtube_channel_id="UC_1",
        name="Channel 1",
        watch_enabled=True,
        human_review_required=False,
        encrypted_oauth_token=tok1,
    )

    # Create channel 2 with corrupted token that will fail decryption
    c2 = Channel(
        id="c2",
        youtube_channel_id="UC_2",
        name="Channel 2",
        watch_enabled=True,
        human_review_required=False,
        encrypted_oauth_token="CORRUPTED_BASE64_TOKEN",
    )

    db_session.add(c1)
    db_session.add(c2)
    db_session.commit()

    backup_mock = MagicMock(return_value={"filename": "backup.db.gz"})

    with patch("shorts_engine.services.storage.backup_manager.DatabaseBackupManager.create_backup", backup_mock):
        with pytest.raises(Exception):
            rotate_all_channel_tokens(db_session, old_master_key=old_key, new_master_key=new_key, auto_backup=True)

        # 1. Verify backup was called
        assert backup_mock.called

    # 2. Verify complete rollback: channel 1 still has its original token under old_key
    db_session.refresh(c1)
    assert c1.encrypted_oauth_token == tok1
    assert decrypt_secret(c1.encrypted_oauth_token, master_key=old_key) == "token_one"


def test_rotate_secrets_success_and_reencryption(db_session):
    """Successful rotation decrypts with old_key, encrypts with new_key, and commits."""
    old_key = "k" * 32
    new_key = "n" * 32

    tok = encrypt_secret("my_secret_token", master_key=old_key)
    c = Channel(
        id="c_ok",
        youtube_channel_id="UC_ok",
        name="OK Channel",
        watch_enabled=True,
        human_review_required=False,
        encrypted_oauth_token=tok,
    )
    db_session.add(c)
    db_session.commit()

    backup_mock = MagicMock(return_value={"filename": "backup.db.gz"})
    with patch("shorts_engine.services.storage.backup_manager.DatabaseBackupManager.create_backup", backup_mock):
        count = rotate_all_channel_tokens(db_session, old_master_key=old_key, new_master_key=new_key, auto_backup=True)
        assert count == 1
        assert backup_mock.called

    db_session.refresh(c)
    # Must now decrypt with new_key
    assert decrypt_secret(c.encrypted_oauth_token, master_key=new_key) == "my_secret_token"
    # Must fail to decrypt with old_key
    with pytest.raises(ValueError):
        decrypt_secret(c.encrypted_oauth_token, master_key=old_key)


def test_cli_rotate_secrets_reads_from_env_vars(monkeypatch):
    """CLI rotate-secrets reads keys from environment variables and does not require CLI arguments."""
    from typer.testing import CliRunner
    from cli import app

    runner = CliRunner()
    monkeypatch.setenv("OLD_MASTER_KEY", "oldkey" * 8)
    monkeypatch.setenv("NEW_MASTER_KEY", "newkey" * 8)

    with patch("shorts_engine.db.security.rotate_all_channel_tokens", return_value=3) as mock_rot:
        result = runner.invoke(app, ["rotate-secrets"])
        assert result.exit_code == 0
        assert mock_rot.called
        assert "Successfully rotated 3 stored secret(s)" in result.stdout

