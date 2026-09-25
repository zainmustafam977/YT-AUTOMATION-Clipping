# 06 API & Interface Specifications

**Document Version:** 1.0.0  
**Status:** Verified Complete  
**Last Updated:** 2026-09-24  
**Primary Sources:** [`shorts_engine/dashboard/routes/`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/), [`shorts_engine/dashboard/auth.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/auth.py), [`cli.py`](file:///d:/Projects/YT%20AUTOMATION/cli.py), [`run.py`](file:///d:/Projects/YT%20AUTOMATION/run.py)

---

## 1. REST & SSE Endpoint Catalog

The FastAPI dashboard application binds to `http://<DASHBOARD_HOST>:<DASHBOARD_PORT>/` ([`cli.py:356-367`](file:///d:/Projects/YT%20AUTOMATION/cli.py#L356-L367)).  
All state-modifying requests (`POST`, `PUT`, `DELETE`, `PATCH`) enforce CSRF token validation via `X-CSRF-Token` header or `csrf_token` form field ([`shorts_engine/dashboard/auth.py:246-277`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/auth.py#L246-L277)).

### 1.1 Authentication & User Management Routes (`routes/auth.py`)

| Method | Path | Request Body / Params | Response Codes | RBAC Tier | CSRF Protected | Description & Source Citation |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `GET` | `/login` | None | `200 OK` | Public | No | Renders login page ([`auth.py:45`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/auth.py#L45)) |
| `POST` | `/login` | Form: `username`, `password` | `303 See Other`, `401 Unauthorized` | Public | No | Authenticates user and issues signed HMAC cookie ([`auth.py:55`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/auth.py#L55)) |
| `GET` | `/logout` | None | `303 See Other` | Public | No | Clears session cookie and redirects ([`auth.py:98`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/auth.py#L98)) |
| `POST` | `/logout` | None | `303 See Other` | Public | Yes | Clears session cookie and redirects ([`auth.py:97`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/auth.py#L97)) |
| `GET` | `/api/me` | None | `200 OK`, `401 Unauthorized` | Viewer | No | Returns active user profile JSON ([`auth.py:117`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/auth.py#L117)) |
| `GET` | `/api/users` | None | `200 OK`, `403 Forbidden` | Admin | No | Lists all registered system users ([`auth.py:136`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/auth.py#L136)) |
| `POST` | `/api/users` | JSON: `username`, `password`, `role` | `201 Created`, `400 Bad Request` | Admin | Yes | Programmatic user creation ([`auth.py:155`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/auth.py#L155)) |
| `DELETE` | `/api/users/{user_id}` | Path: `user_id` | `200 OK`, `404 Not Found` | Admin | Yes | Deletes specified user account ([`auth.py:203`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/auth.py#L203)) |
| `GET` | `/admin/users` | None | `200 OK`, `403 Forbidden` | Admin | No | Renders HTML user administration UI ([`auth.py:228`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/auth.py#L228)) |
| `POST` | `/admin/users` | Form: `username`, `password`, `role` | `303 See Other`, `400 Bad Request` | Admin | Yes | Creates user from Admin web form ([`auth.py:251`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/auth.py#L251)) |
| `POST` | `/admin/users/{user_id}/delete` | Path: `user_id` | `303 See Other` | Admin | Yes | Deletes user from Admin web UI ([`auth.py:292`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/auth.py#L292)) |

---

### 1.2 Pipeline Status & Ingestion Routes (`routes/status.py`)

| Method | Path | Request Body / Params | Response Codes | RBAC Tier | CSRF Protected | Description & Source Citation |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `GET` | `/` | None | `200 OK` | Viewer | No | Renders primary operations dashboard ([`status.py:552`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/status.py#L552)) |
| `POST` | `/process-video` | Form: `video_url`, `dry_run`, `force_rerun`, `register_channel`, `overrides` | `303 See Other`, `400 Bad Request` | Operator | Yes | Spawns background video ingestion pipeline ([`status.py:604`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/status.py#L604)) |
| `POST` | `/process-video/{video_id}/cancel` | Path: `video_id` | `303 See Other` | Operator | Yes | Cooperatively terminates active pipeline task ([`status.py:868`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/status.py#L868)) |
| `POST` | `/process-video/{video_id}/retry` | Path: `video_id` | `303 See Other` | Operator | Yes | Retries failed pipeline from failure stage ([`status.py:916`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/status.py#L916)) |
| `POST` | `/process-video/{video_id}/resume` | Path: `video_id` | `303 See Other` | Operator | Yes | Resumes stalled/interrupted pipeline ([`status.py:980`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/status.py#L980)) |
| `POST` | `/process-video/{video_id}/clean-retry` | Path: `video_id` | `303 See Other` | Operator | Yes | Purges media fragments and restarts from scratch ([`status.py:1023`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/status.py#L1023)) |
| `POST` | `/process-video/{video_id}/delete` | Path: `video_id`, Form: `delete_files` | `303 See Other` | Operator | Yes | Deletes source video record and files ([`status.py:1102`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/status.py#L1102)) |
| `GET` | `/api/thumbnails/{video_or_clip_id}` | Path: `video_or_clip_id` | `200 OK`, `307 Redirect` | Viewer | No | Serves local thumbnail or redirects to YouTube CDN ([`status.py:1199`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/status.py#L1199)) |
| `GET` | `/api/events/dashboard` | None | `200 OK` (SSE) | Viewer | No | Real-time Server-Sent Events stream for telemetry ([`status.py:1296`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/status.py#L1296)) |
| `GET` | `/api/video-status/{video_id:path}` | Path: `video_id` | `200 OK` | Viewer | No | Pre-flight validation query for URL/ID ([`status.py:1379`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/status.py#L1379)) |
| `GET` | `/api/status` | None | `200 OK` | Viewer | No | Comprehensive JSON summary of system state ([`status.py:1533`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/status.py#L1533)) |
| `POST` | `/api/analytics/sync` | None | `200 OK` | Operator | Yes | Triggers background YouTube Analytics sync ([`status.py:1559`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/status.py#L1559)) |

---

### 1.3 Review Queue & Quality Gates (`routes/queue.py`)

| Method | Path | Request Body / Params | Response Codes | RBAC Tier | CSRF Protected | Description & Source Citation |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `GET` | `/queue` | Query: `tab`, `search`, `provider` | `200 OK` | Viewer | No | Review queue UI with card and table views ([`queue.py:65`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L65)) |
| `GET` | `/queue/export` | Query: `format` (`csv` / `json`) | `200 OK` | Viewer | No | Metadata export of approved clips ([`queue.py:324`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L324)) |
| `GET` | `/api/video/{clip_id}` | Path: `clip_id` | `200 OK`, `206 Partial` | Viewer | No | Byte-range video streaming endpoint ([`queue.py:402`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L402)) |
| `POST` | `/queue/{clip_id}/approve` | Path: `clip_id` | `303 See Other` | Operator | Yes | Transitions clip to `APPROVED` ([`queue.py:424`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L424)) |
| `POST` | `/queue/{clip_id}/undo` | Path: `clip_id` | `303 See Other` | Operator | Yes | Reverts clip to `WAITING_APPROVAL` ([`queue.py:462`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L462)) |
| `POST` | `/queue/{clip_id}/schedule` | Path: `clip_id`, Form: `publish_at` | `303 See Other` | Operator | Yes | Schedules clip publication time ([`queue.py:486`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L486)) |
| `POST` | `/queue/{clip_id}/select-variation` | Path: `clip_id`, Form: `variant_index` | `303 See Other` | Operator | Yes | Activates selected A/B title/hook variant ([`queue.py:555`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L555)) |
| `POST` | `/queue/{clip_id}/reject` | Path: `clip_id` | `303 See Other` | Operator | Yes | Moves clip to `REJECTED` ([`queue.py:602`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L602)) |
| `POST` | `/queue/{clip_id}/restore` | Path: `clip_id` | `303 See Other` | Operator | Yes | Restores rejected clip back to queue ([`queue.py:646`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L646)) |
| `POST` | `/queue/re-evaluate/{video_id}` | Path: `video_id` | `303 See Other` | Operator | Yes | Re-runs QA gates across all clips of a video ([`queue.py:675`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L675)) |
| `POST` | `/api/clip/{clip_id}/open-folder` | Path: `clip_id` | `200 OK` | Operator | Yes | Launches host native file explorer highlighting clip ([`queue.py:702`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L702)) |
| `GET` | `/queue/clip/{clip_id}/diagnostics`| Path: `clip_id` | `200 OK` | Viewer | No | Returns QA gate failure breakdown and suggested auto-fixes ([`queue.py:715`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L715)) |
| `POST` | `/queue/clip/{clip_id}/re-evaluate` | Path: `clip_id` | `200 OK` | Operator | Yes | Re-checks single clip against QA gates ([`queue.py:729`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L729)) |
| `POST` | `/queue/clip/{clip_id}/force-approve`| Path: `clip_id`, Form: `override_reason` | `200 OK` | Operator | Yes | Operator bypass of QA failure gates ([`queue.py:758`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L758)) |
| `POST` | `/queue/clip/{clip_id}/fix` | Path: `clip_id`, JSON: `fix_type` | `200 OK` | Operator | Yes | Applies auto-fix (`DURATION_CLAMP`, `SHIFT_MARGIN`, `REANCHOR_TRANSCRIPT`) ([`queue.py:787`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L787)) |
| `POST` | `/queue/bulk-action` | Form: `action`, `clip_ids` | `303 See Other` | Operator | Yes | Bulk approval or rejection ([`queue.py:816`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L816)) |
| `POST` | `/queue/{clip_id}/re-render-style` | Path: `clip_id`, Form: `preset`, style params | `303 See Other` | Operator | Yes | Re-renders video with updated caption ASS styling ([`queue.py:850`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L850)) |
| `POST` | `/queue/{clip_id}/trim` | Path: `clip_id`, Form: `start_ms`, `end_ms` | `303 See Other` | Operator | Yes | Sub-frame trim with speech boundary snapping ([`queue.py:997`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L997)) |
| `POST` | `/queue/{clip_id}/delete` | Path: `clip_id`, Form: `delete_files` | `303 See Other` | Operator | Yes | Deletes clip record and disk assets ([`queue.py:1050`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L1050)) |
| `GET` | `/dead-letter` | None | `200 OK` | Viewer | No | Dead-letter queue failure UI ([`queue.py:1094`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L1094)) |
| `POST` | `/dead-letter/clip/{clip_id}/retry` | Path: `clip_id` | `303 See Other` | Operator | Yes | Retries failed clip render ([`queue.py:1119`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L1119)) |
| `POST` | `/dead-letter/video/{video_id}/retry`| Path: `video_id` | `303 See Other` | Operator | Yes | Retries failed source video ([`queue.py:1151`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L1151)) |
| `POST` | `/dead-letter/clip/{clip_id}/delete` | Path: `clip_id` | `303 See Other` | Operator | Yes | Purges failed clip from DLQ ([`queue.py:1184`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L1184)) |
| `POST` | `/dead-letter/video/{video_id}/delete`| Path: `video_id` | `303 See Other` | Operator | Yes | Purges failed source video from DLQ ([`queue.py:1220`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L1220)) |
| `POST` | `/dead-letter/clear-all` | Form: `wipe_files` | `303 See Other` | Operator | Yes | Bulk clears all DLQ failures ([`queue.py:1265`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/queue.py#L1265)) |

---

### 1.4 Channels & WebSub Routes (`routes/channels.py`, `routes/websub.py`)

| Method | Path | Request Body / Params | Response Codes | RBAC Tier | CSRF Protected | Description & Source Citation |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `GET` | `/channels` | None | `200 OK` | Viewer | No | Renders channel manager UI ([`channels.py:38`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/channels.py#L38)) |
| `GET` | `/api/channels` | None | `200 OK` | Viewer | No | Returns channel list JSON ([`channels.py:62`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/channels.py#L62)) |
| `POST` | `/channels/{channel_id}/toggle-watch` | Path: `channel_id` | `303 See Other` | Operator | Yes | Toggles automatic watch poller ([`channels.py:81`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/channels.py#L81)) |
| `POST` | `/channels/{channel_id}/toggle-review`| Path: `channel_id` | `303 See Other` | Operator | Yes | Toggles human review requirement ([`channels.py:115`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/channels.py#L115)) |
| `POST` | `/channels/{channel_id}/trigger-poll` | Path: `channel_id` | `303 See Other` | Operator | Yes | Triggers manual RSS poll ([`channels.py:152`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/channels.py#L152)) |
| `POST` | `/channels/{channel_id}/subscribe-websub` | Path: `channel_id` | `303 See Other` | Operator | Yes | Registers WebSub subscription ([`channels.py:179`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/channels.py#L179)) |
| `POST` | `/channels/add` | Form: `channel_id`, `name`, `auto_publish` | `303 See Other` | Operator | Yes | Registers new monitored channel ([`channels.py:206`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/channels.py#L206)) |
| `POST` | `/channels/{channel_id}/delete` | Path: `channel_id` | `303 See Other` | Operator | Yes | Deletes channel and cascaded data ([`channels.py:273`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/channels.py#L273)) |
| `GET` | `/api/channels/{channel_id}/videos` | Path: `channel_id` | `200 OK` | Viewer | No | Lists public uploads with status ([`channels.py:334`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/channels.py#L334)) |
| `GET` | `/websub/callback` | Query: `hub.mode`, `hub.challenge`, `hub.topic` | `200 OK`, `404 Not Found` | Public | No | WebSub verification handshake ([`websub.py:34`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/websub.py#L34)) |
| `POST` | `/websub/callback` | Headers: `X-Hub-Signature`, Body: Atom XML | `204 No Content`, `403 Forbidden` | Public | No | HMAC-SHA1 verified push notification ([`websub.py:59`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/websub.py#L59)) |

---

### 1.5 Settings & API Key Management (`routes/settings.py`)

| Method | Path | Request Body / Params | Response Codes | RBAC Tier | CSRF Protected | Description & Source Citation |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `GET` | `/settings` | None | `200 OK` | Viewer | No | Renders settings editor UI ([`settings.py:94`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/settings.py#L94)) |
| `POST` | `/settings` | Form: key-value settings pairs | `303 See Other` | Admin | Yes | Updates runtime settings and persists to disk ([`settings.py:167`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/settings.py#L167)) |
| `POST` | `/settings/keys/delete` | Form: `provider`, `key` | `303 See Other` | Admin | Yes | Removes individual API key ([`settings.py:371`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/settings.py#L371)) |
| `POST` | `/settings/keys/add` | Form: `provider`, `key` | `303 See Other` | Admin | Yes | Validates and appends API key ([`settings.py:410`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/settings.py#L410)) |
| `POST` | `/settings/keys/test` | Form: `provider`, `key` | `200 OK` | Admin | Yes | Live ping/probe of API key ([`settings.py:454`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/settings.py#L454)) |

---

### 1.6 Observability, Calendar & Logs (`routes/health.py`, `routes/metrics.py`, `routes/logs.py`, `routes/calendar.py`)

| Method | Path | Request Body / Params | Response Codes | RBAC Tier | CSRF Protected | Description & Source Citation |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `GET` | `/livez` | None | `200 OK` | Public | No | Kubernetes liveness probe ([`health.py:32`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/health.py#L32)) |
| `GET` | `/readyz` | None | `200 OK`, `503 Unavailable` | Public | No | Deep readiness check (DB, Storage, FFmpeg) ([`health.py:45`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/health.py#L45)) |
| `GET` | `/healthz` | None | `200 OK`, `503 Unavailable` | Public | No | Unified health check summary ([`health.py:156`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/health.py#L156)) |
| `GET` | `/metrics` | None | `200 OK` (Prometheus) | Public | No | Prometheus/OpenMetrics text exposition ([`metrics.py:23`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/metrics.py#L23)) |
| `GET` | `/calendar` | None | `200 OK` | Viewer | No | Editorial publication calendar UI ([`calendar.py:36`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/calendar.py#L36)) |
| `GET` | `/api/calendar/events` | Query: `start`, `end` | `200 OK` | Viewer | No | FullCalendar JSON events feed ([`calendar.py:63`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/calendar.py#L63)) |
| `POST` | `/api/calendar/reschedule/{clip_id}` | Path: `clip_id`, JSON: `scheduled_publish_at` | `200 OK` | Operator | Yes | Drag-and-drop reschedule clip ([`calendar.py:153`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/calendar.py#L153)) |
| `GET` | `/logs` | None | `200 OK` | Viewer | No | Real-time live log viewer UI ([`logs.py:37`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/logs.py#L37)) |
| `GET` | `/api/logs` | Query: `limit`, `level` | `200 OK` | Viewer | No | Tail recent log records as JSON ([`logs.py:55`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/logs.py#L55)) |
| `GET` | `/api/events/logs` | None | `200 OK` (SSE) | Viewer | No | Real-time SSE stream of engine logs ([`logs.py:63`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/logs.py#L63)) |

---

## 2. CLI Command Catalog (`cli.py`, `run.py`)

All commands are executable via `python run.py <command>` or `python cli.py <command>`. `run.py` initializes Sentry SDK before delegating to `cli.py` ([`run.py:12-33`](file:///d:/Projects/YT%20AUTOMATION/run.py#L12-L33)).

| Command Name | Arguments / Options | Option Flags | Default Value | Description & Source Citation |
| :--- | :--- | :--- | :--- | :--- |
| `init-db` | None | - | - | Initializes database schema and tables ([`cli.py:36-42`](file:///d:/Projects/YT%20AUTOMATION/cli.py#L36-L42)) |
| `rotate-secrets` | `old_key`, `new_key` | `--old-key`, `--new-key` | Required | Bulk re-encrypts all channel OAuth tokens ([`cli.py:44-65`](file:///d:/Projects/YT%20AUTOMATION/cli.py#L44-L65)) |
| `backup-db` | `no_compress` | `--no-compress` | `False` | Creates atomic hot backup in `storage/backups/` ([`cli.py:68-88`](file:///d:/Projects/YT%20AUTOMATION/cli.py#L68-L88)) |
| `list-backups` | None | - | - | Displays table of all local DB backup archives ([`cli.py:90-106`](file:///d:/Projects/YT%20AUTOMATION/cli.py#L90-L106)) |
| `restore-db` | `file`, `yes` | `--file` / `-f`, `--yes` / `-y` | Required, `False` | Restores SQLite database from backup archive ([`cli.py:108-133`](file:///d:/Projects/YT%20AUTOMATION/cli.py#L108-L133)) |
| `channel add` | `channel_id`, `name`, `auto_publish` | `--channel-id` / `-c`, `--name` / `-n`, `--auto-publish` | Required, Required, `False` | Adds YouTube channel to watch list ([`cli.py:135-160`](file:///d:/Projects/YT%20AUTOMATION/cli.py#L135-L160)) |
| `channel list` | None | - | - | Lists watched channels, watch states, review flags ([`cli.py:162-186`](file:///d:/Projects/YT%20AUTOMATION/cli.py#L162-L186)) |
| `channel remove`| `channel_id` | `--channel-id` / `-c` | Required | Removes channel and cascades orphaned data ([`cli.py:188-209`](file:///d:/Projects/YT%20AUTOMATION/cli.py#L188-L209)) |
| `process` | `video`, `dry_run` | `--video` / `-v`, `--dry-run` / `--live` | Required, `True` | Runs end-to-end pipeline on YouTube video ([`cli.py:211-231`](file:///d:/Projects/YT%20AUTOMATION/cli.py#L211-L231)) |
| `resume` | `video`, `dry_run` | `--video` / `-v`, `--dry-run` / `--live` | Required, `False` | Resumes interrupted pipeline from last checkpoint ([`cli.py:233-253`](file:///d:/Projects/YT%20AUTOMATION/cli.py#L233-L253)) |
| `reconcile-stale`| None | - | - | Releases dead worker locks and purges temp files ([`cli.py:255-269`](file:///d:/Projects/YT%20AUTOMATION/cli.py#L255-L269)) |
| `status` | None | - | - | Displays summary metrics and YouTube quota remaining ([`cli.py:271-300`](file:///d:/Projects/YT%20AUTOMATION/cli.py#L271-L300)) |
| `watch` | `interval` | `--interval` / `-i` | `RSS_POLL_INTERVAL_MINUTES` | Starts blocking RSS fallback poller loop ([`cli.py:302-341`](file:///d:/Projects/YT%20AUTOMATION/cli.py#L302-L341)) |
| `dashboard` | `host`, `port`, `reload` | `--host`, `--port` / `-p`, `--reload` | `0.0.0.0`, `8000`, `False` | Starts Uvicorn ASGI server for web dashboard ([`cli.py:343-368`](file:///d:/Projects/YT%20AUTOMATION/cli.py#L343-L368)) |
| `subscribe` | `channel_id` | `--channel-id` / `-c` | Required | Sends WebSub subscription challenge to Google Hub ([`cli.py:370-395`](file:///d:/Projects/YT%20AUTOMATION/cli.py#L370-L395)) |
| `create-user` | `username`, `password`, `role` | `--username` / `-u`, `--password` / `-p`, `--role` / `-r` | Required, Required, `operator` | Bootstraps user account with PBKDF2 hashing ([`cli.py:397-446`](file:///d:/Projects/YT%20AUTOMATION/cli.py#L397-L446)) |
| `worker` | `concurrency`, `poll_interval` | `--concurrency` / `-c`, `--poll-interval` | `1`, `5.0` | Starts headless rendering and QA worker daemon ([`cli.py:448-464`](file:///d:/Projects/YT%20AUTOMATION/cli.py#L448-L464)) |

---

## 3. Windows Desktop Control Center Scripts

Located in [`Shorts_Engine_Control_Center/`](file:///d:/Projects/YT%20AUTOMATION/Shorts_Engine_Control_Center/):
- **`START_HERE.bat`**: Interactive launcher menu for all control actions.
- **`START_DASHBOARD.bat`**: Runs `python run.py dashboard --host 0.0.0.0 --port 8000`.
- **`START_ALL_SERVICES.bat`**: Spawns background worker, RSS poller, and dashboard concurrently.
- **`RUN_WORKER.bat`**: Runs headless render/QA worker daemon.
- **`START_WATCH_LOOP.bat`**: Starts YouTube channel RSS monitor.
- **`PROCESS_VIDEO.bat`**: Interactive prompt asking for YouTube URL and executing `python run.py process`.
- **`CHECK_STATUS.bat`**: Displays CLI health status table and exits.
- **`BACKUP_DATABASE.bat`**: Executes `python run.py backup-db`.
- **`BOOTSTRAP_ADMIN.bat`**: Interactive prompt creating or resetting an administrative account.
- **`VIEW_LOGS.bat`**: Live PowerShell `Get-Content -Wait` stream on `storage/logs/shorts_engine.log`.
