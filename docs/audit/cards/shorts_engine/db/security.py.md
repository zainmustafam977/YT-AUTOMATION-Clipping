# Assessment Cards: shorts_engine/db/security.py

---

## Unit 1: _derive_key
| Field | Content |
|---|---|
| Location | `shorts_engine/db/security.py:22-30` |
| What it does | Derives a 256-bit AES symmetric key from a master key string and a 16-byte salt using PBKDF2-HMAC-SHA256 with 600,000 iterations. |
| Industrial-grade? | **A** - Meets and exceeds OWASP 2023 recommendations (600,000 iterations for PBKDF2-HMAC-SHA256). |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass (thread-safe pure function). **Atomicity:** Pass. **External-dependency:** Pass (`cryptography`). **Security:** Pass (high work factor resists brute force). **Performance:** Pass (deliberate work factor; acceptable for OAuth tokens). **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | High CPU cost by design (~150-250ms per key derivation). Not suitable for high-frequency inner loops, but appropriate for token storage. |
| Improvement | Keep iteration count as is; do not reduce. |
| Better approach? | Option 1: PBKDF2-HMAC-SHA256 with 600k iterations (Recommended - standard across enterprise FIPS environments). Option 2: Argon2id (Superior memory hardness, but introduces additional C-extension dependency `argon2-cffi`). |
| Priority / Effort | P3 / S |

---

## Unit 2: encrypt_secret
| Field | Content |
|---|---|
| Location | `shorts_engine/db/security.py:33-52` |
| What it does | Encrypts plaintext strings using AES-256-GCM with a cryptographically secure 16-byte random salt and 12-byte random nonce; packs into base64 payload. |
| Industrial-grade? | **A** - Authenticated encryption (AEAD) ensures confidentiality and integrity. Unique salt and nonce per encryption eliminate pattern leakage. |
| Checklist | **Error handling:** Pass (empty plaintext returns empty string). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass (produces fresh ciphertext each run). **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass (`os.urandom` is thread-safe). **Atomicity:** Pass. **External-dependency:** Pass (`cryptography.hazmat`). **Security:** Pass (AES-GCM avoids padding oracle attacks). **Performance:** Pass. **Observability:** N/A. **Config:** Pass (falls back to `settings.MASTER_ENCRYPTION_KEY`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. Fails gracefully on empty input without raising unexpected exceptions. |
| Improvement | Keep as is. |
| Better approach? | Option 1: AES-256-GCM with salt+nonce envelope (Recommended - NIST SP 800-38D standard). Option 2: Fernet (Simpler, but uses CBC + HMAC rather than AEAD). |
| Priority / Effort | P3 / S |

---

## Unit 3: decrypt_secret
| Field | Content |
|---|---|
| Location | `shorts_engine/db/security.py:54-80` |
| What it does | Decodes base64 payload, unpacks salt, nonce, and ciphertext, verifies GCM 16-byte authentication tag, and returns decrypted plaintext. |
| Industrial-grade? | **A** - Prevents side-channel leakage by masking underlying crypto exceptions behind a generic `ValueError("Decryption failed...")`. |
| Checklist | **Error handling:** Pass (catches tampering/invalid key and logs error without leaking secret). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (validates payload length $\ge$ 44 bytes). **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (AEAD tag prevents bit-flipping). **Performance:** Pass. **Observability:** Pass (logs warning/error without leaking plaintext). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | If `master_key` is incorrect or payload corrupted, raises `ValueError`. Callers must handle `ValueError` appropriately. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Raise standard `ValueError` with generic failure message (Recommended - standard security practice). |
| Priority / Effort | P3 / S |

---

## Unit 4: rotate_secret
| Field | Content |
|---|---|
| Location | `shorts_engine/db/security.py:82-85` |
| What it does | Convenience function decrypting a payload under an old master key and re-encrypting under a new master key with a fresh salt and nonce. |
| Industrial-grade? | **A** - Clean, functional composition of decrypt and encrypt. |
| Checklist | **Error handling:** Pass (inherits validation and exception semantics from decrypt/encrypt). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (re-generates fresh salt and nonce). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 5: rotate_all_channel_tokens
| Field | Content |
|---|---|
| Location | `shorts_engine/db/security.py:88-114` |
| What it does | Re-encrypts all stored channel OAuth tokens in the database within a single database transaction, rolling back on any decryption or commit failure. |
| Industrial-grade? | **B** - Excellent transaction atomicity and rollback semantics, but lacks row locking on PostgreSQL. |
| Checklist | **Error handling:** Pass (explicit try/except with rollback). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Fail (repeated execution with old key fails after first commit). **Input validation:** Pass. **Resource cleanup:** Pass (rolls back on failure). **Concurrency safety:** Warning (in multi-worker Postgres deployments, concurrent token refresh could be clobbered without `with_for_update()`). **Atomicity:** Pass (session-level transaction). **External-dependency:** Pass (SQLAlchemy). **Security:** Pass. **Performance:** Pass (batch query). **Observability:** Pass (logs rotated count). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | In concurrent multi-process environments, another worker could update an OAuth token concurrently during key rotation. |
| Improvement | Add `.with_for_update()` to the query when running against PostgreSQL engines. |
| Better approach? | Option 1: Add row locking `with_for_update()` for PostgreSQL (Recommended for multi-worker scale). Option 2: Keep plain query for SQLite only (Current - fine for single-node SQLite). |
| Priority / Effort | P2 / S |

---

## Unit 6: mask_secret
| Field | Content |
|---|---|
| Location | `shorts_engine/db/security.py:117-123` |
| What it does | Masks sensitive secrets for logging by displaying only the first and last `show_chars` characters separated by ellipsis, or asterisks if the secret is short. |
| Industrial-grade? | **A** - Bulletproof redaction preventing credential leakage in logs and telemetry banners. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (handles None, empty strings, short strings). **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (prevents API key and secret leakage). **Performance:** Pass (O(1)). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |
