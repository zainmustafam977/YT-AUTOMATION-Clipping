# Assessment Cards: shorts_engine/services/detection/filter.py

---

## Unit 1: is_existing_short
| Field | Content |
|---|---|
| Location | `shorts_engine/services/detection/filter.py:13-41` |
| What it does | Evaluates video title, metadata tags, and duration against short-form video heuristics (#Shorts hashtag or duration $\le 180$s) to prevent clipping existing YouTube Shorts. |
| Industrial-grade? | **B** - Fast and descriptive, but duration-only check risks false positives on short 16:9 landscape videos. |
| Checklist | **Error handling:** Pass (handles None inputs gracefully). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass (pure function). **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass (sub-millisecond evaluation). **Observability:** Pass (returns diagnostic reason string). **Config:** Pass (`settings.MAX_EXTENDED_DURATION_SEC`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | A 2-minute landscape video (16:9) without any `#shorts` tag will be flagged as an existing Short and skipped purely because its duration is $\le 180$ seconds. |
| Improvement | Combine duration check with aspect ratio (width < height) or require explicit user configuration to treat short landscape videos as shorts. |
| Better approach? | Option 1: Require `#shorts` tag OR (duration $\le 180$s AND vertical aspect ratio) (Recommended). Option 2: Duration $\le 180$s alone (Current - potential false positives on short landscape clips). |
| Priority / Effort | P2 / S |
