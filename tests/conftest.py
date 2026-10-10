import os

# Prevent OpenMP and BLAS threading runtime conflicts between torch, ctranslate2, and pyav on Linux
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

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
