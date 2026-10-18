"""R4-6.4 — Build a Windows distribution artifact.

Produces a self-contained zip with:
  - The application code
  - A PowerShell installer (setup.ps1)
  - A start-menu launcher (setup.bat)
  - A README with the manual install steps

The artifact is verified on a clean Windows VM by:
  1. Unzip into D:\\YT-Shorts-Automation\\
  2. Double-click setup.bat
  3. Confirm the dashboard is reachable at http://localhost:8000

The build runs on any platform; the verification only works on Windows.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _copy_tree(src: Path, dst: Path, exclude: set[str]) -> None:
    """Copy ``src`` into ``dst``, skipping files in ``exclude``."""
    if not src.exists():
        return
    for root, dirs, files in os.walk(src):
        # Prune excluded dirs in-place.
        dirs[:] = [d for d in dirs if d not in exclude]
        for f in files:
            if f in exclude:
                continue
            sp = Path(root) / f
            dp = dst / sp.relative_to(src)
            dp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sp, dp)


def build(output_dir: Path, version: str) -> Path:
    """Produce a zip at ``output_dir/YT-Shorts-Automation-{version}.zip``."""
    stage = output_dir / f"YT-Shorts-Automation-{version}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)

    # Files to include.
    _copy_tree(REPO_ROOT / "shorts_engine", stage / "shorts_engine",
               exclude={"__pycache__", ".pytest_cache"})
    _copy_tree(REPO_ROOT / "frontend", stage / "frontend",
               exclude={"node_modules", "dist", ".vite"})
    for top in ("cli.py", "pyproject.toml", "setup.ps1", "setup.bat",
                "README.md", "LICENSE"):
        sp = REPO_ROOT / top
        if sp.exists():
            shutil.copy2(sp, stage / top)
    # Include the docs that the operator will need on first run.
    (stage / "docs").mkdir(exist_ok=True)
    for doc in ("WINDOWS_ACCEPTANCE.md",):
        sp = REPO_ROOT / "docs" / doc
        if sp.exists():
            shutil.copy2(sp, stage / "docs" / doc)

    # Install manifest.
    (stage / "INSTALL.txt").write_text(
        "YT-Shorts Automation — Windows distribution\n"
        f"Version: {version}\n\n"
        "INSTALL\n"
        "-------\n"
        "1. Unzip this archive anywhere on your system.\n"
        "2. Double-click setup.bat to install.\n"
        "3. When setup finishes, the dashboard shortcut is in the Start menu.\n\n"
        "VERIFY\n"
        "------\n"
        "Follow the steps in docs\\WINDOWS_ACCEPTANCE.md to confirm every\n"
        "R4-1.x row is DONE-VERIFIED.\n",
        encoding="utf-8",
    )

    # Zip it up.
    zpath = output_dir / f"YT-Shorts-Automation-{version}.zip"
    if zpath.exists():
        zpath.unlink()
    with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(stage):
            for f in files:
                fp = Path(root) / f
                zf.write(fp, fp.relative_to(stage.parent))
    # Cleanup the staging dir.
    shutil.rmtree(stage)
    return zpath


def verify(path: Path) -> int:
    """Owner-only verification step on a clean Windows VM.

    Prints a copy-pasteable PowerShell block the owner can run. Does
    NOT touch any state on the build machine.
    """
    if sys.platform != "win32":
        print(f"VERIFY is Windows-only; produced artifact: {path}")
        print("Run these steps on a clean Windows VM:")
        print("  Expand-Archive YT-Shorts-Automation-VERSION.zip -DestinationPath C:\\YT-Shorts-Automation\\")
        print("  cd C:\\YT-Shorts-Automation")
        print("  .\\setup.bat")
        print("  (Invoke-WebRequest http://localhost:8000/api/health).Content")
        return 0
    # On Windows, do nothing — the owner verifies manually.
    print(f"Artifact: {path}")
    print("Run the steps in docs/WINDOWS_ACCEPTANCE.md on a clean Windows VM.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="dist", help="output directory")
    p.add_argument("--version", default="1.4.0", help="version string")
    p.add_argument("--verify", action="store_true", help="emit verification steps")
    args = p.parse_args()

    output_dir = (REPO_ROOT / args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    zpath = build(output_dir, args.version)
    print(f"Built: {zpath}")
    if args.verify:
        return verify(zpath)
    return 0


if __name__ == "__main__":
    sys.exit(main())
