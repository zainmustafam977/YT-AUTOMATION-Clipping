# Assessment Cards: shorts_engine/services/broll/client.py

---

## Unit 1: Pexels B-Roll Stock Footage Client & Local Cache
| Field | Content |
|---|---|
| Location | `shorts_engine/services/broll/client.py:1-123` |
| What it does | Discovers, downloads, and caches stock video footage from the Pexels Video API for visual cutaways. Checks local cache (`broll_cache/`) before querying online, falls back gracefully if `PEXELS_API_KEY` is not configured, filters by portrait orientation and resolution (>=720p), and writes downloads via atomic `.tmp.mp4` replacement. |
| Industrial-grade? | **A** - Atomic file replacement, local disk caching, and graceful offline fallback. |
| Checklist | **Error handling:** Pass (gracefully catches HTTP errors and timeouts without crashing caller). **Timeouts:** Pass (7s API timeout, 15s download timeout). **Retries:** N/A. **Idempotency:** Pass (cached by query slug). **Input validation:** Pass (sanitizes query into filesystem-safe filename). **Resource cleanup:** Pass (atomic rename). **Concurrency safety:** Pass. **Atomicity:** Pass (`temp_target.replace(cached_file)`). **External-dependency:** Pass (Pexels API). **Security:** Pass (sanitizes query parameters). **Performance:** Pass (chunked 64KB disk writes). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Pexels API client with local disk caching and atomic rename (Recommended). |
| Priority / Effort | P3 / S |
