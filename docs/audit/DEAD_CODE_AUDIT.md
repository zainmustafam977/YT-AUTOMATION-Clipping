# Dead / Unwired Feature Audit (T-P2-3)

Audited 2026-11-11 against HEAD.

- Module-level orphan scan: no Python module under `shorts_engine/` has zero name references across `shorts_engine/` and `tests/`.
- `telegram_notify`: no references anywhere (already absent).
- Webhooks service (`shorts_engine/services/webhooks/`): actively referenced by models, routes, and tests.
- CLI/ergonomics: `cli.py`/`run.py` present and referenced by Dockerfile CMD and docs; no dead entrypoints identified.

Conclusion: no safe code removals identified in this pass; all surveyed features are referenced. Further dead-code detection requires runtime coverage profiling (deferred).