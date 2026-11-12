# tools/audit

Utility scripts used during engineering reviews and stress testing.

These are **not part of the runtime platform**. They live here so disposable
scratch scripts have a documented home instead of littering the repo root.

Current retained items:
- `coverage_check.py`
- `read_chunk.py`
- `check_db.py`
- `stress_test_orchestrator.py`

If a script here is no longer useful, remove it rather than aging it in place.
