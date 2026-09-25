# Assessment Cards: shorts_engine/services/detection/websub.py

---

## Unit 1: Subscription State Persistence (_load_subscriptions, _save_subscriptions)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/detection/websub.py:37-48` |
| What it does | Loads and writes WebSub subscription lease state to a local JSON sidecar file (`storage/websub_subscriptions.json`). |
| Industrial-grade? | **C** - Direct `write_text` risks file truncation if power or process terminates mid-write; lacks file locking. |
| Checklist | **Error handling:** Pass on load (returns empty dict), Fail on save. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Fail (concurrent worker writes will clobber). **Atomicity:** Fail (non-atomic overwrite). **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | If the worker or server crashes mid-write, `websub_subscriptions.json` can be left corrupted or 0-bytes, losing all lease renewal schedules. |
| Improvement | Write to a temporary file in the same directory and execute atomic `os.replace`, or migrate state directly into the relational database. |
| Better approach? | Option 1: Relational DB storage in `channels` table (Recommended - unified transactional state). Option 2: Atomic temp file replace (Interim filesystem fix). Option 3: Direct `write_text` (Current - crash hazard). |
| Priority / Effort | P1 / S |

---

## Unit 2: WebSubSubscriber.subscribe & verify_challenge
| Field | Content |
|---|---|
| Location | `shorts_engine/services/detection/websub.py:50-130` |
| What it does | Submits URL-encoded subscription requests to YouTube's Google PubSubHubbub hub (`https://pubsubhubbub.appspot.com/subscribe`) and handles incoming HTTP GET challenge verifications. |
| Industrial-grade? | **A** - Implements the full W3C WebSub recommendation specification, validates YouTube topic URIs, and checks for HTTP 202 Accepted. |
| Checklist | **Error handling:** Pass (catches network errors, logs unexpected status codes). **Timeouts:** Pass (10s urllib timeout). **Retries:** N/A. **Idempotency:** Pass (re-subscribing the same topic is idempotent). **Input validation:** Pass (verifies callback URL and YouTube topic format). **Resource cleanup:** Pass (`with urllib.request.urlopen(...)`). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (Google PubSubHubbub hub). **Security:** Pass (signs requests with `WEBSUB_SECRET`). **Performance:** Pass. **Observability:** Pass. **Config:** Pass (`settings.WEBSUB_HUB_URL`, `WEBSUB_CALLBACK_URL`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 3: WebSubNotificationParser (verify_signature & parse_notification)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/detection/websub.py:132-188` |
| What it does | Authenticates inbound YouTube push notifications via HMAC-SHA1 signature verification and extracts `(channel_id, video_id)` from Atom XML feed payloads. |
| Industrial-grade? | **A** - Employs `hmac.compare_digest()` for timing-attack-resistant cryptographic verification. |
| Checklist | **Error handling:** Pass (catches XML parse errors defensively). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (validates `sha1=` prefix and header presence). **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (timing-safe HMAC comparison prevents signature oracle attacks). **Performance:** Pass (fast C-accelerated HMAC). **Observability:** Pass (warns on signature spoofing). **Config:** Pass (`settings.WEBSUB_SECRET`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | If `WEBSUB_SECRET` is unset, signature verification is skipped (intended for local dev). In production, `settings.py` enforces secrets in production environments. |
| Improvement | Keep as is. |
| Better approach? | Option 1: `hmac.compare_digest` with dev fallback (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 4: WebSubLeaseRenewer.check_and_renew_all
| Field | Content |
|---|---|
| Location | `shorts_engine/services/detection/websub.py:190-227` |
| What it does | Scans active channel subscriptions and automatically initiates hub re-subscription for leases that have exceeded the 80% lifetime threshold. |
| Industrial-grade? | **A** - Proactive 80% lease renewal prevents subscription drop-outs and ensures continuous real-time push ingestion. |
| Checklist | **Error handling:** Pass (per-channel try/except prevents single-channel failure from blocking others). **Timeouts:** Pass. **Retries:** Pass (retried on subsequent check cycle). **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass (logs lease percentages and renewals). **Config:** Pass (`settings.WEBSUB_RENEW_THRESHOLD`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: 80% proactive threshold renewal (Recommended - industry standard for WebSub/OAuth). |
| Priority / Effort | P3 / S |
