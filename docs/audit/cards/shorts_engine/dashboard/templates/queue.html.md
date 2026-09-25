# Assessment Cards: shorts_engine/dashboard/templates/queue.html

---

## Unit 1: Multi-Facet Search Toolbar, Undo Banner & Bulk Action Bar
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/templates/queue.html:1-167` |
| What it does | Provides full search and filtering controls (keyword search, channel filter, AI provenance selector, sorting by date/score/duration, page limits), a reversible rejection alert banner with 1-click restore, and a floating bulk action bar showing dynamic selection counts with bulk approve and reject buttons. |
| Industrial-grade? | **A** - High-density filter bar with undo safeguards and bulk selection capabilities. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (CSRF tokens included in all action forms). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Bootstrap 5 dark filter bar with floating bulk action panel (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: Grouped Cards & Flat Table Views with Multi-Gate Telemetry
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/templates/queue.html:168-660` |
| What it does | Dual-mode clip presentation: hierarchical grouping by source video/date (card grid) and dense tabular display (flat table). Renders virality metrics, ML predicted scores, and 4-tier Quality Gate status badges (Technical, Framing, Semantic, Advisory) with contextual tooltips and full action button suites. Includes responsive pagination controls. |
| Industrial-grade? | **A** - High operational fidelity providing instant visual status across video quality criteria. |
| Checklist | **Error handling:** Pass (graceful image fallback onerror handler). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (HTMX and form POSTs protected by CSRF). **Performance:** Pass. **Observability:** Pass (multi-tier gate badges). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Dual-layout view with multi-tier quality gate badges (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 3: 9:16 Video Player Modal & Web Audio Synthesizer Chime
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/templates/queue.html:661-765` |
| What it does | 9:16 aspect ratio vertical video preview modal with clean play/pause and source unmounting lifecycle. Connects to `/api/events/dashboard` SSE to track new incoming candidate clips in real-time, synthesizing a two-tone A5/D6 chime via the browser's native Web Audio API (`AudioContext`) when new items arrive. |
| Industrial-grade? | **A** - Zero external audio asset dependencies via Web Audio API synthesis, clean player memory cleanup. |
| Checklist | **Error handling:** Pass (catches audio playback errors, unmounts video `src` on modal hide). **Timeouts:** N/A. **Retries:** Pass (EventSource reconnect). **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (clears video source on close to stop buffering). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (browser Web Audio API). **Security:** Pass. **Performance:** Pass (no audio assets loaded over network). **Observability:** Pass. **Config:** Pass (persists chime preference in `localStorage`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Native Web Audio synthesizer chime + 9:16 modal player (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 4: View Mode Switcher, Bulk Selection & Caption Style Switcher Modal
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/templates/queue.html:766-1200` |
| What it does | Manages persistent view mode toggling (stored in `localStorage`), synchronized checkbox selections with indeterminate master checkboxes, and group-level bulk approvals/rejections. Embeds the Caption Style Switcher modal supporting 5 visual presets (Hormozi, MrBeast, Cyberpunk Neon, Minimalist, High Contrast Box) and 3s hook overlay banner re-renders. |
| Industrial-grade? | **A** - High creative versatility allowing rapid subtitle aesthetic changes without re-transcribing. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (passes CSRF tokens on style re-renders). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Interactive preset switcher with hook overlay burn-in (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 5: Clip Recovery Diagnostics & Automated Auto-Fixes
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/templates/queue.html:1201-1313, 1483-1600` |
| What it does | Diagnostic modal inspecting rejected or failing clips. Fetches live diagnostics (`/queue/clip/{id}/diagnostics`) showing pass/fail status and exact error strings across Technical, Framing, and Semantic gates. Displays automated auto-fix buttons (Duration Clamp, Caption Margin Shift, Dialogue Re-Anchor) dynamically based on failure mode, and provides Force-Approve overrides. |
| Industrial-grade? | **A** - Actionable failure remediation workflow enabling 1-click repairs directly from the queue. |
| Checklist | **Error handling:** Pass (handles diagnostics fetch errors). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (CSRF headers on recovery actions). **Performance:** Pass. **Observability:** Pass (displays exact gate failure details). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Dedicated diagnostics modal with contextual auto-fix buttons (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 6: Automated YouTube Publication Scheduling Modal
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/templates/queue.html:1314-1420` |
| What it does | Date/time picker modal for scheduling approved clips. Offers 1-click quick presets (+1 Hour, Tomorrow 9:00 AM, Tomorrow 5:00 PM, Prime Time Weekend) and posts ISO timestamps to `/queue/{id}/schedule` with CSRF protection. |
| Industrial-grade? | **A** - Streamlined drip-feed publishing UX with practical scheduling presets. |
| Checklist | **Error handling:** Pass (validates non-empty clip ID and datetime). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (CSRF protected). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Modal scheduling with automated prime-time presets (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 7: Keyboard Navigation Hotkeys & Sub-Frame Precision Trimmer Modal
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/templates/queue.html:1421-1823` |
| What it does | Power-user keyboard hotkeys (j/k for card navigation, a for approve, r for reject, space for video playback). Embeds the Sub-Frame Precision Trimmer modal with +/-0.25s and +/-1.0s nudge buttons, visual sweet spot duration indicators (25-45s green badge), and natural speech pause snapping toggle. |
| Industrial-grade? | **A** - High-speed operator review workflows with sub-frame video editing precision. |
| Checklist | **Error handling:** Pass (enforces 10.0s minimum clip duration). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (minimum duration validation). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (`csrf_token`). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keyboard hotkeys + nudge-based speech-snapping trimmer (Recommended). |
| Priority / Effort | P3 / S |
