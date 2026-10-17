#!/usr/bin/env python3
"""
==============================================================================
read_chunk.py - Industrial-Grade Auditing Tool & Ledger Logger
==============================================================================
Enforces strict, auditable line-by-line reading of codebase source files.
Prints numbered lines (max 200 lines per call) and appends a cryptographic
proof record to docs/audit/_ledger.jsonl.
==============================================================================
"""

import sys
import hashlib
import json
import datetime
import subprocess
from pathlib import Path

# Enforce UTF-8 on Windows consoles to prevent encoding errors on non-ASCII characters
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

MAX_CHUNK_SIZE = 200
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LEDGER_PATH = REPO_ROOT / "docs" / "audit" / "_ledger.jsonl"


def get_git_head() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except Exception:
        return "UNKNOWN_HEAD"


def compute_file_sha256(file_path: Path) -> str:
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def main():
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <file_path> <start_line> <end_line>", file=sys.stderr)
        sys.exit(1)

    raw_path = sys.argv[1]
    try:
        start_line = int(sys.argv[2])
        end_line = int(sys.argv[3])
    except ValueError:
        print("Error: start_line and end_line must be positive integers.", file=sys.stderr)
        sys.exit(1)

    target_path = Path(raw_path)
    if not target_path.is_absolute():
        target_path = (REPO_ROOT / target_path).resolve()
    else:
        target_path = target_path.resolve()

    if not target_path.exists() or not target_path.is_file():
        print(f"Error: Target file does not exist: {target_path}", file=sys.stderr)
        sys.exit(1)

    try:
        rel_path = target_path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        rel_path = target_path.as_posix()

    # Read all lines
    try:
        with open(target_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
    except UnicodeDecodeError:
        with open(target_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()

    total_lines = len(all_lines)

    # Special handling for empty files
    if total_lines == 0:
        start_line = 0
        end_line = 0
        chunk_lines = []
    else:
        if start_line < 1:
            print(f"Error: start_line must be >= 1 (got {start_line})", file=sys.stderr)
            sys.exit(1)
        if end_line < start_line:
            print(f"Error: end_line ({end_line}) cannot be less than start_line ({start_line})", file=sys.stderr)
            sys.exit(1)
        chunk_span = end_line - start_line + 1
        if chunk_span > MAX_CHUNK_SIZE:
            print(
                f"Error: Chunk span ({chunk_span}) exceeds max allowed chunk size of {MAX_CHUNK_SIZE} lines.",
                file=sys.stderr,
            )
            sys.exit(1)

        # Slice lines (1-indexed inclusive)
        actual_end = min(end_line, total_lines)
        chunk_lines = all_lines[start_line - 1 : actual_end]

    chunk_text = "".join(chunk_lines)
    chunk_hash = compute_text_sha256(chunk_text)
    file_hash = compute_file_sha256(target_path)
    git_head = get_git_head()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Append entry to ledger
    ledger_record = {
        "file": rel_path,
        "start": start_line,
        "end": end_line,
        "total_file_lines": total_lines,
        "chunk_line_count": len(chunk_lines),
        "chunk_sha256": chunk_hash,
        "file_sha256": file_hash,
        "git_head": git_head,
        "timestamp": now_iso,
    }

    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER_PATH, "a", encoding="utf-8") as lf:
        lf.write(json.dumps(ledger_record) + "\n")

    # Output lines to console
    header = f"=== [{rel_path}] Lines {start_line}..{end_line} of {total_lines} (SHA256: {chunk_hash[:12]}...) ==="
    print(header)
    for idx, line in enumerate(chunk_lines, start=start_line):
        # Strip trailing newline for clean print
        stripped = line.rstrip("\r\n")
        print(f"{idx:5d}: {stripped}")
    print(f"=== End Chunk ({len(chunk_lines)} lines recorded to {LEDGER_PATH.name}) ===\n")


if __name__ == "__main__":
    main()
