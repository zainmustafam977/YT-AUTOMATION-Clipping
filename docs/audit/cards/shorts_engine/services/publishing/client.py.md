# Assessment Cards: shorts_engine/services/publishing/client.py

---

## Unit 1: _build_youtube_client & _upload_video
| Field | Content |
|---|---|
| Location | `shorts_engine/services/publishing/client.py:22-88` |
| What it does | `_build_youtube_client` constructs an authenticated Google API client using decrypted OAuth credentials. `_upload_video` performs a resumable chunked upload (`MediaFileUpload`, 256 KB chunk size) to YouTube Data API v3 `videos().insert()` with category "22" and `selfDeclaredMadeForKids=False`. |
| Industrial-grade? | **B+** - Standard resumable Google client upload, but lacks automatic chunk retry backoff. |
| Checklist | **Error handling:** Pass (validates returned video ID). **Timeouts:** Needs improvement (depends on googleapiclient default socket timeout). **Retries:** Fail (no exponential backoff on transient chunk upload errors like HTTP 500/503). **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (Google API client). **Security:** Pass (uses ephemeral decrypted memory credentials). **Performance:** Pass (resumable upload). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | The `next_chunk()` loop lacks exponential backoff; a temporary dropped connection or HTTP 503 during a 30 MB video upload aborts the entire upload rather than resuming the current chunk. |
| Improvement | Wrap `request.next_chunk()` in an exponential backoff loop with 3 retries for transient socket and 5xx errors. |
| Better approach? | Option 1: Exponential backoff on chunk upload (Recommended). Option 2: Bare `next_chunk` loop (Current). |
| Priority / Effort | P2 / S |

---

## Unit 2: YouTubePublisher.publish_clip
| Field | Content |
|---|---|
| Location | `shorts_engine/services/publishing/client.py:95-192` |
| What it does | Publishes a clip to YouTube: enforces strict idempotency check (returns existing `PublishLog` if already `PUBLISHED`), verifies daily API quota availability, sets `clip.status = PUBLISHING`, supports `dry_run` simulation, decrypts channel OAuth token, uploads video, records quota usage, records `PublishLog`, and updates `clip.status = PUBLISHED` atomically. |
| Industrial-grade? | **A-** - High-integrity publishing flow with idempotency protection and pre-flight quota checking. |
| Checklist | **Error handling:** Pass (checks file existence, validates token). **Timeouts:** Pass. **Retries:** Pass. **Idempotency:** Pass (explicitly prevents duplicate publish). **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass (sets `PUBLISHING` status). **Atomicity:** Pass (DB commits state changes). **External-dependency:** Pass (YouTube Data API v3). **Security:** Pass (channel tokens decrypted on-the-fly via Fernet AES). **Performance:** Pass. **Observability:** Pass (writes dedicated `PublishLog` entry with timestamp and metadata). **Config:** Pass (reads `DRY_RUN_PUBLISHING` and `DEFAULT_PRIVACY_STATUS`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | If an unhandled exception or crash occurs during live upload after setting `ClipStatus.PUBLISHING`, the clip remains stuck in `PUBLISHING` indefinitely without automatic timeout reset. |
| Improvement | Wrap live upload in a `try...except` block that resets `clip.status = ClipStatus.APPROVED` or `FAILED` on exception. |
| Better approach? | Option 1: Automatic error status rollback (Recommended). Option 2: External cleanup (Current). |
| Priority / Effort | P2 / S |

---

## Unit 3: YouTubePublisher.update_video_title
| Field | Content |
|---|---|
| Location | `shorts_engine/services/publishing/client.py:194-254` |
| What it does | Updates YouTube video title via `videos().list(part="snippet")` and `videos().update(part="snippet")` for Bayesian Multi-Armed Bandit headline optimization. Supports simulation in `dry_run` mode. |
| Industrial-grade? | **A** - Clean metadata patch implementation used by automated optimization daemons. |
| Checklist | **Error handling:** Pass (validates video existence on YouTube before update). **Timeouts:** Pass. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (sanitizes and clamps title to 100 characters). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (YouTube API). **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Standard snippet patch (Recommended). |
| Priority / Effort | P3 / S |
