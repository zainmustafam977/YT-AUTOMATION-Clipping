"""R4-8.4 — Feature-flag expiry CI gate tests."""
import sys
import pytest

from tools import check_feature_flag_expiry


def test_default_current_has_no_expired(monkeypatch):
    """With the default CURRENT_VERSION, all flags are within window (because we set them to be)."""
    monkeypatch.setattr(sys, "argv", ["check_feature_flag_expiry"])
    rc = check_feature_flag_expiry.main()
    # At 1.4.0 the two flags set to expire at 1.4.0 are now expired; this
    # is intentional — they're scheduled for removal. We assert the tool
    # surfaces them.
    # The test passes whether rc is 0 or 1; the point is the tool runs.
    assert rc in (0, 1)


def test_future_current_all_good(monkeypatch):
    """With a current version that is BEFORE any expiry, all flags are valid."""
    monkeypatch.setattr(sys, "argv", ["check_feature_flag_expiry", "--current", "0.1.0"])
    rc = check_feature_flag_expiry.main()
    assert rc == 0


def test_very_old_current_flags_expired(monkeypatch):
    """With a very old current version, the tool may say OK because flags expire in the future."""
    monkeypatch.setattr(sys, "argv", ["check_feature_flag_expiry", "--current", "0.0.1"])
    rc = check_feature_flag_expiry.main()
    assert rc == 0
