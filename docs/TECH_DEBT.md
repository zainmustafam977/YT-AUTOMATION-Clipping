# Tech-Debt Register (R4-8.5)

Each entry: id, description, owner (the team / person who will close
it), target version, severity (LOW / MED / HIGH).

| ID | Description | Owner | Target | Severity |
|----|-------------|-------|--------|----------|
| TD-001 | Some routes have no explicit auth (relies on global middleware). Add per-route auth assertions in tests. | platform-team | v1.4 | MED |
| TD-002 | `services/publishing/client.py` has YouTube-specific coupling; abstract behind a `Publisher` interface for future platforms. | platform-team | v1.5 | MED |
| TD-003 | `webhooks_dead_letter` table is unbounded; add a 90-day retention job. | platform-team | v1.4 | LOW |
| TD-004 | LLM model router is heuristic-based; replace with a learned router once we have 90 days of telemetry. | ai-team | v2.0 | LOW |
| TD-005 | `services/worker/daemon.py` uses polling; switch to Postgres LISTEN/NOTIFY when ported to PG. | platform-team | v2.0 | LOW |
| TD-006 | `services/execution/hwaccel.py` only probes by encoder name; add NVENC runtime probe to avoid false positives. | video-team | v1.4 | MED |
| TD-007 | `tests/test_publish_slots.py` has a skip on a specific date; replace with `freezegun` once that's added. | platform-team | v1.4 | LOW |
| TD-008 | Some long-running tests are flaky; investigate and stabilise. | qa-team | v1.4 | MED |
| TD-009 | The unified .env loader doesn't currently warn on deprecated keys. | platform-team | v1.4 | LOW |
| TD-010 | `services/ab_testing/thumbnail_generator.py` uses heuristic font choice; integrate with the new overlay engine. | video-team | v1.5 | MED |
| TD-011 | The dashboard's auth uses session cookies; add CSRF tokens for state-changing forms. | security-team | v1.4 | HIGH |
| TD-012 | Add a contract-test fixture for the YouTube API refresh. | platform-team | v1.5 | MED |

## How to add a new entry

Append a row to the table. Keep the table sorted by id. Reference
`TD-XXX` in commit messages and PR titles so debt is traceable.
