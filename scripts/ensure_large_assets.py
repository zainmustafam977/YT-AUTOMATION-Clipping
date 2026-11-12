"""Ensure large (Git LFS) assets are present in the working tree.

Usage:
    python scripts/ensure_large_assets.py

Exits non-zero if a required large asset is missing, printing the
`git lfs pull` remediation command.
"""
from __future__ import annotations

import pathlib
import sys

REQUIRED = [
    "assets/fonts/seguiemj.ttf",
]


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    missing = []
    for rel in REQUIRED:
        p = root / rel
        if not p.exists() or p.stat().st_size < 1_000_000:
            missing.append(rel)
    if missing:
        print("Missing large assets:", ", ".join(missing))
        print("Run: git lfs install && git lfs pull")
        return 1
    print("Large assets present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())