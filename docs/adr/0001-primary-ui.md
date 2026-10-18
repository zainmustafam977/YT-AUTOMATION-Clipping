# ADR 0001 — Primary UI is the React SPA at `/app`

- **Status:** Accepted
- **Date:** 2026-10-17
- **Context:** R4-3.1 / R4-3.2

## Context

The project ships two UI surfaces:

1. A **React single-page application** served at `/app` and developed in
   the `frontend/` directory.
2. A **Jinja2 / server-rendered** dashboard at `/` (the legacy admin).

There are also several **standalone publish pages** under
`/publish/<token>` that have accreted over the R1 / R2 work.

The R3 work shipped ~25 batch operations, 9 webhooks, a cost engine,
overlays, an A/B testing engine, a search/command palette, a triage
dashboard, a caption editor, and 18 other features. Some of these
exist in only one of the two surfaces; some are only reachable from
the standalone publish pages.

Operators using the project today must context-switch between at least
three different UIs to do one job, and the standalone publish pages
have grown stale relative to the SPA.

## Decision

1. The **primary UI is the React SPA at `/app`**. All new R3+ features
   ship in the SPA. No new feature may be added to the Jinja2 surface
   or to a standalone publish page.
2. The **Jinja2 surface is deprecated for new development** but is kept
   alive for the operations that are SPA-incompatible (raw log viewing,
   a few CLI-driven admin tasks). New R3+ feature code paths surface
   in the SPA; the Jinja2 surface either redirects to `/app` or shares
   the same API.
3. The **standalone publish pages are retired**. Each one is replaced
   by a docked right-side drawer in the SPA called the **Publishing
   Studio**, which contains the same flow plus a quota gauge, a direct
   video-ingestion drop zone, and an outbox.
4. The Jinja2 surface is removed entirely in v2.0.0; before then, a
   banner at the top of every Jinja2 page links to the SPA.

## Consequences

Positive:

- One source of truth for the operator workflow.
- New features ship in one place; testing is concentrated.
- The standalone publish pages no longer drift.

Negative:

- Operators who prefer the Jinja2 surface must migrate.
- The SPA must grow a few new components (Publishing Studio drawer
  with quota gauge, ingestion, outbox).
- R4-3 (UI parity) requires an audit of every R3 feature to confirm
  it is reachable in the SPA. This is tracked in `docs/PARITY.md`.

## Compliance

- **R4-3.1 (inventory):** the inventory is this ADR.
- **R4-3.2 (parity):** `docs/PARITY.md` lists every R3 feature and
  where it is implemented in the SPA. A blank cell is a defect.
- **R4-3.3 (Publishing Studio):** the dock/drawer is in the SPA; the
  Jinja2 surface links to it.
- **R4-3.4 (SPA CI):** the SPA job in `agent.yml` enforces lint,
  typecheck, unit, build on every push.
- **R4-3.5 (Playwright audit):** `tests/ui/test_dead_controls.py`
  enumerates every interactive element in the SPA.

## References

- R3 ledger: `docs/agent/ledger.yaml`
- R4 ledger: `docs/agent/ledger.yaml`
- Handover: `docs/HANDOVER/architecture.md`
- Parity matrix: `docs/PARITY.md`
