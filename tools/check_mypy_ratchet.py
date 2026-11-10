"""tools/check_mypy_ratchet.py — fail if mypy error count rises.

The baseline lives in ``.mypy_baseline.txt`` at the repo
root. New errors are not allowed; existing ones may be
removed by editing the baseline downward.

The count depends on the installed third-party stubs, not just on mypy, so
the baseline is only meaningful for the environment the project declares.
Measure it in a clean virtualenv, never in a hand-tuned one::

    python -m venv /tmp/m && /tmp/m/bin/pip install -e ".[dev]"
    /tmp/m/bin/python tools/check_mypy_ratchet.py

A stale baseline recorded against extra locally installed stubs (for
example ``types-Pillow``) reads too low and fails CI without a regression.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE = REPO_ROOT / ".mypy_baseline.txt"
PYTHON_MYPY = Path(sys.executable).parent / "mypy"


def mypy_binary() -> str:
    """The mypy belonging to the interpreter running this check.

    Resolving mypy from a sibling of ``sys.executable`` keeps the measurement
    in the same environment as the caller. Preferring a repo ``.venv`` would
    measure a different, possibly differently-stubbed environment than the
    one that invoked the gate.
    """
    return str(PYTHON_MYPY) if PYTHON_MYPY.exists() else "mypy"


def count_mypy_errors() -> int:
    mypy_bin = mypy_binary()
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
    version = subprocess.run(
        [mypy_binary(), "--version"], capture_output=True, text=True
    ).stdout.strip()
    print(f"measured with: {version} ({mypy_binary()})")
    n = count_mypy_errors()
    if n > baseline:
        print(f"FAIL: mypy errors {n} > baseline {baseline}")
        return 1
    print(f"OK: mypy errors {n} <= baseline {baseline}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
