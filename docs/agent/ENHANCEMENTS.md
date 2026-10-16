# ENHANCEMENTS

This file is generated from `docs/agent/ledger.yaml`.
Hand-editing is forbidden; edit the ledger instead.

## A..O quality matrix (before -> after, scored 0-3)

| Letter | Attribute | Before | After | Evidence |
|--------|-----------|--------|-------|----------|
| A | Reliability / resilience | 1 | 3 | durable queue + idempotency + crash-recover + drain probes |
| B | Security / access control | 1 | 3 | Fernet at rest + CSRF + session timeout + RBAC + url_guard + secret redaction |
| C | Performance / scalability | 1 | 2 | 1k-item scale test in docs/agent/E2E_EVIDENCE.md; multi-worker deferred (R-F-07) |
| D | Observability / debuggability | 1 | 3 | prometheus /metrics + correlation IDs + structured logs + audit log + /events SSE |
| E | Maintainability / code quality | 1 | 3 | ruff + mypy ratchet + coverage floor 80% + ledger + pre-commit + ADRs |
| F | Testability | 1 | 3 | 1000+ tests; E2E S1..S8; sabotage checks; chaos suite for non-deterministic stages |
| G | Documentation | 1 | 3 | RUNBOOK + THREAT_MODEL + ADRs + agent docs + spec files + E2E evidence |
| H | API design | 1 | 3 | REST + OpenAPI generated; pydantic validation; consistent error envelope |
| I | Interop / integration | 1 | 3 | YouTube + TikTok + IG Reels syndication; webhooks; settings I/O |
| J | Compliance / governance | 1 | 3 | Rights ledger + pre-registration + mode-aware guards + audit log |
| K | Cost efficiency | 1 | 3 | Per-clip cost roll-up + soft/hard budget + alert + LLM failover |
| L | Operator UX | 1 | 3 | Needs-attention + doctor + triage + setup wizard + command palette + WCAG |
| M | Disaster recovery | 1 | 3 | cli backup-db/restore-db + retention + house-keeping + drain |
| N | Data integrity / consistency | 1 | 3 | transactional outbox + CAS claim + unique constraints + idempotency keys |
| O | Operability / deployability | 1 | 3 | liveness/readiness probes + drain + Dockerfile + docker-compose + Caddyfile |

## Enhancements (E-001..E-033) — see ledger for evidence

* **E-001** — Batch engine (preview/confirm/run/pause/cancel/retry-failed) (DONE-VERIFIED)
* **E-002** — Batch approve (DONE-VERIFIED)
* **E-003** — Batch reject (with reason code) (DONE-VERIFIED)
* **E-004** — Batch schedule (slot assignment) (DONE-VERIFIED)
* **E-005** — Batch publish (quota-aware, jittered) (DONE-VERIFIED)
* **E-006** — Needs attention home view (DONE-VERIFIED)
* **E-007** — Doctor preflight command (DONE-VERIFIED)
* **E-008** — Webhook notifications (with url_guard + payload redaction) (DONE-VERIFIED)
* **E-009** — Settings export/import (with secret redaction + dry-run diff) (DONE-VERIFIED)
* **E-010** — Per-clip cost roll-up (with soft/hard budget + alert) (DONE-VERIFIED)
* **E-011** — Multi-layer overlay engine (multi-layer + presets + batch apply + preview + audit) (DONE-VERIFIED)
* **E-012** — Overlay preset export/import (SHA-256 tamper detection) (DONE-VERIFIED)
* **E-013** — Batch retry-failed / requeue (DONE-VERIFIED)
* **E-014** — Bulk title/description/tags edit with template + find-and-replace + per-item diff preview (DONE-VERIFIED)
* **E-015** — Batch ingest of URLs (paste / CSV / text-file / de-duplication report) (DONE-VERIFIED)
* **E-016** — Caption editor (per-word CRUD + reset + audit) (DONE-VERIFIED)
* **E-017** — A/B title test framework (Wilson-score, round-robin, audit) (DONE-VERIFIED)
* **E-018** — Single-clip preview page (composing overlays + captions + A/B) (DONE-VERIFIED)
* **E-019** — Triage console (severity-ordered categories + JSON endpoint) (DONE-VERIFIED)
* **E-020** — First-run setup wizard + one-command bootstrap (DONE-VERIFIED)
* **E-021** — Command palette + global search (DONE-VERIFIED)
* **E-022** — WCAG 2.2 AA + dark mode + i18n strings (DONE-VERIFIED)
* **E-023** — CLI ergonomics (--json --yes --dry-run --filter shell completions exit codes) (DONE-VERIFIED)
* **E-024** — Quota planner + tracker (daily/weekly/monthly forecast) (DONE-VERIFIED)
* **E-025** — Dead-letter viewer + replay (DONE-VERIFIED)
* **E-026** — Near-duplicate detection (perceptual hash) (DONE-VERIFIED)
* **E-027** — Thumbnail scoring (face + contrast + entropy) (DONE-VERIFIED)
* **E-028** — Analytics roll-up (per-channel / per-day / per-format) (DONE-VERIFIED)
* **E-029** — Content calendar (drag-drop scheduling view) (DONE-VERIFIED)
* **E-030** — Full-pipeline dry-run mode (no side effects) (DONE-VERIFIED)
* **E-031** — Per-channel templates and profiles (DONE-VERIFIED)
* **E-032** — Best-time-to-publish suggestions (per channel) (DONE-VERIFIED)
* **E-033** — Operations CLI beyond doctor (status, reconcile, watch, drain) (DONE-VERIFIED)
