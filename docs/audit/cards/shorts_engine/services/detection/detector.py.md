# Assessment Cards: shorts_engine/services/detection/detector.py

---

## Unit 1: DetectionService.__init__ & Channel Lookup
| Field | Content |
|---|---|
| Location | `shorts_engine/services/detection/detector.py:24-35` |
| What it does | Coordinates WebSub subscriber, lease renewal manager, and RSS poller components against a shared database session. |
| Industrial-grade? | **A** - Clean facade pattern uniting push and pull detection channels. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: DetectionService.on_new_video
| Field | Content |
|---|---|
| Location | `shorts_engine/services/detection/detector.py:36-78` |
| What it does | Idempotent handler invoked when a new YouTube upload is signaled via push or pull; queries existing records, registers new `SourceVideo` in `DETECTED` status, updates `channel.last_seen_video_id`, and commits. |
| Industrial-grade? | **B** - Idempotent query protects against sequential duplicates, but lacks `IntegrityError` catch on concurrent races. |
| Checklist | **Error handling:** Warning (does not handle concurrent `IntegrityError`). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass (sequential idempotency verified). **Input validation:** Pass (validates channel existence and active watch state). **Resource cleanup:** N/A. **Concurrency safety:** Warning (race condition between WebSub push and RSS pull could raise unhandled `IntegrityError`). **Atomicity:** Pass (`db.commit()`). **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass (indexed lookup). **Observability:** Pass (logs detection and queueing). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | If a WebSub push notification arrives at the exact same millisecond that the RSS poller detects the video, both threads might find `existing == None` and attempt `db.add()`, triggering an uncaught `sqlalchemy.exc.IntegrityError` on the unique constraint. |
| Improvement | Wrap `db.commit()` in `try ... except IntegrityError:` with `self.db.rollback()` to retrieve and return the concurrent record safely. |
| Better approach? | Option 1: Database unique constraint with `IntegrityError` recovery (Recommended). Option 2: Pre-query only (Current - vulnerable to concurrent race). |
| Priority / Effort | P1 / S |

---

## Unit 3: WebSub & RSS Integration Bridges
| Field | Content |
|---|---|
| Location | `shorts_engine/services/detection/detector.py:79-97` |
| What it does | Delegation methods for subscribing channels to WebSub hubs, renewing expiring leases, and wiring an `RSSPollLoop`. |
| Industrial-grade? | **A** - Clean orchestration delegates. |
| Checklist | **Error handling:** Pass. **Timeouts:** Pass. **Retries:** Pass. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |
