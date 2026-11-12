import os

# Ensure libass/fontconfig can locate the bundled project fonts even in minimal
# CI/container images that ship no system fontconfig configuration.
if not os.environ.get("FONTCONFIG_FILE"):
    _fc_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
    _fc_conf = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".fontconfig", "fonts.conf")
    os.makedirs(os.path.dirname(_fc_conf), exist_ok=True)
    if not os.path.exists(_fc_conf):
        with open(_fc_conf, "w", encoding="utf-8") as _fh:
            _fh.write(
                "<?xml version=\"1.0\"?>\n<!DOCTYPE fontconfig SYSTEM \"fonts.dtd\">\n"
                "<fontconfig>\n"
                f"  <dir>{_fc_dir}</dir>\n"
                "  <cachedir>/tmp/fontconfig-cache</cachedir>\n"
                "</fontconfig>\n"
            )
    os.environ["FONTCONFIG_FILE"] = _fc_conf
    os.environ.setdefault("FONTCONFIG_PATH", os.path.dirname(_fc_conf))

# Prevent OpenMP and BLAS threading runtime conflicts between torch, ctranslate2, and pyav on Linux
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

# Starlette's TestClient sends "Host: testserver". The dashboard enforces a Host
# header allowlist and answers 400 for unknown hosts, which would mask the
# status codes these tests assert on. Allow the test host explicitly instead of
# disabling the guard, so the allowlist itself stays under test.
os.environ.setdefault("DASHBOARD_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver")

import json
import pytest
from datetime import date


@pytest.fixture(autouse=True)
def isolate_quota_state(monkeypatch, tmp_path):
    """
    Ensures each test execution starts with a clean, isolated YouTube quota state
    so that repeated local test runs never exhaust the daily 10,000 unit quota.
    """
    isolated_state_file = tmp_path / "test_quota_state.json"
    isolated_state_file.write_text(
        json.dumps({"date": date.today().isoformat(), "used_units": 0}),
        encoding="utf-8",
    )
    from shorts_engine.services.publishing.quota import QuotaTracker

    orig_init = QuotaTracker.__init__

    def patched_init(self, state_file=None):
        orig_init(self, state_file=state_file or isolated_state_file)

    monkeypatch.setattr(QuotaTracker, "__init__", patched_init)
    yield


@pytest.fixture(autouse=True)
def default_test_master_key(monkeypatch):
    """Provides a valid test Fernet master key for test execution if not explicitly set."""
    if not os.environ.get("SECRETS_MASTER_KEY"):
        from shorts_engine.config.settings import settings
        env_key = getattr(settings, "SECRETS_MASTER_KEY", None)
        if env_key:
            monkeypatch.setenv("SECRETS_MASTER_KEY", env_key)
        else:
            from cryptography.fernet import Fernet
            test_key = Fernet.generate_key().decode()
            monkeypatch.setenv("SECRETS_MASTER_KEY", test_key)
    yield

