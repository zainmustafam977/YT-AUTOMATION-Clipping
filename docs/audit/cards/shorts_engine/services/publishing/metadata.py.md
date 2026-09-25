# Assessment Cards: shorts_engine/services/publishing/metadata.py

---

## Unit 1: ShortsMetadata
| Field | Content |
|---|---|
| Location | `shorts_engine/services/publishing/metadata.py:9-50` |
| What it does | Enforces YouTube Shorts metadata constraints: automatically appends `#Shorts` tag to title, truncates titles exceeding 100 characters gracefully, injects hashtag blocks into description (capped at 5,000 characters), and formats YouTube Data API v3 snippet/status payloads with `selfDeclaredMadeForKids=False`. |
| Industrial-grade? | **A** - Clean metadata normalizer preventing API validation rejections. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (enforces strict character caps). **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (YouTube Data API v3 schema). **Security:** Pass. **Performance:** Pass. **Observability:** Pass (`to_dict`). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | YouTube `categoryId` is hardcoded to `"22"` (People & Blogs), which may not suit gaming, technology, or comedy channels. |
| Improvement | Support optional `category_id: str = "22"` parameter with fallback to `settings.DEFAULT_YOUTUBE_CATEGORY_ID`. |
| Better approach? | Option 1: Configurable categoryId (Recommended). Option 2: Hardcoded "22" (Current). |
| Priority / Effort | P3 / S |
