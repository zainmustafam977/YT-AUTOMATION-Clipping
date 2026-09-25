# Assessment Cards: shorts_engine/dashboard/routes/channels.py

---

## Unit 1: Channel Listing & JSON API
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/channels.py:38-79` |
| What it does | Serves the channel management HTML dashboard (`/channels`) with WebSub callback status indicators, RSS polling interval details, and provides a programmatic JSON API (`/api/channels`). |
| Industrial-grade? | **A** - Clean listing endpoints with RBAC viewer permissions. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (enforces `require_viewer`, CSRF token passed to template). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Standard channel view (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: Channel Toggles & Triggers (Watch, Review, Poll, WebSub)
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/channels.py:81-204` |
| What it does | HTMX-optimized interactive state toggles (`toggle_watch`, `toggle_review`) returning dynamic HTML button fragments with toast triggers, and manual trigger endpoints (`trigger_poll`, `subscribe_websub`) for immediate ingestion or hub renewal. |
| Industrial-grade? | **A** - Highly responsive HTMX integration with complete CSRF and operator RBAC enforcement. |
| Checklist | **Error handling:** Pass (raises HTTP 404 for missing channels). **Timeouts:** Pass (uses poller/subscriber timeouts). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass (commits state changes). **External-dependency:** Pass. **Security:** Pass (`require_operator`, `verify_csrf`). **Performance:** Pass. **Observability:** Pass (audit logs with username and action details). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: HTMX outerHTML swap with toast triggers (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 3: add_channel & delete_channel
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/channels.py:206-332` |
| What it does | `add_channel` extracts 24-character UC channel IDs from URLs, rejects unsupported @handles with actionable error messages, checks duplicate registration, and pre-fetches the latest video ID when skip-backfill is enabled. `delete_channel` deletes channel records with optional cascading file wipe (`delete_files=True`) purging both hierarchical directories and legacy flat media files. |
| Industrial-grade? | **A** - Comprehensive lifecycle management with robust input validation and thorough file cleanup. |
| Checklist | **Error handling:** Pass (catches duplicate registration HTTP 409, invalid format HTTP 422). **Timeouts:** Pass. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (regex `^UC[a-zA-Z0-9_-]{22}$`). **Resource cleanup:** Pass (cascading safe unlinks and directory pruning). **Concurrency safety:** Pass. **Atomicity:** Pass (DB transaction commit). **External-dependency:** Pass. **Security:** Pass (`require_operator`, `verify_csrf`). **Performance:** Pass. **Observability:** Pass (detailed operator audit logs). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None of significance. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Regex channel ID validation + optional cascading disk wipe (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 4: api_channel_videos Explorer
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/channels.py:334-427` |
| What it does | Multi-tiered video explorer: retrieves channel's latest videos from RSS Atom feed, falls back to `yt-dlp` flat metadata extraction, falls back to existing DB videos, reconciles each item against the database to determine ingestion status and clip count, and filters by search query. |
| Industrial-grade? | **A** - Resilient channel video inspection allowing operators to browse public uploads and verify ingestion status. |
| Checklist | **Error handling:** Pass (gracefully catches RSS and yt-dlp exceptions, falls back across 3 sources). **Timeouts:** Pass (poller 8s timeout). **Retries:** Pass (tiered fallbacks). **Idempotency:** Pass. **Input validation:** Pass (guards channel existence). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (YouTube RSS / yt-dlp). **Security:** Pass (`require_viewer`). **Performance:** Pass (fast flat extraction, bounded by limit). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Multi-tiered metadata discovery with status reconciliation (Recommended). |
| Priority / Effort | P3 / S |
