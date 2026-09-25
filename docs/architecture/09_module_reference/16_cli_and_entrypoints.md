# Module Reference: 16 Command-Line Interface & Application Entry Points

> **Path:** Repository Root & `Shorts_Engine_Control_Center/`  
> **Source Files:**
> - [`run.py`](file:///d:/Projects/YT%20AUTOMATION/run.py)
> - [`cli.py`](file:///d:/Projects/YT%20AUTOMATION/cli.py)
> - [`Shorts_Engine_Control_Center/START_HERE.bat`](file:///d:/Projects/YT%20AUTOMATION/Shorts_Engine_Control_Center/START_HERE.bat)
> - [`Shorts_Engine_Control_Center/01_Servers_and_Workers/start_dashboard.bat`](file:///d:/Projects/YT%20AUTOMATION/Shorts_Engine_Control_Center/01_Servers_and_Workers/start_dashboard.bat)
> - [`Shorts_Engine_Control_Center/01_Servers_and_Workers/start_worker.bat`](file:///d:/Projects/YT%20AUTOMATION/Shorts_Engine_Control_Center/01_Servers_and_Workers/start_worker.bat)
> - [`Shorts_Engine_Control_Center/03_Operations_and_Pipelines/process_video.bat`](file:///d:/Projects/YT%20AUTOMATION/Shorts_Engine_Control_Center/03_Operations_and_Pipelines/process_video.bat)
> - [`Shorts_Engine_Control_Center/04_Admin_and_Distribution/create_user.bat`](file:///d:/Projects/YT%20AUTOMATION/Shorts_Engine_Control_Center/04_Admin_and_Distribution/create_user.bat)

---

## 1. Module Purpose & Responsibilities

This subsystem provides terminal and batch interfaces for operators, container runners, and background system services:
1. **Application Bootstrapping & Sentry Initialization:** `run.py` intercepts process startup, initializes Sentry SDK error tracking once (`traces_sample_rate=0.1, release="shorts-engine@2.0.0"`), and delegates argument parsing to Typer CLI (`run.py:12-34`).
2. **Typer CLI Command Suite:** `cli.py` exposes 16 command endpoints across operations, database maintenance, user provisioning, and media execution using Rich terminal formatting (`cli.py:27-465`).
3. **Channel Sub-Commands:** Nested Typer sub-app (`channel_app`) managing watched channels (`channel add`, `channel list`, `channel remove`) (`cli.py:28-29, 135-210`).
4. **Desktop Operator Control Center:** Native Windows batch scripts structured into functional categories (`01_Servers_and_Workers`, `02_Testing_and_Validation`, `03_Operations_and_Pipelines`, `04_Admin_and_Distribution`) driven by an interactive numbered menu launcher (`START_HERE.bat`).

---

## 2. CLI Command Catalog

| Command | Category | Function Signature | Description |
| :--- | :--- | :--- | :--- |
| `init-db` | Database | `cmd_init_db()` | Initializes SQLAlchemy database schema tables and indexes (`cli.py:36-42`). |
| `rotate-secrets` | Security | `cmd_rotate_secrets(old_key, new_key)` | Bulk re-encrypts all channel OAuth tokens in a single atomic transaction (`cli.py:44-66`). |
| `backup-db` | Database | `cmd_backup_db(no_compress)` | Creates an online hot SQLite backup with SHA-256 integrity verification (`cli.py:68-88`). |
| `list-backups` | Database | `cmd_list_backups()` | Displays a table of all available backup archives with sizes and timestamps (`cli.py:90-106`). |
| `restore-db` | Database | `cmd_restore_db(file, yes)` | Restores an SQLite database from backup archive with safety snapshotting (`cli.py:108-133`). |
| `channel add` | Channels | `cmd_channel_add(channel_id, name, auto_publish)` | Registers a new YouTube channel to watch (`cli.py:135-160`). |
| `channel list` | Channels | `cmd_channel_list()` | Formats and displays all watched channels and their review requirements (`cli.py:162-186`). |
| `channel remove` | Channels | `cmd_channel_remove(channel_id)` | Removes a configured channel and associated pipeline data (`cli.py:188-210`). |
| `process` | Pipeline | `cmd_process(video, dry_run)` | Runs the end-to-end shorts generation pipeline on a video URL/ID (`cli.py:211-232`). |
| `resume` | Pipeline | `cmd_resume(video, dry_run)` | Resumes an interrupted video processing run from its last checkpoint (`cli.py:233-254`). |
| `reconcile-stale` | Ops | `cmd_reconcile_stale()` | Reconciles dead worker claims and deletes orphaned temp files (`cli.py:255-270`). |
| `status` | Telemetry | `cmd_status()` | Displays system status, queue size, and daily API quota metrics (`cli.py:271-301`). |
| `watch` | Daemon | `cmd_watch(interval)` | Starts the blocking RSS channel poller (`cli.py:302-342`). |
| `dashboard` | Web | `cmd_dashboard(host, port, reload)` | Starts the Uvicorn ASGI web server on configured host and port (`cli.py:343-368`). |
| `subscribe` | WebSub | `cmd_subscribe(channel_id)` | Sends a PubSubHubbub subscription request to YouTube hub (`cli.py:369-395`). |
| `create-user` | Auth | `cmd_create_user(username, password, role)` | Creates/updates user with PBKDF2 hash (min 12 characters) (`cli.py:397-446`). |
| `worker` | Worker | `cmd_worker(concurrency, poll_interval)` | Runs headless rendering daemon with dialect-aware locking (`cli.py:448-463`). |

---

## 3. Desktop Control Center Structure

```
Shorts_Engine_Control_Center/
├── START_HERE.bat                        # Interactive master menu
├── README.txt                            # Operator instructions
├── 01_Servers_and_Workers/
│   ├── start_dashboard.bat               # Launches dashboard at http://127.0.0.1:8000
│   └── start_worker.bat                  # Starts background rendering worker
├── 02_Testing_and_Validation/
│   ├── run_all_tests.bat                 # Executes pytest test suite (266 tests)
│   └── run_dashboard_tests.bat           # Executes dashboard-specific tests
├── 03_Operations_and_Pipelines/
│   ├── add_channel.bat                   # Interactive prompt to add watched channel
│   └── process_video.bat                 # Prompts for YouTube URL to process
└── 04_Admin_and_Distribution/
    ├── build_bundle.bat                  # Builds distribution zip archive
    └── create_user.bat                   # Interactive admin/operator provisioning
```

---

## 4. Invariants & Safety Guardrails

1. **Non-Crashing Error Tracking:** `run.py` wraps `sentry_sdk.init()` in an unconditional `try...except` block so missing credentials or network configuration issues never prevent CLI or server startup (`run.py:14-25`).
2. **Password Length Enforcement:** `create-user` strictly enforces `len(password) >= 12` characters, refusing to create insecure operator accounts (`cli.py:410-412`).
3. **Interactive Confirmation on Restore:** `restore-db` requires explicit confirmation (`--yes` or interactive prompt) before overwriting the active database, snapshotting the existing file prior to replacement (`cli.py:120-128`).
