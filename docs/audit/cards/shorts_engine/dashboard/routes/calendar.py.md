# Assessment Cards: shorts_engine/dashboard/routes/calendar.py

---

## Unit 1: calendar_view & calendar_events_feed
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/calendar.py:36-151` |
| What it does | `calendar_view` renders the interactive FullCalendar HTML interface. `calendar_events_feed` serializes scheduled clips and published clips into FullCalendar-compatible JSON event feeds with visual styling (cyan for scheduled, green for published) and clip metadata (hook text, thumbnail URL, channel name). |
| Industrial-grade? | **A** - Rich interactive editorial calendar backend with RBAC restrictions (`require_viewer`). |
| Checklist | **Error handling:** Pass (null-safe traversal of candidate, video, and channel). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (RBAC enforced, viewer role required). **Performance:** Pass (limits published logs to 100). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: FullCalendar JSON event feed (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: calendar_reschedule_clip
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/calendar.py:153-184` |
| What it does | Endpoint handling interactive drag-and-drop or modal rescheduling: validates ISO datetime, updates `clip.scheduled_publish_at`, promotes `WAITING_APPROVAL` to `APPROVED`, validates CSRF, and records operator audit logs. |
| Industrial-grade? | **A** - Secure scheduling endpoint with CSRF verification and operator role enforcement (`require_operator`). |
| Checklist | **Error handling:** Pass (validates ISO date format and clip existence). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (parses ISO timestamp). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass (commits on valid change). **External-dependency:** Pass. **Security:** Pass (enforces CSRF and operator RBAC). **Performance:** Pass. **Observability:** Pass (logs operator reschedule action). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Current interactive reschedule endpoint (Recommended). |
| Priority / Effort | P3 / S |
