# Assessment Cards: Shorts_Engine_Control_Center/04_Admin_and_Distribution/create_user.bat

---

## Unit 1: RBAC User Creation Operational Batch Script
| Field | Content |
|---|---|
| Location | `Shorts_Engine_Control_Center/04_Admin_and_Distribution/create_user.bat:1-61` |
| What it does | Interactive batch utility prompting operators for credentials (username, password) and RBAC role selection (admin, operator, viewer), then executing `run.py create-user` to insert or reset users in the database. |
| Industrial-grade? | **B+** - Interactive validation with role defaulting, but terminal input echoes password in plaintext, and script contains machine-specific absolute paths in directory resolver. |
| Checklist | **Error handling:** Pass (guards against empty username and password). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass (`create-user` handles existing users). **Input validation:** Pass (maps selection 1/2/3 to valid enum strings). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **Secret hygiene:** Needs Improvement (password is entered via `set /p` which visibly displays characters in terminal and command history). **Logging:** Standard output. **Telemetry:** N/A. **Testability:** Pass. **Cost:** Free. **Modularity:** Pass. **Performance:** Pass. |
| Improvement proposals / Alternatives | 1. Remove machine-specific hardcoded absolute paths on lines 12-16. 2. Use a PowerShell secure prompt snippet (`powershell -Command "$p = read-host 'Enter Password' -AsSecureString; ..."`) to prevent displaying plaintext passwords on screen. |
