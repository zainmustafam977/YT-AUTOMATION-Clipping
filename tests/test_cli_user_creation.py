import os
from unittest.mock import patch, MagicMock
import pytest
import typer
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli import cmd_create_user
from shorts_engine.dashboard.auth import verify_password
from shorts_engine.db.models import Base, User, UserRole


@pytest.fixture
def mock_db_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    monkeypatch.setattr("cli.init_db", lambda: None)
    monkeypatch.setattr("cli.SessionLocal", Session)
    return Session


def test_create_user_uses_getpass_when_password_omitted(mock_db_session):
    """Verify that when --password is not provided, getpass.getpass() is used to securely prompt."""
    with patch("getpass.getpass", return_value="SecurePassword12345!") as mock_getpass:
        cmd_create_user(username="operator_sec", password=None, role="operator")

    assert mock_getpass.called
    session = mock_db_session()
    user = session.query(User).filter_by(username="operator_sec").first()
    assert user is not None
    assert user.role == UserRole.OPERATOR
    assert verify_password("SecurePassword12345!", user.password_hash) is True
    session.close()


def test_create_user_warns_when_password_passed_via_cli(mock_db_session, capsys):
    """Verify that passing plaintext --password via CLI logs a security warning."""
    with patch("cli.console.print") as mock_print:
        cmd_create_user(username="cli_pass_user", password="PlaintextPassword12345!", role="viewer")

    # Verify security warning was printed
    assert any("WARNING: Passing --password on the command line is insecure" in str(call) for call in mock_print.call_args_list)

    session = mock_db_session()
    user = session.query(User).filter_by(username="cli_pass_user").first()
    assert user is not None
    assert user.role == UserRole.VIEWER
    session.close()


def test_create_user_uses_env_var_bootstrap(mock_db_session, monkeypatch):
    """Verify that DASHBOARD_BOOTSTRAP_PASSWORD environment variable is used if password omitted."""
    monkeypatch.setenv("DASHBOARD_BOOTSTRAP_PASSWORD", "BootstrapEnvPass12345!")

    with patch("getpass.getpass") as mock_getpass:
        cmd_create_user(username="bootstrap_user", password=None, role="admin")

    # getpass should NOT be called when env var is present
    assert not mock_getpass.called

    session = mock_db_session()
    user = session.query(User).filter_by(username="bootstrap_user").first()
    assert user is not None
    assert user.role == UserRole.ADMIN
    assert verify_password("BootstrapEnvPass12345!", user.password_hash) is True
    session.close()


def test_create_user_rejects_short_password(mock_db_session):
    """Verify that passwords under 12 characters are rejected."""
    with pytest.raises(typer.Exit):
        with patch("getpass.getpass", return_value="short"):
            cmd_create_user(username="fail_user", password=None, role="operator")
