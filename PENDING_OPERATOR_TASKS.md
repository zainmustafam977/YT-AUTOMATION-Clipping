# Pending Operator Tasks & Security Checklist

This document tracks pending manual verification and cleanup tasks to be performed by the operator.

---

## 📌 Pending Tasks

### 1. Delete External Plaintext Secrets Backup (Amendment A5)
- **Status**: ⏳ Pending Operator Review / Deletion
- **Background**:
  During Step 1 of the secrets migration, an external temporary backup of legacy plaintext secrets was created outside the repository to ensure safety during data-model re-encryption.
- **Verification Status**:
  All secrets in the database and on disk have been re-encrypted with Fernet (`shorts_engine.security.secretbox`), OAuth flow tests passed 100% green against `storage/client_secret.enc`, and database persistence tests are verified.
- **File to Delete**:
  ```text
  C:\Users\ZACODEC\.secrets_backup\plaintext_secrets_backup.json
  ```
- **Cleanup Command (PowerShell)**:
  ```powershell
  Remove-Item -Path "C:\Users\ZACODEC\.secrets_backup" -Recurse -Force
  ```

---

### 2. Confirm Provider-Side Key Revocation (Amendment A1)
- **Status**: ⏳ In Progress by Operator
- **Background**:
  Legacy credentials existed in git history prior to the encryption migration. Once the new rotated keys are configured:
  1. Revoke the old Google Cloud OAuth Client ID / Secret in Google Cloud Console.
  2. Revoke and rotate old API keys in Groq, Google AI Studio, OpenRouter, and OpenAI dashboards.
- **Action**:
  Confirm all legacy keys are revoked and only the newly rotated credentials are in use.
