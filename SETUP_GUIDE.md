# YouTube Shorts Automation Engine - Complete Device Setup & Deployment Guide

> **Enterprise-Grade, Zero-Leakage Setup Guide for New Machines & Distributed Teams**  
> *Target Environments:* Windows 10/11, Linux (Ubuntu/Debian/RHEL/Arch), macOS (Apple Silicon / Intel).

---

## 1. System Requirements & Architecture Overview

The **YouTube Shorts Automation Engine** is a high-throughput, autonomous video ingestion, AI clip extraction, reframing, and mastering system. It consists of:
- **FastAPI Operator Dashboard:** Web UI with real-time SSE telemetry, clip review queue, and multi-tier key manager.
- **Multi-Provider AI Intelligence:** 4-tier router supporting Groq LPU, Google Gemini, OpenRouter, and OpenAI with automatic 429 failover.
- **Audio & Video Engine:** Faster-Whisper transcription, MediaPipe/OpenCV face tracking, and FFmpeg filtergraph mastering.
- **Background Worker & Scheduler:** Non-blocking job claiming with GPU VRAM recycling and YouTube/WebSub auto-detection.

### Hardware Prerequisites
| Component | Minimum Specification | Recommended Specification |
|:---|:---|:---|
| **CPU** | 4 Cores (x86_64 or ARM64) | 8+ Cores |
| **RAM** | 8 GB System Memory | 16 GB+ System Memory |
| **Disk Storage** | 10 GB Free Storage (SSD) | 50 GB+ High-Speed NVMe |
| **GPU (Optional)** | Integrated Graphics (CPU mode) | NVIDIA GPU (CUDA 12.x, 6GB+ VRAM) for 5x faster transcription & rendering |

### Software Prerequisites
1. **Python:** Version `3.10` or `3.11` (Python 3.11 recommended).
2. **Git:** Any modern Git distribution (v2.30+).
3. **FFmpeg & FFprobe:** Installed and available on your system `PATH`.

---

## 2. Quickstart: 1-Click Automated Setup

If you want the fastest path from a fresh `git clone` to a running dashboard, use the automated bootstrap scripts:

### Windows (Command Prompt or PowerShell)
Double-click `setup.bat` or run:
```cmd
setup.bat
```
*(Or in PowerShell: `.\setup.ps1`)*

### Linux & macOS
Open your terminal in the cloned directory and run:
```bash
chmod +x setup.sh
./setup.sh
```

### What the 1-Click Script Does Automatically:
1. Detects Python 3.10/3.11+ and checks for system FFmpeg.
2. Creates an isolated `.venv` virtual environment.
3. Upgrades `pip` and installs all locked dependencies from `requirements.txt`.
4. Copies `.env.example` to `.env` and generates fresh 256-bit cryptographic keys.
5. Verifies and creates all required local `storage/` directories.
6. Initializes database tables and schema migrations.
7. Automatically seeds the initial administrator account (`admin` / your `DASHBOARD_PASSWORD`).

---

## 3. Manual Step-by-Step Installation (Detailed Control)

For engineers and system administrators who prefer executing each setup phase manually:

### Step 1: Clone the Repository
```bash
git clone <YOUR_GITHUB_REPO_URL>
cd "YT AUTOMATION"
```

---

### Step 2: Install FFmpeg & FFprobe

FFmpeg is essential for video slicing, audio normalization, ASS subtitle burning, and 9:16 vertical reframing.

#### **Windows:**
Open PowerShell as Administrator:
```powershell
# Option A (Recommended via winget):
winget install Gyan.FFmpeg

# Option B (via Chocolatey):
choco install ffmpeg
```
*Verify in a new terminal:*
```cmd
ffmpeg -version
ffprobe -version
```

#### **Ubuntu / Debian:**
```bash
sudo apt update
sudo apt install -y ffmpeg
```

#### **macOS (Homebrew):**
```bash
brew install ffmpeg
```

---

### Step 3: Create & Activate Virtual Environment

Keep your operating system clean by creating an isolated virtual environment:

