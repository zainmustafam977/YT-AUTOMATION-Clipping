"""R4-8.6 — Contract tests for platform APIs.

Contract tests assert that the code uses a platform API (YouTube, OpenAI,
etc.) with the exact shape the platform expects. We use **recorded
fixtures** (HTTP request/response pairs captured once against the live
API) instead of hitting the live API on every CI run.

Refresh procedure (owner-only, manual):

  1. Set ``SHORTS_ENGINE_REFRESH_FIXTURES=1`` and the real platform
     credentials in env.
  2. Run ``python -m tests.contract.refresh`` to overwrite fixtures.
  3. Commit the updated JSON to ``tests/contract/fixtures/``.

The fixtures are versioned and the test runner asserts the recorded
fixture's ``api_version`` matches the version the platform currently
documents. Mismatches fail loudly so the owner knows to refresh.
"""
