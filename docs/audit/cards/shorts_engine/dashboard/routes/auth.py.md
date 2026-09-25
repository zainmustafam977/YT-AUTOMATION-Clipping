# Assessment Cards: shorts_engine/dashboard/routes/auth.py

---

## Unit 1: Login & Session Authentication Endpoints
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/auth.py:45-95` |
| What it does | Serves `/login` HTML page and handles POST authentication: verifies credentials against PBKDF2 hash, issues signed session token in `HttpOnly` cookie, and returns session-bound CSRF token. |
| Industrial-grade? | **A** - Secure authentication flow with constant-time verification and signed session cookies. |
| Checklist | **Error handling:** Pass (uniform "Invalid username or password" message prevents username enumeration). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (guards empty fields). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (HttpOnly, SameSite=Lax, PBKDF2). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Standard signed session cookie auth (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: Logout & Current User Endpoints
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/auth.py:97-132` |
| What it does | `logout` purges session cookies and redirects browser clients to `/login` (or returns JSON for API clients). `get_me` provides active user identity, role, and a fresh CSRF token. |
| Industrial-grade? | **A** - Clean session revocation and identity inspection. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (clears HttpOnly cookie). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Standard session clearance (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 3: Admin User Management (API & UI)
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/routes/auth.py:136-308` |
| What it does | Complete user CRUD endpoints (REST API and HTML form endpoints) restricted to administrators: lists users, creates users with PBKDF2 hashing, and deletes users with self-deletion protection. |
| Industrial-grade? | **A-** - Comprehensive RBAC user management, but contains minor password policy inconsistency between API and UI. |
| Checklist | **Error handling:** Pass (prevents duplicate usernames, prevents self-deletion). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (CSRF validated on mutations, PBKDF2 hashing, admin role enforcement). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Minimum password length is checked as 12 characters in `CreateUserRequest` (line 41), but checked as 8 characters in `admin_create_user_form` (line 264). |
| Improvement | Align HTML form password validation to require at least 12 characters (`len(password) < 12`). |
| Better approach? | Option 1: Consistent 12-char minimum policy (Recommended). Option 2: Divergent lengths (Current). |
| Priority / Effort | P3 / S |
