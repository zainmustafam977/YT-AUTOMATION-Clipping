# Assessment Cards: shorts_engine/services/detection/rss_poller.py

---

## Unit 1: FeedEntry Class
| Field | Content |
|---|---|
| Location | `shorts_engine/services/detection/rss_poller.py:28-34` |
| What it does | Backwards-compatible `tuple` subclass returning `(video_id, published_at)` while exposing an attached `.title` attribute. |
| Industrial-grade? | **A** - Clean tuple specialization preserving legacy tuple unpacking `vid, pub = entry` while supporting title filtering. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass (lightweight tuple). **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Tuple subclass (Recommended for zero-breakage backward compatibility). Option 2: Dataclass (Requires updating all caller unpacking sites). |
| Priority / Effort | P3 / S |

---

## Unit 2: RSSPoller.fetch_feed
| Field | Content |
|---|---|
| Location | `shorts_engine/services/detection/rss_poller.py:36-95` |
| What it does | Fetches and parses the public YouTube channel Atom XML feed (`https://www.youtube.com/feeds/videos.xml?channel_id=...`) using standard library `urllib` and `ElementTree`, returning entries sorted newest-first. |
| Industrial-grade? | **A** - Zero external dependencies (avoids heavyweight `feedparser`). Validates channel ID format (`UC...` 24 chars) and sets timeout. |
| Checklist | **Error handling:** Pass (catches HTTP errors, 404s, XML parse errors). **Timeouts:** Pass (10s default timeout). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (guards against malformed channel IDs). **Resource cleanup:** Pass (`with urllib.request.urlopen(...) as resp`). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (stdlib only). **Security:** Pass (guards against SSRF with fixed YouTube feed URL prefix). **Performance:** Pass. **Observability:** Pass (warns on 404 and parse failure). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Stdlib `urllib` + `xml.etree.ElementTree` (Recommended - lean, fast, zero CVEs). Option 2: `feedparser` (Heavy third-party dependency). |
| Priority / Effort | P3 / S |

---

## Unit 3: RSSPoller.poll_channel
| Field | Content |
|---|---|
| Location | `shorts_engine/services/detection/rss_poller.py:96-143` |
| What it does | Evaluates channel Atom feed against `channel.last_seen_video_id`, skips already-vertical shorts via `is_existing_short(title)`, and advances `last_seen_video_id` in the database. |
| Industrial-grade? | **B** - Excellent short pre-filtering, but if an upload batch contains exclusively shorts, `last_seen_video_id` is not advanced. |
| Checklist | **Error handling:** Pass. **Timeouts:** Pass. **Retries:** N/A. **Idempotency:** Pass (`last_seen_video_id` pointer prevents re-processing). **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass (`db.commit()`). **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass (logs skipped shorts and new detections). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | If a creator uploads only a YouTube Short, `new_video_ids` is empty, leaving `last_seen_video_id` unchanged. On the subsequent poll, the same Short feed entry is re-evaluated and logged again. |
| Improvement | Update `channel.last_seen_video_id = entries[0][0]` even if all new entries were skipped shorts. |
| Better approach? | Option 1: Advance `last_seen_video_id` to newest feed entry regardless of short filtering (Recommended). Option 2: Advance only on accepted videos (Current). |
| Priority / Effort | P2 / S |

---

## Unit 4: RSSPollLoop
| Field | Content |
|---|---|
| Location | `shorts_engine/services/detection/rss_poller.py:145-176` |
| What it does | Long-running daemon loop iterating across all active monitored channels, polling feeds, invoking video callback handlers, and sleeping between intervals. |
| Industrial-grade? | **B** - Robust session scoping (`try / finally: db.close()`) per iteration, but uses uninterruptible `time.sleep()`. |
| Checklist | **Error handling:** Pass (catches per-channel errors to prevent loop crash). **Timeouts:** Pass. **Retries:** Pass (resumes next cycle). **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (closes DB session in finally). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass (`settings.RSS_POLL_INTERVAL_MINUTES`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | `time.sleep(interval)` cannot be cleanly interrupted by OS signals (SIGTERM/SIGINT) without waiting out the full interval (e.g. 15 minutes). |
| Improvement | Use `threading.Event().wait(interval)` or small 1-second sleep increments checking a shutdown flag. |
| Better approach? | Option 1: `threading.Event().wait(timeout=interval)` (Recommended for graceful container shutdown). Option 2: `time.sleep(interval)` (Current - delays shutdown). |
| Priority / Effort | P2 / S |
