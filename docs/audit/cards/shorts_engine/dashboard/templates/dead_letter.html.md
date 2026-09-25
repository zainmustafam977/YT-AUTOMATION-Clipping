# Assessment Cards: shorts_engine/dashboard/templates/dead_letter.html

---

## Unit 1: Dead-Letter Failed Clips View
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/templates/dead_letter.html:1-70` |
| What it does | Displays all pipeline-failed clips with retry counter status (`MAX_RETRY_COUNT` bounds enforcement), expandable collapsible `<details>` traces for full Quality Gate diagnostics JSON, individual retry buttons (disabled when retries are exhausted), and dismiss triggers linked to the universal soft delete modal. |
| Industrial-grade? | **A** - Comprehensive operational failure inspection and controlled retry throttling. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** Pass (visually flags exhausted retries and disables buttons). **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (CSRF tokens included in POST forms). **Performance:** Pass. **Observability:** Pass (full trace collapsible). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Bootstrap 5 dark table with collapsible traces and CSRF retry forms (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: Dead-Letter Failed Videos View
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/templates/dead_letter.html:71-129` |
| What it does | Renders failed source video ingestions with YouTube links, retry counter badges, collapsible error logs, retry action forms, and dismissal options with selective file purging. |
| Industrial-grade? | **A** - High operational transparency for broken ingestion jobs. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** Pass. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (`csrf_token`). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Detailed error breakdown with direct YouTube link and retry controls (Recommended). |
| Priority / Effort | P3 / S |
