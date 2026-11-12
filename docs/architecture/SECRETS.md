# Secrets Management & Encryption Architecture

This document describes the security policies, encryption architecture, operational procedures, and rotation protocols for all persisted secrets in the YouTube Shorts Automation Platform.

---

## 1. Master Key Policy & Generation

All persisted secrets (OAuth refresh tokens, API keys, client credentials, and cookies) are encrypted at rest using **Fernet symmetric authenticated encryption** (AES-128 in CBC mode with HMAC-SHA256 authentication).

### Generating a New Master Key

To generate a cryptographically secure 32-byte URL-safe base64-encoded Fernet key, execute:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Configuration & Storage Policy

- The master key must be provided exclusively via the **`SECRETS_MASTER_KEY`** environment variable (e.g. in `.env` or your secret manager).
- **Fail-Fast Enforcement**: If `SECRETS_MASTER_KEY` is unset or empty at startup or when accessing secrets, the platform immediately aborts with an actionable `RuntimeError`:
  ```
  RuntimeError: SECRETS_MASTER_KEY is not set. Refusing to operate with plaintext secrets.
  Generate one with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  and set SECRETS_MASTER_KEY in your environment or .env.
  ```
- **Zero Hardcoded Fallbacks**: No default master key exists in `settings.py` or `secretbox.py`. Ephemeral keys are strictly restricted to isolated test fixtures in `tests/conftest.py`.

---

## 2. Encryption Architecture (`shorts_engine.security.secretbox`)

All encryption and decryption operations route through the centralized `secretbox` module:

```python
from shorts_engine.security.secretbox import encrypt, decrypt, rotate

# Encrypt plaintext payload
ciphertext = encrypt("my-secret-token")

# Decrypt ciphertext
plaintext = decrypt(ciphertext)

# Rotate ciphertext to a new key
new_ciphertext = rotate(ciphertext, old_key="old-fernet-key", new_key="new-fernet-key")
```

### Persisted Secret Locations

| Secret Category | Storage Target | Format | Model / File |
| :--- | :--- | :--- | :--- |
| **OAuth Client Credentials** | `storage/client_secret.enc` | Fernet Ciphertext | Loaded on-the-fly by `ChannelOAuthManager` |
| **Per-Channel OAuth Tokens** | `channels.encrypted_oauth_token` | Fernet Ciphertext | `Channel` ORM model (stores JSON blob with `refresh_token`, `access_token`) |
| **AI Provider API Keys** | `api_keys.api_key_encrypted` | Fernet Ciphertext | `ApiKey` ORM model (Groq, Gemini, OpenRouter, OpenAI) |
| **Anti-Bot Cookies** | `cookies.txt.enc`, `storage/harvester_cookies.txt.enc` | Fernet Ciphertext | Encrypted Netscape cookie vaults |

### Data Model Rules

1. **`channels.encrypted_oauth_token` Scope**:
   - Holds **per-channel** token material only (`refresh_token`, `access_token`, `token_uri`, `scopes`).
   - Application-level Google OAuth client secrets (`client_id`, `client_secret`) must **never** be stored in the `channels` table; they live exclusively in `storage/client_secret.enc`.
2. **Runtime Settings Sanitization**:
   - `storage/config/runtime_settings.json` is strictly restricted to non-sensitive runtime parameters (durations, thresholds, model names).
   - `save_runtime_settings()` automatically filters out any field matching `key`, `secret`, `token`, `password`, `credential`, or `auth` before writing to disk.
3. **Audit Log Redaction Filter**:
   - The platform registers `SensitiveDataFilter` on the root logger and all output handlers.
   - Any log call format or argument containing sensitive fields recursively redacts matching values with `[REDACTED]`.

---

## 3. Key Rotation Procedure

When rotating the master key, all stored secrets must be re-encrypted under the new key.

### CLI Rotation (Recommended)

1. Generate your new key:
   ```bash
   NEW_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
   ```
2. Run the rotation command:
   ```bash
   python cli.py rotate-secrets --old-key "$SECRETS_MASTER_KEY" --new-key "$NEW_KEY"
   ```
   *(Or run `python cli.py rotate-secrets` and enter both keys securely when prompted).*
3. The rotation procedure:
   - Automatically takes a hot SQLite backup with gzip compression and SHA-256 verification.
   - Decrypts each row in `channels` and `api_keys` using the old key.
   - Re-encrypts each payload using the new key.
   - Re-encrypts `storage/client_secret.enc` and cookie vaults.
   - Atomically commits all changes in a single transaction. If any decryption or re-encryption fails, the entire transaction rolls back cleanly.
4. Update `SECRETS_MASTER_KEY` in `.env`:
   ```bash
   SECRETS_MASTER_KEY=<new-fernet-key>
   ```
5. Restart running services (`dashboard`, `worker`).

---

## 4. Emergency Key Compromise Protocol

If `SECRETS_MASTER_KEY` or any individual credentials are leaked or compromised:

1. **Rotate Upstream Credentials First**:
   - **Google Cloud Console**: Revoke the compromised OAuth Client ID/Secret and generate a new OAuth 2.0 Web Application client.
   - **AI Providers**: Revoke all API keys on Groq, Google AI Studio, OpenRouter, and OpenAI consoles.
2. **Generate a Fresh Master Key**:
   - Generate a new `SECRETS_MASTER_KEY`.
3. **Update Local Vaults**:
   - Save the new client credentials as JSON and encrypt with `secretbox.encrypt()` to `storage/client_secret.enc`.
   - Update `api_keys` in the database via the dashboard or CLI.
4. **Purge Backups & Logs**:
   - Delete any temporary backup files outside the repository.
   - Ensure historical log files in `storage/logs/` are cleared.
