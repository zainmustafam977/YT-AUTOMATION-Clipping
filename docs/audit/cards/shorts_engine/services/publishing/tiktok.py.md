# Assessment Cards: shorts_engine/services/publishing/tiktok.py

---

## Unit 1: TikTokPublisher.__init__ & health_check
| Field | Content |
|---|---|
| Location | `shorts_engine/services/publishing/tiktok.py:33-72` |
| What it does | Initializes TikTok Content Posting API v2 integration with access credentials, and performs a live connectivity check against `POST /v2/post/publish/creator_info/query/`. |
| Industrial-grade? | **A** - Clean pre-flight health probe validating token scope and endpoint reachability. |
| Checklist | **Error handling:** Pass (catches missing credentials and API error payloads). **Timeouts:** Pass (10.0s HTTP timeout). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (uses context manager). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (TikTok Open API). **Security:** Pass (Bearer token headers, no secrets logged). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Standard creator info query (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: TikTokPublisher._init_chunked_upload & _upload_chunks
| Field | Content |
|---|---|
| Location | `shorts_engine/services/publishing/tiktok.py:90-161` |
| What it does | Implements TikTok Content Posting API v2 chunked upload: calculates total 10 MB chunks (`CHUNK_SIZE_BYTES = 10 MB`), posts `/v2/post/publish/video/init/`, and uploads byte-range slices via HTTP PUT with `Content-Range: bytes {start}-{end}/{total}` headers. |
| Industrial-grade? | **A-** - High-performance chunked file streamer adhering to TikTok's 5MB–64MB chunk constraints without excessive RAM consumption. |
| Checklist | **Error handling:** Pass (checks response status codes 200, 201, 206). **Timeouts:** Pass (60s chunk timeout). **Retries:** Fail (no retry loop on individual failed chunk PUT requests). **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (file handle closed after chunk loop). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (TikTok presigned upload endpoint). **Security:** Pass. **Performance:** Pass (10 MB buffer avoids loading full video in RAM). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | If a network glitch causes an individual chunk PUT to fail mid-upload, the loop raises a `RuntimeError` rather than retrying the failed byte range. |
| Improvement | Add a 3-attempt retry loop per chunk with exponential backoff on transient network errors. |
| Better approach? | Option 1: Per-chunk retry loop with backoff (Recommended). Option 2: Single-attempt chunk loop (Current). |
| Priority / Effort | P2 / S |

---

## Unit 3: TikTokPublisher._poll_publish_status & publish
| Field | Content |
|---|---|
| Location | `shorts_engine/services/publishing/tiktok.py:162-276` |
| What it does | Coordinates full publishing flow: creator info verification, privacy level reconciliation, chunked upload, status polling for `PUBLISH_COMPLETE` or `AUDIT_REJECT`, and returns standardized `PlatformPublishResult` with permalink. Supports `dry_run` simulation. |
| Industrial-grade? | **A-** - Comprehensive end-to-end integration handling TikTok's async audit queue with simulation mode. |
| Checklist | **Error handling:** Pass (detects AUDIT_REJECT and FAILED statuses, wraps exceptions). **Timeouts:** Pass (bounded polling loop). **Retries:** Pass. **Idempotency:** Pass. **Input validation:** Pass (checks video file existence and non-zero size). **Resource cleanup:** Pass (httpx.Client context manager). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (TikTok Content Posting API). **Security:** Pass. **Performance:** Pass. **Observability:** Pass (debug logs on polling, structured result). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None of significance. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Strategy-compliant TikTok publisher (Recommended). |
| Priority / Effort | P3 / S |
