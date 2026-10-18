# Test strategy (R4-7.1)

## Layers

| Layer | Tool | What it covers |
|-------|------|----------------|
| Unit | `pytest tests/` | Each module's behaviour, mocked where needed |
| Integration | `pytest tests/test_batch_engine.py` etc. | Real DB, real ffmpeg (in CI), real ffmpeg in dev |
| Concurrency | `pytest tests/test_sqlite_concurrency.py` | WAL, retry, contention |
| E2E | `pytest tests/e2e/` | Full HTTP flow with FastAPI TestClient |
| Fresh-eyes | `pytest tests/test_fresh_eyes.py` | Independent-runner gates |
| Sabotage | `pytest tests/test_sabotage_*.py` | "If this control is removed, what breaks?" |
| Scale | `pytest tests/test_scale_1k_batch.py` | 1000-item batch |
| Smoke (post-deploy) | `python -m tools.smoke` | Health / readiness / live |

## How to run each suite

```bash
# All non-network tests
.venv/bin/pytest -q -m "not network"

# Just the sabotage suite
.venv/bin/pytest -q tests/test_sabotage_*.py

# E2E only
.venv/bin/pytest -q tests/e2e/

# Scale only
.venv/bin/pytest -q tests/test_scale_1k_batch.py

# Fresh-eyes (independent runner; useful in CI to assert no flakiness)
.venv/bin/pytest -q tests/test_fresh_eyes.py

# Concurrency
.venv/bin/pytest -q tests/test_sqlite_concurrency.py
```

## Markers

* `network` — test hits external network; skipped in CI by default.
* `real_ffmpeg` — test requires a real ffmpeg; skipped if absent.
* `e2e` — full HTTP flow; runs in the dedicated CI job.
* `slow` — slow tests; only run in nightly.

## Coverage

Per R4 quality gate:

* `pytest --cov=shorts_engine --cov-report=term-missing` — coverage report.
* CI gate fails if a PR reduces overall coverage by more than 0.5%.
* Critical paths (publish, oauth, ingest) are pinned at 90%+.

## Sabotage suite philosophy

Every R3 / R4 control has a paired sabotage test that:

1. Asserts the control is in place (positive test).
2. Removes the control (e.g. monkey-patches the redactor to a no-op).
3. Asserts that the system detects the missing control and either
   blocks the operation or returns a clear error.

The sabotage suite is the regression net for STRICT_MODE (R4-5.2) and
the security model. A passing sabotage test means "if the control
breaks, we'll know".
