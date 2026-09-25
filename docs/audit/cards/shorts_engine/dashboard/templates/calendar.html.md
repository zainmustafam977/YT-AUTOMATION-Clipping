# Assessment Cards: shorts_engine/dashboard/templates/calendar.html

---

## Unit 1: Interactive FullCalendar View & Dark Styling
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/templates/calendar.html:1-84` |
| What it does | Embeds FullCalendar with customized dark theme CSS matching the dashboard design system. Displays publication counters for scheduled and published clips and provides a responsive calendar view (month, week, list). |
| Industrial-grade? | **A** - Clean, responsive integration with clear visual status distinctions. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (FullCalendar CDN). **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Standard FullCalendar v6 dark theme integration (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: Event Inspector Modal & Drag-and-Drop Rescheduling
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/templates/calendar.html:85-197` |
| What it does | Provides modal inspection on event click (thumbnail preview, verbatim hook text, virality score, channel, direct YouTube or Review Queue deep link). Enables drag-and-drop rescheduling via `eventDrop`, executing an asynchronous POST to `/api/calendar/reschedule/{clip_id}` with CSRF verification and automatic revert on error. Rejects rescheduling of already published clips. |
| Industrial-grade? | **A** - Intuitive scheduling UX with rollback guarantees and CSRF protection. |
| Checklist | **Error handling:** Pass (calls `info.revert()` if reschedule fails or if clip is already published). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (guards published clips from being dragged). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (passes CSRF header). **Performance:** Pass. **Observability:** Pass (toast notifications). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Asynchronous calendar drag-and-drop with optimistic revert (Recommended). |
| Priority / Effort | P3 / S |
