# Module Reference: 15 Operator Dashboard, Security Model & UI Routes

> **Path:** `shorts_engine/dashboard/`  
> **Source Files:**
> - [`shorts_engine/dashboard/app.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/app.py)
> - [`shorts_engine/dashboard/auth.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/auth.py)
> - [`shorts_engine/dashboard/routes/status.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/status.py)
> - [`shorts_engine/dashboard/routes/queue.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py)
> - [`shorts_engine/dashboard/routes/channels.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/channels.py)
> - [`shorts_engine/dashboard/routes/settings.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/settings.py)
> - [`shorts_engine/dashboard/routes/health.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/health.py)
> - [`shorts_engine/dashboard/routes/metrics.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/metrics.py)
> - [`shorts_engine/dashboard/routes/logs.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/logs.py)
> - [`shorts_engine/dashboard/routes/calendar.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/calendar.py)
> - [`shorts_engine/dashboard/routes/websub.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/websub.py)

---

## 1. Module Purpose & Responsibilities

This subsystem implements the human operator control center, web interface, real-time telemetry streams, and security boundaries:
1. **FastAPI Application Factory & Lifecycle:** `create_dashboard_app()` configures Jinja2 templating, mounts route groups, starts background daemons (`DripFeedDaemon`, `ABWinnerPromotionDaemon`) in lifespan context, and performs startup reconciliation of stale worker claims (`app.py:30-68`).
2. **Session-Based Multi-User Authentication:** Replaced legacy HTTP Basic Auth with cryptographic session tokens (`<user_id>.<timestamp>.<hmac_sha256>`) stored in `HttpOnly`, `SameSite=Lax` cookies or transmitted via `Authorization: Bearer` (`auth.py:66-103, 188-224`).
3. **Password Security:** PBKDF2-HMAC-SHA256 with 600,000 iterations and 16-byte random salts (`pbkdf2_sha256$600000$<salt>$<hash>`) verified using constant-time comparison (`auth.py:30-59`).
4. **Cryptographic CSRF Protection:** Derives session-bound HMAC-SHA256 CSRF tokens verified on all state-changing HTTP methods (`POST`, `PUT`, `DELETE`, `PATCH`) via header `X-CSRF-Token` or form input `csrf_token` (`auth.py:125-184`).
5. **Role-Based Access Control (RBAC):** Three privilege tiers:
   - `ADMIN`: Full configuration access, API key updates, user management (`require_admin`).
   - `OPERATOR`: Ingestion, clip approvals/rejections, recovery auto-fixes, channel toggles (`require_operator`).
   - `VIEWER`: Read-only access to metrics, logs, and queue review (`require_viewer`) (`auth.py:226-251`).
6. **Real-Time Telemetry & SSE Streaming:** Server-Sent Events (`/events`) stream live pipeline stages, speed, ETA, and AI quota usage directly to connected browsers (`routes/status.py`).
7. **Operational UI Routes:**
   - `/`: Status overview, active jobs, cancellation/resumption controls, manual ingestion form.
   - `/queue`: Review queue (card view vs flat view), clip playback, recovery modal auto-fixes, folder launcher.
   - `/channels`: Channel registration, watch/review toggles, video explorer drawer.
   - `/settings`: Multi-tier runtime configuration with bounded parameter enforcement.
   - `/livez`, `/readyz`, `/healthz`, `/metrics`: Prometheus metrics and Kubernetes probes.

---

## 2. Public Interfaces & Signatures

### 2.1 `shorts_engine.dashboard.auth`
- `hash_password(password: str) -> str`: PBKDF2 600,000 iterations (`auth.py:30-41`).
- `verify_password(plain_password: str, hashed_password: str) -> bool`: Constant-time check (`auth.py:43-59`).
- `create_session_token(user_id: str, secret_key: str = None, timestamp: Optional[int] = None) -> str` (`auth.py:66-77`).
- `verify_session_token(token: str, secret_key: str = None, max_age_secs: int = 86400 * 7) -> Optional[str]` (`auth.py:79-103`).
- `generate_csrf_token(session_token: str) -> str` (`auth.py:125-133`).
- `verify_csrf(request: Request) -> bool`: Dependency for mutations (`auth.py:151-184`).
- `get_current_user(request: Request, db: Session = Depends(get_db)) -> User` (`auth.py:188-224`).
- `require_admin`, `require_operator`, `require_viewer`: Role dependencies (`auth.py:248-251`).

### 2.2 `shorts_engine.dashboard.app`
- `lifespan(app: FastAPI)`: Starts and stops drip-feed and A/B testing daemons (`app.py:30-41`).
- `create_dashboard_app() -> FastAPI`: Application factory (`app.py:43-119`).

---

## 3. Dashboard Route & Security Architecture

```mermaid
flowchart TD
    CLIENT[Browser User / Operator / API Client] --> REQ[Incoming HTTP Request]

    subgraph Middleware_and_Security_Gates
        REQ --> CACHE_CTRL[Middleware: Inject Cache-Control: no-store on HTML]
        CACHE_CTRL --> AUTH_GATE{get_current_user: Cookie or Bearer Token?}
        
        AUTH_GATE -->|Invalid / Missing| HANDLER{Is HTML Browser Request?}
        HANDLER -->|Yes| REDIR[303 Redirect to /login?next=...]
        HANDLER -->|No| JSON401[401 JSON Unauthorized]
        
        AUTH_GATE -->|Valid| RBAC{Role Authorization Check}
        RBAC -->|Insufficient Privilege| FORBID403[403 Forbidden]
        RBAC -->|Authorized| CSRF{Method in POST, PUT, DELETE, PATCH?}
        
        CSRF -->|Yes| CSRF_CHECK{X-CSRF-Token == HMAC-SHA256(session_token)?}
        CSRF_CHECK -->|No| FORBID_CSRF[403 Invalid CSRF Token]
        CSRF_CHECK -->|Yes| ROUTE_EXEC[Execute Endpoint Handler]
        CSRF -->|No: GET / HEAD / OPTIONS| ROUTE_EXEC
    end

    subgraph Route_Groups
        ROUTE_EXEC --> STATUS[routes/status.py: Live Telemetry, Manual Ingest, SSE]
        ROUTE_EXEC --> QUEUE[routes/queue.py: Review, Approve, Reject, Recovery Modal]
        ROUTE_EXEC --> CHANNELS[routes/channels.py: Channel Toggles & Drawer]
        ROUTE_EXEC --> SETTINGS[routes/settings.py: Runtime Config & Bounds]
        ROUTE_EXEC --> PROBES[routes/health.py: /livez, /readyz & Prometheus /metrics]
    end
```

---

## 4. Invariants, Session Lifecycles & UI Guardrails

1. **Complete Removal of Basic Auth:** All access requires an active database entity (`User`) with an assigned role (`admin`, `operator`, `viewer`). Shared hardcoded username/password credentials have been completely eradicated (`auth.py:1-5, 197-200`).
2. **Back-Forward Cache (bfcache) Leak Prevention:** The `add_no_cache_headers_for_html` middleware automatically attaches `Cache-Control: no-store, no-cache, must-revalidate, max-age=0` to all HTML responses, preventing browsers from displaying cached authenticated dashboards after logout (`app.py:95-105`).
3. **Session Invalidation on Expiry:** Signed session tokens enforce a hard expiration limit of 7 days (`max_age_secs = 604800`). Expired tokens fail verification and trigger immediate re-authentication (`auth.py:98-100`).
4. **Timing-Safe Digest Validations:** Password hash comparisons (`secrets.compare_digest`), session token signature checks, and CSRF token verifications all run under constant-time equality checks to eliminate timing side-channel attacks (`auth.py:56, 96, 140`).
