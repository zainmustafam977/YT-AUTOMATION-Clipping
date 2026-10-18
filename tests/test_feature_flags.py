"""R4-8.4 — Feature-flag expiry tests."""
import pytest

from shorts_engine.services.feature_flags import (
    Flag, FLAGS, is_expired, flag as flag_enabled,
    check_expiries, _parse_semver, CURRENT_VERSION,
)


def test_parse_semver_basic():
    assert _parse_semver("1.2.3") == (1, 2, 3)
    assert _parse_semver("v1.2.3") == (1, 2, 3)


def test_parse_semver_invalid():
    with pytest.raises(ValueError):
        _parse_semver("1.2")
    with pytest.raises(ValueError):
        _parse_semver("not-a-version")


def test_is_expired_past():
    f = Flag(name="x", expires_in="1.0.0")
    assert is_expired(f, current="1.1.0") is True
    assert is_expired(f, current="2.0.0") is True


def test_is_expired_future():
    f = Flag(name="x", expires_in="2.0.0")
    assert is_expired(f, current="1.0.0") is False
    assert is_expired(f, current="1.9.9") is False


def test_is_expired_equal():
    """An expiry of v1.4.0 is expired once we ARE on v1.4.0."""
    f = Flag(name="x", expires_in="1.4.0")
    assert is_expired(f, current="1.4.0") is True


def test_is_expired_unparseable():
    f = Flag(name="x", expires_in="not-a-version")
    assert is_expired(f) is True


def test_flag_enabled_known():
    # A flag that is not yet expired is enabled.
    assert flag_enabled("STRICT_MODE") is True


def test_flag_enabled_unknown():
    assert flag_enabled("DOES_NOT_EXIST") is False


def test_check_expiries_default_current():
    """With the default CURRENT_VERSION, expiried flags are reported."""
    expired = list(check_expiries())
    # All flags with expires_in <= CURRENT_VERSION are in here.
    # Default: RIGHTS_LEDGER_MODE and PREREGISTRATION_MODE are <= 1.4.0.
    names = {f.name for f in expired}
    assert "RIGHTS_LEDGER_MODE" in names
    assert "PREREGISTRATION_MODE" in names


def test_check_expiries_custom_current():
    """With an older current version, fewer flags are expired."""
    expired = list(check_expiries(current="1.0.0"))
    assert expired == []  # all our flags expire >= 1.4.0


def test_check_expiries_newer_current():
    """With a much newer current version, every flag is expired."""
    expired = list(check_expiries(current="99.99.99"))
    assert len(expired) == len(FLAGS)


def test_get_returns_flag():
    f = FLAGS.get("STRICT_MODE")
    assert f is not None
    assert f.name == "STRICT_MODE"


def test_get_unknown_returns_none():
    assert FLAGS.get("UNKNOWN") is None


def test_current_version_is_set():
    """If someone deletes CURRENT_VERSION the CI gate will fail; that's good."""
    assert CURRENT_VERSION
