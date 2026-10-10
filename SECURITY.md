# 🛡️ Enterprise Security Policy & Posture

## 1. Security Architecture Overview

This project is deployed under an **Enterprise Private Repository Posture**. The system manages sensitive broadcast assets, cloud API tokens, OAuth credentials, and automated channel syndication pipelines.

### Cryptographic Baseline
- **Symmetric Encryption**: AES-256-GCM authenticated encryption for all tokens, channel secrets, and sensitive credentials stored at rest.
- **Key Derivation**: PBKDF2 with HMAC-SHA256 and minimum 100,000 iterations for master key derivations.
- **Session Authentication**: JWT signed with HMAC-SHA256 (`DASHBOARD_SECRET_KEY`) enforcing strict token expiration windows and CSRF tokens for state-modifying actions.
- **Process Isolation**: Execution of external tools (FFmpeg, yt-dlp) is executed under constrained environments with strict argument whitelisting to eliminate shell injection vulnerabilities.

---

## 2. Key Vault & Credential Hygiene

### 2.1. Master Encryption Key (`MASTER_ENCRYPTION_KEY`)
- Must be a high-entropy string of at least 32 bytes.
- Used exclusively by `shorts_engine/services/ai/key_vault.py` and `shorts_engine/services/publishing/oauth_manager.py` to encrypt/decrypt database credential fields.
- Zero plaintext credential storage in database tables.

### 2.2. Secret Rotation Protocol
The system implements automated secret rotation via `tests/test_secret_rotation.py`:
1. Generate a new master encryption key.
2. Run database migration script:
   ```bash
   python -m shorts_engine.utils.rotate_secrets --old-key "$OLD_KEY" --new-key "$NEW_KEY"
   ```
3. Update `.env` with the new key.
4. Restart background worker and dashboard services.

---

## 3. Subprocess & Shell Injection Protection

- Direct invocation of `subprocess.Popen` with `shell=True` is strictly forbidden across the codebase.
- All OS commands are parameterized as argument lists:
  ```python
  # Enterprise Standard (Safe)
  subprocess.run(["ffmpeg", "-y", "-i", input_path, output_path], check=True)
  ```
- File paths undergo strict validation and normalization via `Path.resolve()` to prevent path traversal attacks (`../`).

---

## 4. Reporting a Security Vulnerability

If you discover a security vulnerability in this engine:
1. **Do not create a public issue**.
2. Document the vulnerability details, proof-of-concept steps, and affected components.
3. Transmit the disclosure directly to the designated repository security administrator:
   - Contact: `zainmustafam041@gmail.com`
4. The security administrator will review the disclosure within 48 hours and coordinate remediation.
