#!/usr/bin/env python3
"""
==============================================================================
YouTube Shorts Automation Platform - Cross-Platform Bootstrap & Setup Script
==============================================================================
This automated script validates prerequisites, sets up directory structures,
bootstraps secure environment configs (.env), initializes the database,
and seeds the default administrator account.

Works seamlessly on Windows, macOS, and Linux.
==============================================================================
"""

import sys
import os
import shutil
import secrets
from pathlib import Path

# Minimum Python version requirement
MIN_PYTHON_VERSION = (3, 10)
RECOMMENDED_PYTHON_VERSION = (3, 11)


def print_banner():
    banner = """
==============================================================================
     *** YOUTUBE SHORTS AUTOMATION - ENVIRONMENT SETUP & BOOTSTRAP ***
==============================================================================
"""
    print(banner)


def check_python_version():
    print("[1/7] Checking Python version...")
    current = sys.version_info[:2]
    if current < MIN_PYTHON_VERSION:
        print(f"  [ERROR] Python {MIN_PYTHON_VERSION[0]}.{MIN_PYTHON_VERSION[1]}+ is required.")
        print(f"  Current version: Python {sys.version.split()[0]}")
        print("  Please upgrade your Python installation from https://www.python.org/downloads/")
        sys.exit(1)
    
    if current == RECOMMENDED_PYTHON_VERSION:
        print(f"  [OK] Python {sys.version.split()[0]} (Recommended version)")
    else:
        print(f"  [OK] Python {sys.version.split()[0]} (Meets minimum >= 3.10 requirement)")


def check_ffmpeg():
    print("\n[2/7] Checking FFmpeg & FFprobe media tools...")
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")

    if ffmpeg_path and ffprobe_path:
        print(f"  [OK] ffmpeg found:  {ffmpeg_path}")
        print(f"  [OK] ffprobe found: {ffprobe_path}")
        return True

    print("  [WARNING] FFmpeg and/or FFprobe not found on system PATH!")
    print("  The engine requires FFmpeg for video rendering, clipping, and audio mastering.")
    print("  Installation instructions by Operating System:")
    if sys.platform == "win32":
        print("    Windows (winget):  winget install Gyan.FFmpeg")
        print("    Windows (choco):   choco install ffmpeg")
        print("    Manual download:   https://ffmpeg.org/download.html")
    elif sys.platform == "darwin":
        print("    macOS (Homebrew):  brew install ffmpeg")
    else:
        print("    Ubuntu/Debian:     sudo apt update && sudo apt install -y ffmpeg")
        print("    Fedora/RHEL:       sudo dnf install -y ffmpeg")
        print("    Arch Linux:        sudo pacman -S ffmpeg")
    print("  (You can proceed with setup now, but make sure FFmpeg is installed before running pipelines.)")
    return False


def setup_directories(root_dir: Path):
    print("\n[3/7] Initializing storage and asset directory structure...")
    dirs_to_create = [
        root_dir / "storage",
        root_dir / "storage" / "downloads",
        root_dir / "storage" / "outputs",
        root_dir / "storage" / "temp",
        root_dir / "storage" / "thumbnails",
        root_dir / "storage" / "models",
        root_dir / "storage" / "logs",
        root_dir / "storage" / "config",
        root_dir / "assets",
        root_dir / "assets" / "sfx_library",
    ]

    for d in dirs_to_create:
        d.mkdir(parents=True, exist_ok=True)
        # Ensure .gitkeep exists so directories stay tracked in git
        gitkeep = d / ".gitkeep"
        if not gitkeep.exists() and d.name != "storage" and d.name != "assets":
            gitkeep.touch()
        print(f"  [OK] Directory verified: {d.relative_to(root_dir)}")


def bootstrap_env_file(root_dir: Path):
    print("\n[4/7] Checking environment configuration (.env)...")
    env_file = root_dir / ".env"
    env_example = root_dir / ".env.example"

    if env_file.exists():
        print(f"  [OK] Existing '.env' found ({env_file.stat().st_size} bytes). Preserving your settings.")
        return

    if not env_example.exists():
        print("  [ERROR] '.env.example' template not found in project root!")
        sys.exit(1)

    print("  Creating new '.env' from '.env.example' with cryptographically secure random keys...")
    content = env_example.read_text(encoding="utf-8")

    # Generate fresh cryptographically secure random keys
    master_key = secrets.token_urlsafe(32)
    session_key = secrets.token_urlsafe(32)
    salt = secrets.token_urlsafe(24)

    content = content.replace("yt_shorts_dev_master_secret_key_32_bytes_min!", master_key)
    content = content.replace("yt_shorts_dev_dashboard_session_secret_32_bytes!", session_key)
    content = content.replace("yt_shorts_kdf_salt_2026_secure!", salt)

    env_file.write_text(content, encoding="utf-8")
    print("  [OK] Successfully created '.env' with generated 256-bit encryption keys.")
    print("  NOTE: Add your Groq, Gemini, or OpenRouter API keys to '.env' or via the dashboard Settings page.")