#### **Windows:**
```cmd
python -m venv .venv
.venv\Scripts\activate
```
*(If using PowerShell and encountering execution policy errors, run: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`)*

#### **Linux / macOS:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

---

### Step 4: Install Dependencies

Install the core production package suite:
```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

*(Optional) If you plan to run automated tests or browser verification:*
```bash
pip install -r requirements-dev.txt
```

*(Optional) For NVIDIA GPU hardware acceleration (CUDA):*
```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
```

---

### Step 5: Configure Environment Variables (`.env`)

1. Copy the provided enterprise template:
   - **Windows:** `copy .env.example .env`
   - **Linux/macOS:** `cp .env.example .env`

2. Open `.env` in your text editor and review key settings:

```dotenv
# Operational mode
ENV=development
DEBUG=false

# SQLite database (zero-configuration local run)
DATABASE_URL=sqlite:///./storage/shorts_automation.db

# Cryptographic Keys (generate with python -c "import secrets; print(secrets.token_urlsafe(32))")
MASTER_ENCRYPTION_KEY=yt_shorts_dev_master_secret_key_32_bytes_min!
DASHBOARD_SECRET_KEY=yt_shorts_dev_dashboard_session_secret_32_bytes!
ENCRYPTION_SALT=yt_shorts_kdf_salt_2026_secure!

# Operator Dashboard
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8000
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=change-me-generate-a-unique-32-char-value

# Multi-Provider AI Keys (Configure any or all - free tiers available)
GROQ_API_KEYS=gsk_...
GEMINI_API_KEYS=AQ...
OPENROUTER_API_KEYS=sk-or-v1-...
```

#### Where to get free AI API keys:
- **Groq LPU (Sub-300ms speed):** [https://console.groq.com/keys](https://console.groq.com/keys)
- **Google Gemini (1M context):** [https://aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
- **OpenRouter (Multi-model free aggregator):** [https://openrouter.ai/keys](https://openrouter.ai/keys)

> *Note:* You can paste API keys directly into `.env`, or configure and hot-swap them at runtime via the dashboard Settings page (`http://127.0.0.1:8000/settings`).

---

### Step 6: Initialize Database & Seed Administrator

Run the automated bootstrap helper:
```bash
python setup_environment.py
```

*Or perform database and user initialization via the CLI:*
```bash
# 1. Initialize SQLite/Postgres schema tables
python run.py init-db

# 2. Seed initial admin user
python run.py create-user --username admin --password "<your-own-strong-password>" --role admin
```

---

### Step 7: Run System Verification & Test Suite

Verify that all 251 unit, integration, and security tests pass cleanly on your device:
```bash
python -m pytest tests -q
```
*Expected output:* `251 passed in ...s`

---

## 4. Running the Platform

Once setup is complete, the platform is operated through three primary services:

### 1. Launch the Operator Web Dashboard
```bash
python run.py dashboard --host 127.0.0.1 --port 8000
```
- Open your browser at: **`http://127.0.0.1:8000/`**
- **Default Login:**
  - **Username:** `admin`
  - **Password:** the `DASHBOARD_PASSWORD` value from your `.env` *(or the password set during user creation)*

### 2. Start the Background Rendering & Verification Worker
In a second terminal window (with virtual environment active):
```bash
python run.py worker
```
The worker monitors the job queue, claims clips, tracks facial focal points, and renders final vertical MP4 clips with animated captions and SFX.

### 3. Monitor Channels via Automatic RSS Polling (Optional)
In a third terminal window:
```bash
python run.py watch --interval 15
```
Automatically detects new video uploads across monitored channels and enqueues them for processing.

### 4. Interactive Master Control Center (Windows Only)
On Windows, you can double-click **`START_HERE.bat`** in the repository root to launch an interactive menu for one-click server starting, worker launching, testing, and clip management.

---

## 5. Repository Hygiene & `.gitignore` Validation

To guarantee that no secrets, database dumps, virtual environments, or heavy video files leak when sharing the repository:

### Verified `.gitignore` Inclusions:
- **Zero Secret Exposure:** `.env`, `.env.*`, OAuth `token.json`, and Google `client_secret*.json` are strictly excluded. `.env.example` is explicitly preserved (`!.env.example`).
- **Zero Python Artifacts:** `.venv/`, `venv/`, `__pycache__/`, `*.pyc`, `dist/`, and `build/` are ignored.
- **Zero Heavy Binary Leaks:** All `.mp4`, `.mov`, `.wav`, and `.mp3` media files are ignored, except for the bundled sound effects library in `assets/sfx_library/`.
- **Directory Structure Preservation:** All operational directories (`storage/downloads/`, `storage/outputs/`, `storage/temp/`, `storage/thumbnails/`, `storage/models/`, `storage/logs/`, `storage/config/`) contain `.gitkeep` files so new clones automatically possess the full directory tree.

You can verify your Git status at any time:
```bash
git status
```
It should show only tracked code and documentation, with no untracked binaries or secrets.

---

## 6. Docker & Containerized Deployment (Alternative)

For teams using Docker or Kubernetes:

```bash
# 1. Create .env from template
cp .env.example .env

# 2. Build and launch containers
docker-compose up -d --build

# 3. View live logs
docker-compose logs -f dashboard
```
- The dashboard will be accessible at: `http://localhost:8000`
- Volume mappings preserve database records and outputs across container rebuilds:
  - `./storage:/app/storage`

---

## 7. Troubleshooting & FAQ

#### Q: `ffmpeg: command not found`
- **Fix:** Ensure FFmpeg was installed and its `bin` directory was added to your operating system's `PATH`. Close and reopen your terminal or IDE after installing.

#### Q: `sqlite3.OperationalError: database is locked`
- **Fix:** The engine automatically enables SQLite WAL (Write-Ahead Logging) mode and a 30-second busy timeout. If a previous run was abruptly terminated, run:
  ```bash
  python run.py reconcile-stale
  ```

#### Q: How do I change the admin password or create an operator account?
- **Fix:** Use the CLI:
  ```bash
  python run.py create-user --username new_admin --password "NewSecurePass123!" --role admin
  python run.py create-user --username editor --password "EditorPass123!" --role operator
  ```

#### Q: Port 8000 is already in use
- **Fix:** Specify a different port when starting the dashboard:
  ```bash
  python run.py dashboard --port 8080
  ```

#### Q: Why are my clip cards marked `WAITING_APPROVAL`?
- **Fix:** By default, channels require human review before publishing. Navigate to the **Review Queue** (`http://127.0.0.1:8000/queue`) to inspect hooks, preview the video, and click **Approve**. To enable auto-publishing without review, toggle "Human Review" in the Channel Manager.
