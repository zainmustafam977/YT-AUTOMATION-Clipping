# Assessment Cards: shorts_engine/db/models.py

---

## Unit 1: generate_uuid Helper & Enums (SourceVideoStatus, ClipStatus, UserRole)
| Field | Content |
|---|---|
| Location | `shorts_engine/db/models.py:20-56` |
| What it does | UUID4 generator string callable and typed string enumerations defining lifecycle states for videos, clips, and dashboard RBAC user roles. |
| Industrial-grade? | **A** - Canonical state definitions with string coercion (`str, Enum`) allowing seamless JSON and DB serialization. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (SQLAlchemy). **Security:** Pass (UUID4 prevents enumeration). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: `str, Enum` with `generate_uuid` (Recommended). Option 2: Integer autoincrement (Exposes sequential IDs, leaks business metrics). |
| Priority / Effort | P3 / S |

---

## Unit 2: Channel Model
| Field | Content |
|---|---|
| Location | `shorts_engine/db/models.py:58-73` |
| What it does | SQLAlchemy model representing monitored YouTube channels, tracking target upload channels, review toggles, encrypted OAuth tokens, and cascade relationships. |
| Industrial-grade? | **A** - Unique constraint and index on `youtube_channel_id`, cascading orphan deletion to source videos. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (`cascade="all, delete-orphan"`). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (tokens stored in `encrypted_oauth_token`). **Performance:** Pass (indexed channel ID). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Uses `datetime.utcnow` which is deprecated in Python 3.12+ (recommends `datetime.now(timezone.utc)`). |
| Improvement | Migrate `default=datetime.utcnow` to `server_default=func.now()` or timezone-aware factory. |
| Better approach? | Option 1: SQL `server_default=func.now()` (Recommended - database server authoritative). Option 2: `datetime.now(timezone.utc)` (Python client authoritative). |
| Priority / Effort | P2 / S |

---

## Unit 3: SourceVideo Model & runtime_overrides Property
| Field | Content |
|---|---|
| Location | `shorts_engine/db/models.py:75-122` |
| What it does | Tracks ingested source video metadata, download paths, processing status, and per-video runtime parameter overrides via JSON serialization properties. |
| Industrial-grade? | **A** - Enforces `UniqueConstraint("channel_id", "youtube_video_id")` preventing duplicate ingestion. Property setter/getter handles serialization defensively. |
| Checklist | **Error handling:** Pass (catches JSON decode errors, falls back to empty dict). **Timeouts:** N/A. **Retries:** Pass (tracks `retry_count`). **Idempotency:** Pass (unique constraint). **Input validation:** Pass. **Resource cleanup:** Pass (cascades transcripts and candidates). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass (indexed status and video ID). **Observability:** Pass (stores `error_message`). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | `runtime_overrides_json` stores raw text rather than SQLAlchemy `JSON` type. |
| Improvement | Migrate to SQLAlchemy `JSON` column type for native JSON querying on Postgres. |
| Better approach? | Option 1: SQLAlchemy `JSON` type with property wrapper (Recommended). Option 2: Plain Text with manual `json.loads` (Current - functional across all DBs). |
| Priority / Effort | P2 / S |

---

## Unit 4: Transcript Model
| Field | Content |
|---|---|
| Location | `shorts_engine/db/models.py:124-134` |
| What it does | Stores word-level Whisper timestamp JSON arrays and detected language for an ingested source video with a 1:1 foreign key relationship. |
| Industrial-grade? | **A** - Clean 1:1 relationship with unique foreign key constraint and cascade deletion. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Word-level JSON for a 2-hour video can exceed 10 MB in raw text. |
| Improvement | Consider zlib/gzip compression for transcript payloads exceeding 1 MB in storage. |
| Better approach? | Option 1: Text/JSONB with client gzip compression (Recommended for high video volumes). Option 2: Raw Text (Current - fast and simple for shorts workloads). |
| Priority / Effort | P3 / S |

---

## Unit 5: ClipCandidate Model & variations Property
| Field | Content |
|---|---|
| Location | `shorts_engine/db/models.py:136-170` |
| What it does | Stores candidate segment boundaries (`start_ms`, `end_ms`), virality scores, viral reasoning hooks, payoff explanations, and A/B title/hook variations. |
| Industrial-grade? | **A** - Indexed selection flag (`selected`) and foreign key; defensive JSON getter/setter for A/B variations. |
| Checklist | **Error handling:** Pass (defensive JSON parsing). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass (cascades rendered clip). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass (indexed foreign key and selection status). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 6: Clip Model, variations & analytics Properties
| Field | Content |
|---|---|
| Location | `shorts_engine/db/models.py:172-237` |
| What it does | Master entity for rendered 9:16 vertical shorts, storing filesystem paths, caption styling, SFX cue tags, quality check verdicts, publishing schedules, and YouTube analytics. |
| Industrial-grade? | **A** - Rich domain model with indexed publication timestamps and status, cascading logs, and fallback variation inheritance from candidate. |
| Checklist | **Error handling:** Pass (defensive JSON property getters). **Timeouts:** N/A. **Retries:** Pass (tracks `retry_count`). **Idempotency:** Pass (1:1 candidate relationship). **Input validation:** Pass. **Resource cleanup:** Pass (cascades publish logs). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass (indexed candidate FK, status, and scheduled publish time). **Observability:** Pass (stores QA checks, analytics, retry counts). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Filesystem path in `rendered_path` is an absolute or relative path string; if storage root moves, paths require migration. |
| Improvement | Ensure `PathManager` is always used to resolve relative paths against current storage root. |
| Better approach? | Option 1: Store relative paths in DB and resolve via `PathManager` (Recommended). Option 2: Store absolute OS paths (Breaks across machines/containers). |
| Priority / Effort | P2 / S |

---

## Unit 7: PublishLog Model
| Field | Content |
|---|---|
| Location | `shorts_engine/db/models.py:239-257` |
| What it does | Historical record of YouTube video upload executions, capturing video IDs, published timestamps, uploaded metadata, and delivery status. |
| Industrial-grade? | **A** - `UniqueConstraint("clip_id", "youtube_video_id")` guarantees zero accidental double-publishing of the same clip to YouTube. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass (unique constraint prevents double-publish). **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass (indexed video ID). **Observability:** Pass (captures status and error message). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 8: PlatformPublishLog Model
| Field | Content |
|---|---|
| Location | `shorts_engine/db/models.py:259-277` |
| What it does | Audit log for multi-platform syndication attempts across YouTube, TikTok, and Instagram Reels, tracking post IDs, permalinks, and error diagnostics. |
| Industrial-grade? | **A** - Indexed platform and status columns enable granular per-platform syndication reporting. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass (captures platform status, permalink, error message). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Lacks composite unique constraint on `(clip_id, platform, platform_post_id)`. |
| Improvement | Add unique constraint to prevent duplicate platform syndication entries. |
| Better approach? | Option 1: Add composite unique index `(clip_id, platform)` for non-repeatable syndications (Recommended). Option 2: Allow multiple records for audit retry history (Current). |
| Priority / Effort | P2 / S |

---

## Unit 9: SFXLibrary & User Models
| Field | Content |
|---|---|
| Location | `shorts_engine/db/models.py:279-298` |
| What it does | Database entities for cataloging sound effect audio assets and authenticating dashboard operators with argon2/bcrypt password hashes and RBAC roles. |
| Industrial-grade? | **A** - Unique indexed tags on SFX and usernames on User; typed enum role enforcement. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (hashed passwords, RBAC). **Performance:** Pass (indexed username). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |
