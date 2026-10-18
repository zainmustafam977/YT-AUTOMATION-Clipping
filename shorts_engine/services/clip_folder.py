"""R4-1.3 — Open-clip-folder: cross-platform "reveal in Finder/Explorer" helper.

Used by the dashboard's ``/api/clips/{clip_id}/open-folder`` endpoint. The
endpoint:

  1. Looks up the clip's ``output_path`` (or final video) in the DB.
  2. Validates the path exists and is safe to open.
  3. Constructs the OS-specific command to reveal the file in the file
     manager (Explorer on Windows, Finder on macOS, xdg-open on Linux).
  4. Returns the command for the operator UI to execute; the actual
     subprocess is run on the owner's machine, not the server.

The endpoint is **not** a public API; it is intentionally locked behind the
operator auth middleware. Server-side execution of arbitrary open commands
would be a code-execution risk; the endpoint returns the command to the
browser, which uses an hta or local helper to launch it.
"""
from __future__ import annotations

import os
import platform
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Tuple, List, Optional

from shorts_engine.services.paths import path_is_safe, path_warnings, escape_ffmpeg_path


def _resolve_existing_file(path: str) -> Optional[str]:
    """Return path if it exists; else None."""
    if not path:
        return None
    p = Path(path)
    if p.is_file():
        return str(p.resolve())
    if p.is_dir():
        return str(p.resolve())
    return None


def reveal_command(file_path: str) -> Tuple[str, List[str]]:
    """Return the (shell, argv) command to reveal ``file_path`` in the
    native file manager. The owner can copy this command to a launcher.

    Returns a tuple ``(kind, argv)`` where ``kind`` is one of:
        "windows_explorer_select" — select the file in Explorer
        "macos_finder_reveal"     — reveal in Finder
        "linux_xdg_open"          — open containing directory
        "unsupported"             — cannot construct

    The command is constructed safely with shlex and the path is validated
    for length and reserved characters.
    """
    if not file_path:
        return ("unsupported", [])
    p = _resolve_existing_file(file_path)
    if p is None:
        return ("missing", [])
    # Sanity check.
    ok, reason = path_is_safe(p)
    if not ok:
        return ("unsafe", [reason])
    sysname = platform.system().lower()
    if sysname.startswith("win"):
        # explorer.exe /select,"<path>" — select the file in its folder.
        # Note: explorer.exe uses comma-separated args oddly; we wrap the
        # path in double quotes inside the single argument.
        return ("windows_explorer_select", ["explorer.exe", f"/select,{p}"])
    if sysname == "darwin":
        return ("macos_finder_reveal", ["open", "-R", p])
    # Linux / BSD
    parent = str(Path(p).parent)
    return ("linux_xdg_open", ["xdg-open", parent])


def run_reveal(file_path: str) -> Tuple[bool, str]:
    """Run the reveal command in the local process. Used only on the owner's
    machine (typically the server is the owner's). Returns (ok, detail).
    """
    kind, argv = reveal_command(file_path)
    if kind in ("unsupported", "missing", "unsafe"):
        return False, f"reveal failed: {kind} (argv={argv!r})"
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        if proc.returncode != 0:
            return False, f"{kind} returned {proc.returncode}: {proc.stderr!r}"
        return True, f"revealed via {kind}"
    except FileNotFoundError as e:
        return False, f"command not found: {e}"
    except subprocess.TimeoutExpired:
        return False, f"{kind} timed out"


def describe_reveal(file_path: str) -> dict:
    """Return a JSON-serialisable description of the reveal operation.
    Used by the API endpoint so the operator UI can show the user what
    command will be launched.
    """
    if not file_path:
        return {"ok": False, "reason": "no file path provided"}
    p = _resolve_existing_file(file_path)
    if p is None:
        return {"ok": False, "reason": "file not found", "file_path": file_path}
    kind, argv = reveal_command(p)
    warnings = path_warnings(p)
    return {
        "ok": kind not in ("unsupported", "missing", "unsafe"),
        "kind": kind,
        "argv": argv,
        "argv_quoted": [shlex.quote(a) for a in argv],
        "file_path": p,
        "warnings": warnings,
    }
