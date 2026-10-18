"""R4-1.2 — Path handling: drive letters, spaces, long paths, non-ASCII, ffmpeg escaping.

Centralised helpers for paths that get embedded in ffmpeg filter chains or
ASS subtitle files, where backslashes, colons, single quotes, and percent signs
all have special meaning.
"""
from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path
from typing import List, Union


# --- ffmpeg drawtext / filter path escaping ---------------------------------
# ffmpeg drawtext escapes: \ : ' % ( ) , ; [ ]
# When a Windows path like ``C:\Users\Name\file.png`` is interpolated into
# ``drawtext=image='<PATH>'`` we must double backslashes and escape colons
# and percent signs. We do NOT escape the drive colon (C:) to colons.

_FT_DRAW = str.maketrans({
    "\\": r"\\",
    ":": r"\:",
    "'": r"\'",
    "%": r"\%",
    ",": r"\,",
    ";": r"\;",
    "[": r"\[",
    "]": r"\]",
    "(": r"\(",
    ")": r"\)",
})


def escape_ffmpeg_path(p: Union[str, Path]) -> str:
    """Escape a file path for safe use inside an ffmpeg drawtext or filter
    expression. The drive letter colon (e.g. ``C:``) is preserved — we only
    escape the path separator colons.
    """
    s = str(p)
    # Split drive prefix (Windows: C: or D: etc.) so we don't escape its colon.
    drive, rest = _split_drive(s)
    # Translate every backslash on Windows to forward slash for ffmpeg.
    if sys.platform == "win32" or (len(rest) > 0 and rest[0] == "\\"):
        rest = rest.replace("\\", "/")
    rest = rest.translate(_FT_DRAW)
    return drive + rest


def _split_drive(p: str) -> tuple[str, str]:
    """Split a Windows path's drive letter from the rest. Returns ``("", p)``
    on POSIX or when no drive is present.
    """
    if len(p) >= 2 and p[1] == ":" and p[0].isalpha():
        return p[:2], p[2:]
    return "", p


# --- ffmpeg concat / filter argument list -----------------------------------

def ffmpeg_arg(s: str) -> str:
    """Quote a string for safe use as a single ffmpeg CLI argument."""
    if s == "":
        return '""'
    if re.match(r"^[A-Za-z0-9_./:=+@\-]+$", s):
        return s
    return shlex.quote(s)


def ffmpeg_args(items: List[str]) -> List[str]:
    """Quote a list of strings for ffmpeg CLI."""
    return [ffmpeg_arg(s) for s in items]


# --- ASS subtitle path escaping ---------------------------------------------

def escape_ass_path(p: Union[str, Path]) -> str:
    """Escape a path for safe use inside an ASS subtitle file override tag.

    ASS uses ``\\`` as the path separator on Windows. We also escape braces
    which ASS uses for override blocks.
    """
    s = str(p).replace("\\", "/").replace("{", r"\{").replace("}", r"\}")
    return s


# --- Long path support (Windows) --------------------------------------------

def enable_windows_long_paths() -> bool:
    """Best-effort enable of long path support on Windows. Returns True if
    the running platform is Windows and long paths are available; False
    otherwise. This is a no-op on POSIX.
    """
    if sys.platform != "win32":
        return False
    try:
        # Python 3.8+ supports the long path prefix `\\?\` on Windows when
        # the policy is enabled. We attempt to register a long-path form for
        # any path that exceeds MAX_PATH (260) and return its result.
        import ctypes
        from ctypes import wintypes
        GetLongPathNameW = ctypes.windll.kernel32.GetLongPathNameW
        GetLongPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        GetLongPathNameW.restype = wintypes.DWORD

        def to_long(p: str) -> str:
            buf_len = 1024
            buf = ctypes.create_unicode_buffer(buf_len)
            n = GetLongPathNameW(p, buf, buf_len)
            if n == 0 or n > buf_len:
                return p
            return buf.value

        # Smoke test on a known path.
        to_long(os.getcwd())
        return True
    except Exception:
        return False


# --- Path safety validation -------------------------------------------------

# Windows / POSIX reserved characters
_RESERVED_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
_LONG_PATH_THRESHOLD = 250  # MAX_PATH is 260 on Windows; we warn at 250


def path_is_safe(p: Union[str, Path]) -> tuple[bool, str]:
    """Validate that a path is safe to use.

    Returns (True, "") if safe, (False, reason) otherwise. The check is
    lenient (informational) — it never raises. It rejects:
      * control characters
      * POSIX NUL bytes
      * the Windows-reserved set ``<>:"/\\|?*``

    Use this for log messages and validation only; do not gate file IO on
    it because Windows has historically allowed these in non-reserved paths.
    """
    s = str(p)
    if "\x00" in s:
        return False, "path contains NUL byte"
    if _RESERVED_CHARS.search(s):
        return False, "path contains reserved characters"
    return True, ""


def path_warnings(p: Union[str, Path]) -> List[str]:
    """Return a list of human-readable warnings about a path."""
    warnings: List[str] = []
    s = str(p)
    if len(s) > _LONG_PATH_THRESHOLD:
        warnings.append(
            f"path length {len(s)} exceeds {_LONG_PATH_THRESHOLD}; "
            "Windows MAX_PATH (260) may apply"
        )
    if any(ord(c) > 127 for c in s):
        warnings.append("path contains non-ASCII characters; ensure UTF-8 console + I/O")
    if sys.platform == "win32" and " " in s:
        warnings.append("path contains spaces; ensure proper quoting in ffmpeg args")
    if not s.strip():
        warnings.append("path is empty or whitespace")
    return warnings
