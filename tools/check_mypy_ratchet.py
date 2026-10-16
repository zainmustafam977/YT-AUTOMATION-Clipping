"""tools/check_mypy_ratchet.py — fail if mypy error count rises.

The baseline lives in ``.mypy_baseline.txt`` at the repo
root. New errors are not allowed; existing ones may be
removed by editing the baseline downward.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE = REPO_ROOT / ".mypy_baseline.txt"
VENV_MYPY = REPO_ROOT / ".venv" / "bin" / "mypy"


def count_mypy_errors() -> int:
    mypy_bin = str(VENV_MYPY) if VENV_MYPY.exists() else "mypy"
    proc = subprocess.run(
        [mypy_bin, "--no-error-summary", "shorts_engine/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # mypy returns 0 even on errors; we count "error:" lines.
    n = 0
    for line in proc.stdout.splitlines():
        if re.search(r"\berror:", line):
            n += 1
    return n


def main() -> int:
    if not BASELINE.exists():
        sys.exit(f"missing baseline at {BASELINE}")
    try:
        baseline = int(BASELINE.read_text(encoding="utf-8").strip())
    except Exception:
        sys.exit(f"baseline is not an integer: {BASELINE}")
    n = count_mypy_errors()
    if n > baseline:
        print(f"FAIL: mypy errors {n} > baseline {baseline}")
        return 1
    print(f"OK: mypy errors {n} <= baseline {baseline}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
