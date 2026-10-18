# R3 Feature Parity Matrix

Every R3 feature, indexed by ledger row, with the UI surfaces that
expose it. A blank cell is a defect and must be filed as an issue.

| R-row | Title | React SPA (`/app`) | Jinja2 (`/`) | Standalone publish | CLI | API |
|---|---|---|---|---|---|---|
| R1.1 | Worker pool | implemented | shares-api | n/a | implemented | `/api/workers` |
| R1.2 | Hot-folder watcher | implemented | implemented | n/a | implemented | `/api/hotfolder` |
| R1.3 | Source ingestion | implemented | implemented | n/a | implemented | `/api/ingest` |
| R1.4 | Clip selection | implemented | implemented | n/a | implemented | `/api/clips` |
| R1.5 | Render pipeline | implemented | shares-api | n/a | implemented | `/api/render` |
| R1.6 | Caption engine | implemented | implemented | n/a | implemented | `/api/captions` |
| R1.7 | Upload queue | implemented | implemented | n/a | implemented | `/api/uploads` |
| R1.8 | Dashboard | implemented | implemented | n/a | n/a | `/api/dashboard` |
| R2.1 | Storage | shares-api | shares-api | n/a | implemented | `/api/storage` |
| R2.2 | Settings | implemented | implemented | n/a | implemented | `/api/settings` |
| R2.3 | Telemetry | implemented | implemented | n/a | n/a | `/api/metrics` |
| R2.4 | Search + palette | implemented | n/a | n/a | n/a | `/api/search` |
| R2.5 | Triage | implemented | implemented | n/a | n/a | `/api/triage` |
| R2.6 | Saved views | implemented | n/a | n/a | n/a | `/api/views` |
| R2.7 | Calendar | implemented | n/a | n/a | n/a | `/api/calendar` |
| R2.8 | A/B testing | implemented | n/a | n/a | implemented | `/api/ab` |
| R2.9 | Cost engine | implemented | implemented | n/a | implemented | `/api/cost` |
| R2.10 | Webhooks | implemented | implemented | n/a | implemented | `/api/webhooks` |
| R2.11 | Batch ops (25) | implemented | partial | n/a | implemented | `/api/batch` |
| R2.12 | Overlay engine | implemented | n/a | n/a | implemented | `/api/overlays` |
| R2.13 | Caption editor | implemented | n/a | n/a | n/a | `/api/captions/<id>/edit` |
| R2.14 | Watermark | implemented | n/a | n/a | implemented | `/api/overlays/watermark` |
| R2.15 | CLI ergonomics | n/a | n/a | n/a | implemented | n/a |
| R2.16 | Audit log | implemented | implemented | n/a | n/a | `/api/audit` |
| R2.17 | File manifest | n/a | n/a | n/a | implemented | `/api/manifest` |
| R2.18 | SSRF guard | n/a | n/a | n/a | n/a | enforced at API gateway |
| R2.19 | Secrets handling | n/a | n/a | n/a | implemented | enforced |
| R2.20 | Settings export/import | implemented | n/a | n/a | implemented | `/api/settings/export` |
| R3.1 | OAuth reconcile | implemented | implemented | n/a | implemented | `/api/oauth` |
| R3.2 | Quota ledger | implemented | implemented | n/a | n/a | `/api/quota` |
| R3.3 | Captions style | implemented | n/a | n/a | n/a | `/api/captions/styles` |
| R3.4 | Reframer | implemented | n/a | n/a | n/a | `/api/reframer` |
| R3.5 | Visual context | implemented | n/a | n/a | n/a | `/api/visual` |
| R3.6 | Local ingest | implemented | n/a | n/a | implemented | `/api/ingest/local` |
| R3.7 | Watermark presets | implemented | n/a | n/a | n/a | `/api/overlays/presets` |
| R3.8 | API key mgmt | implemented | n/a | n/a | n/a | `/api/keys` |
| R3.9 | WebSub + polling | shares-api | shares-api | n/a | implemented | `/api/discovery` |
| R3.10 | Auto upload | implemented | n/a | n/a | n/a | `/api/uploads/auto` |
| R4-4.1 | Upload detection | implemented | shares-api | n/a | n/a | `/api/discovery/status` |
| R4-4.2 | Golden-set | shares-api | n/a | n/a | n/a | `/api/quality` |
| R4-4.3 | Caption styles | implemented | n/a | n/a | n/a | `/api/captions/styles` |
| R4-4.4 | Reframe | shares-api | n/a | n/a | n/a | `/api/reframer` |
| R4-4.5 | Visual context | shares-api | n/a | n/a | n/a | `/api/visual` |
| R4-4.6 | Watermark | implemented | n/a | n/a | n/a | `/api/overlays/watermark` |
| R4-4.7 | OAuth | implemented | implemented | n/a | n/a | `/api/oauth` |
| R4-4.8 | Auto-upload | implemented | n/a | n/a | n/a | `/api/uploads/auto` |
| R4-4.9 | API key mgmt | implemented | n/a | n/a | n/a | `/api/keys` |
| R4-4.10 | Local ingest | implemented | n/a | n/a | n/a | `/api/ingest/local` |
| R4-4.11 | Double-click launcher | n/a | n/a | n/a | n/a | desktop launcher (not a UI surface) |

Legend:
- `implemented` — feature is in this surface.
- `partial` — feature is half-done; see linked issue.
- `shares-api` — surface is a thin client over the same API.
- `n/a` — feature does not apply to this surface.
- `redirect` — surface links to where the feature is.

If a row has a blank cell where a feature should exist, file an issue
with the `parity` label.
