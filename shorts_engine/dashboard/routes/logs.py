"""
Live Log Viewer Route: view the recent lines of the persistent rotating log file
(storage/logs/shorts_engine.log) directly from the dashboard with Windows-safe SSE streaming.
"""

import os
import json
import asyncio
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from shorts_engine.config.settings import settings
from shorts_engine.dashboard.auth import require_viewer, get_csrf_token
from shorts_engine.db.models import User

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
LOG_FILE_PATH = settings.STORAGE_DIR / "logs" / "shorts_engine.log"


def read_recent_logs(max_lines: int = 200) -> str:
    """Reads the last max_lines from the rotating log file."""
    if not LOG_FILE_PATH.exists():
        return "No log file found at storage/logs/shorts_engine.log yet. Activity will appear here once actions are triggered."

    try:
        with open(LOG_FILE_PATH, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            return "".join(lines[-max_lines:])
    except Exception as e:
        return f"Error reading log file: {e}"


@router.get("/logs", response_class=HTMLResponse, summary="Live log viewer")
def view_logs(
    request: Request,
    current_user: User = Depends(require_viewer),
):
    log_content = read_recent_logs(250)
    return templates.TemplateResponse(
        request=request,
        name="logs.html",
        context={
            "logs": log_content,
            "current_user": current_user,
            "csrf_token": get_csrf_token(request),
            "log_path": str(LOG_FILE_PATH),
        },
    )


@router.get("/api/logs", summary="Recent logs JSON endpoint")
def api_logs(
    max_lines: int = 100,
    current_user: User = Depends(require_viewer),
):
    return {"logs": read_recent_logs(max_lines)}


@router.get("/api/events/logs", summary="Real-time Server-Sent Events stream for live engine logs")
async def logs_events(
    request: Request,
    max_ticks: Optional[int] = None,
    current_user: User = Depends(require_viewer),
):
    """
    Push-based log streaming over SSE.
    Uses discrete read cycles (open, read, close immediately) to prevent Windows WinError 32
    file-locking conflicts when RotatingFileHandler rolls log files over.
    """
    async def log_generator():
        last_pos = 0
        ticks = 0
        if LOG_FILE_PATH.exists():
            try:
                sz = os.path.getsize(LOG_FILE_PATH)
                last_pos = max(0, sz - 64 * 1024)
            except Exception:
                last_pos = 0

        while True:
            if await request.is_disconnected():
                break

            ticks += 1

            if not LOG_FILE_PATH.exists():
                if max_ticks and ticks >= max_ticks:
                    break
                await asyncio.sleep(1.0)
                continue

            try:
                current_size = os.path.getsize(LOG_FILE_PATH)
                # Detect log rotation or file truncation
                if current_size < last_pos:
                    last_pos = 0

                if current_size > last_pos:
                    # Discrete open-read-close cycle: NEVER hold file handle during sleep
                    with open(LOG_FILE_PATH, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(last_pos)
                        new_content = f.read()
                        last_pos = f.tell()

                    if new_content:
                        payload = json.dumps({"text": new_content, "pos": last_pos})
                        yield f"data: {payload}\n\n"
            except Exception:
                # In case of temporary OS file lock during rotation, pass and retry on next tick
                pass

            if max_ticks and ticks >= max_ticks:
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(
        log_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
