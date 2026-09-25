# Assessment Cards: shorts_engine/services/publishing/instagram.py

---

## Unit 1: InstagramReelsPublisher.__init__ & health_check
| Field | Content |
|---|---|
| Location | `shorts_engine/services/publishing/instagram.py:32-59` |
| What it does | Initializes Meta Graph API v19.0 integration with access token and Instagram User ID, and verifies connectivity/token validity against `GET /{ig_user_id}?fields=username,name`. |
| Industrial-grade? | **A** - Clean credentials check with account handle verification. |
| Checklist | **Error handling:** Pass (catches missing credentials and HTTP error codes). **Timeouts:** Pass (10.0s HTTP timeout). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (uses context manager). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (Meta Graph API). **Security:** Pass (no hardcoded tokens). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Meta Graph API health check (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: InstagramReelsPublisher Media Upload & Polling
| Field | Content |
|---|---|
| Location | `shorts_engine/services/publishing/instagram.py:60-159` |
| What it does | Implements Meta's two-phase media ingestion lifecycle: creates container via URL or initializes a resumable upload session (`upload_type="resumable"`), streams binary video, and polls `GET /{container_id}` status until reaching `FINISHED`, `ERROR`, or `EXPIRED`. |
| Industrial-grade? | **B+** - Full Meta Reels state machine implementation, but reads entire video file into memory during direct upload. |
| Checklist | **Error handling:** Pass (detects Meta container errors and timeouts). **Timeouts:** Pass (bounded polling loop). **Retries:** Pass (polls up to 20 attempts with backoff). **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (Meta Graph API). **Security:** Pass. **Performance:** Needs minor improvement (reads entire video bytes into memory with `f.read()`). **Observability:** Pass (debug logs on polling attempts). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | In `_upload_direct_binary` (line 128), `content=f.read()` loads the full file into memory at once. For multi-minute or high-bitrate clips, this can spike memory consumption across parallel workers. |
| Improvement | Stream the file upload via an iterator or chunked generator in `httpx.post(..., content=stream_file(video_path))`. |
| Better approach? | Option 1: Streaming file upload (Recommended). Option 2: Full-memory `f.read()` (Current). |
| Priority / Effort | P2 / S |

---

## Unit 3: InstagramReelsPublisher.publish
| Field | Content |
|---|---|
| Location | `shorts_engine/services/publishing/instagram.py:160-274` |
| What it does | Publishes container via `/{ig_user_id}/media_publish`, fetches permalink, supports `dry_run` simulation, validates file existence, formats hashtags, and returns standardized `PlatformPublishResult`. |
| Industrial-grade? | **A-** - Complete Strategy implementation with dry-run simulation mode. |
| Checklist | **Error handling:** Pass (isolates upload errors into PlatformPublishResult). **Timeouts:** Pass (30s client timeout). **Retries:** Pass. **Idempotency:** Pass. **Input validation:** Pass (checks video file existence and size). **Resource cleanup:** Pass (httpx.Client context manager). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (Meta Graph API). **Security:** Pass. **Performance:** Pass. **Observability:** Pass (returns permalink and container metadata). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Strategy-compliant Meta publisher (Recommended). |
| Priority / Effort | P3 / S |