def check_python_dependencies():
    print("\n[5/7] Checking core Python dependencies...")
    required_modules = [
        ("fastapi", "FastAPI"),
        ("uvicorn", "Uvicorn"),
        ("pydantic", "Pydantic"),
        ("sqlalchemy", "SQLAlchemy"),
        ("httpx", "HTTPX"),
        ("faster_whisper", "Faster-Whisper"),
        ("typer", "Typer"),
        ("rich", "Rich"),
    ]

    missing = []
    for mod_name, label in required_modules:
        try:
            __import__(mod_name)
            print(f"  [OK] {label}")
        except ImportError:
            missing.append(label)
            print(f"  [MISSING] {label}")

    if missing:
        print(f"\n  [ACTION REQUIRED] Missing required dependencies: {', '.join(missing)}")
        print("  Run the following command to install all dependencies:")
        print("    pip install -r requirements.txt")
        print("\n  If using a virtual environment (recommended):")
        if sys.platform == "win32":
            print("    python -m venv .venv")
            print("    .venv\\Scripts\\activate")
            print("    pip install -r requirements.txt")
        else:
            print("    python3 -m venv .venv")
            print("    source .venv/bin/activate")
            print("    pip install -r requirements.txt")
        return False

    return True


def initialize_database_and_admin(root_dir: Path):
    print("\n[6/7] Initializing database schema & admin account...")
    try:
        from shorts_engine.db.session import init_db, SessionLocal
        from shorts_engine.db.models import User, UserRole
        from shorts_engine.dashboard.auth import hash_password

        # Initialize schema tables
        init_db()
        print("  [OK] Database schema initialized.")

        # Seed initial admin user if none exists
        with SessionLocal() as db:
            admin_user = db.query(User).filter_by(role=UserRole.ADMIN).first()
            if admin_user:
                print(f"  [OK] Active administrator account found: '{admin_user.username}' (ID: {admin_user.id})")
            else:
                default_user = os.getenv("DASHBOARD_USERNAME", "admin")
                default_pass = os.getenv("DASHBOARD_PASSWORD", "AdminPass12345!")
                if len(default_pass) < 12:
                    default_pass = "AdminPass12345!"

                new_admin = User(
                    username=default_user,
                    password_hash=hash_password(default_pass),
                    role=UserRole.ADMIN,
                    is_active=True,
                )
                db.add(new_admin)
                db.commit()
                print("  [OK] Successfully created default administrator:")
                print(f"         Username: {default_user}")
                print(f"         Password: {default_pass}")
                print("         Role:     admin")
    except Exception as e:
        print(f"  [ERROR] Database initialization failed: {e}")
        print("  Please check that all dependencies are installed ('pip install -r requirements.txt')")
        return False

    return True


def initialize_runtime_settings(root_dir: Path):
    print("\n[7/7] Verifying runtime settings configuration...")
    settings_json = root_dir / "storage" / "config" / "runtime_settings.json"
    if settings_json.exists():
        print(f"  [OK] Runtime settings file present ({settings_json.stat().st_size} bytes).")
    else:
        default_content = '{\n  "updated_at": "initial_setup",\n  "groq_api_keys": [],\n  "gemini_api_keys": [],\n  "openrouter_api_keys": [],\n  "openai_api_keys": []\n}\n'
        settings_json.write_text(default_content, encoding="utf-8")
        print("  [OK] Initialized default 'storage/config/runtime_settings.json'.")


def print_completion_summary():
    summary = """
==============================================================================
                *** SETUP & VERIFICATION COMPLETED ***
==============================================================================
Your YouTube Shorts Automation environment is ready!

Next Steps to Run the Platform:
------------------------------------------------------------------------------
1. Launch the Operator Dashboard:
     python run.py dashboard
   Then open your browser at:
     http://127.0.0.1:8000/
   Default Login:
     Username: admin
     Password: AdminPass12345!

2. Start the Background Rendering Worker (in a separate terminal):
     python run.py worker

3. Run Automated Healthcheck & Test Suite:
     python -m pytest tests -q

4. Ingest & Process a Test Video via CLI:
     python run.py process --video "https://www.youtube.com/watch?v=VIDEO_ID" --dry-run

For complete documentation, see: SETUP_GUIDE.md
==============================================================================
"""
    print(summary)


def main():
    print_banner()
    root_dir = Path(__file__).resolve().parent

    check_python_version()
    check_ffmpeg()
    setup_directories(root_dir)
    bootstrap_env_file(root_dir)
    deps_ok = check_python_dependencies()

    if deps_ok:
        db_ok = initialize_database_and_admin(root_dir)
        if db_ok:
            initialize_runtime_settings(root_dir)

    print_completion_summary()


if __name__ == "__main__":
    main()
