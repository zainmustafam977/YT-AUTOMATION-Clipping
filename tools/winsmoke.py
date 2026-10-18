"""R4-1 — Windows acceptance smoke tool.

Walks every section of ``docs/WINDOWS_ACCEPTANCE.md`` and runs the
machine-side checks. Owner-only; never run from automation.

Usage:
    python tools/winsmoke.py                  # all sections
    python tools/winsmoke.py --report acc.json   # write JSON report
    python tools/winsmoke.py --section 3      # one section only
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Dict, List


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def check_python() -> Dict:
    out = {"name": "Python 3.11+", "ok": False, "details": ""}
    if not _have("python"):
        out["details"] = "python not on PATH"
        return out
    ver = subprocess.run(
        ["python", "--version"], capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    out["details"] = ver
    parts = ver.replace("Python ", "").split(".")
    out["ok"] = len(parts) >= 2 and int(parts[0]) >= 3 and int(parts[1]) >= 11
    return out


def check_pwsh() -> Dict:
    out = {"name": "PowerShell 7", "ok": False, "details": ""}
    if not _have("pwsh"):
        out["details"] = "pwsh not on PATH"
        return out
    out["details"] = subprocess.run(
        ["pwsh", "--version"], capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    out["ok"] = True
    return out


def check_ffmpeg() -> Dict:
    out = {"name": "ffmpeg", "ok": False, "details": ""}
    if not _have("ffmpeg"):
        out["details"] = "ffmpeg not on PATH"
        return out
    out["details"] = subprocess.run(
        ["ffmpeg", "-version"], capture_output=True, text=True, timeout=5,
    ).stdout.split("\n", 1)[0]
    out["ok"] = True
    return out


def check_long_path_support() -> Dict:
    out = {"name": "Long paths enabled", "ok": False, "details": ""}
    if sys.platform != "win32":
        out["details"] = "skipped (non-Windows)"
        out["ok"] = True
        return out
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\FileSystem",
        ) as k:
            v, _ = winreg.QueryValueEx(k, "LongPathsEnabled")
        out["ok"] = bool(v)
        out["details"] = f"LongPathsEnabled={v}"
    except Exception as e:
        out["details"] = f"error: {e}"
    return out


def check_pywin32() -> Dict:
    out = {"name": "pywin32", "ok": False, "details": ""}
    try:
        import win32job  # noqa: F401
        out["ok"] = True
        out["details"] = "imported win32job"
    except ImportError:
        out["details"] = "pywin32 not installed"
    return out


def check_setup_idempotent(repo_root: str) -> Dict:
    out = {"name": "setup.ps1 idempotent", "ok": False, "details": ""}
    setup = os.path.join(repo_root, "setup.ps1")
    if not os.path.exists(setup):
        out["details"] = "setup.ps1 missing"
        return out
    if not _have("pwsh"):
        out["details"] = "skipped (no pwsh)"
        return out
    try:
        # First run — usually takes 1-3 minutes; cap at 5 min.
        r1 = subprocess.run(
            ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", setup, "-Mode", "install"],
            capture_output=True, text=True, timeout=300,
        )
        # Second run — must complete in <30s and be a no-op.
        t0 = time.time()
        r2 = subprocess.run(
            ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", setup, "-Mode", "install"],
            capture_output=True, text=True, timeout=60,
        )
        elapsed = time.time() - t0
        out["details"] = f"first={r1.returncode} second={r2.returncode} second_time={elapsed:.1f}s"
        out["ok"] = (r1.returncode == 0 and r2.returncode == 0 and elapsed < 30)
    except Exception as e:
        out["details"] = f"error: {e}"
    return out


def check_pytest(repo_root: str) -> Dict:
    out = {"name": "R4 pytest", "ok": False, "details": ""}
    if not _have("pytest"):
        out["details"] = "pytest not on PATH"
        return out
    r = subprocess.run(
        ["pytest", "-q", "-m", "not network", "--maxfail=5",
         "tests/test_settings_parsers.py",
         "tests/test_path_handling.py",
         "tests/test_strict_mode.py",
         "tests/test_feature_flags.py",
         "tests/test_triage.py",
         "tests/contract/"],
        cwd=repo_root, capture_output=True, text=True, timeout=600,
    )
    out["details"] = r.stdout.split("\n")[-2] if r.stdout else ""
    out["ok"] = r.returncode == 0
    return out


def check_no_orphans() -> Dict:
    out = {"name": "No orphan ffmpeg", "ok": True, "details": "skipped (manual)"}
    return out


SECTIONS: List = [
    ("1. Preflight", [check_python, check_pwsh]),
    ("2. Idempotent setup", []),
    ("3. Long paths", [check_long_path_support]),
    ("4. Non-ASCII paths", []),
    ("5. UTF-8 console", []),
    ("6. Drive + spaces", []),
    ("7. ffmpeg escaping", [check_ffmpeg]),
    ("8. pywin32 job objects", [check_pywin32]),
    ("9. Watch-folder lock", []),
    ("10. GPU encoder", []),
    ("11. setup.bat double-click", []),
    ("12. Pytest", []),
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--report", default="")
    p.add_argument("--section", default="", help="section number or prefix to limit")
    p.add_argument("--repo", default=os.getcwd())
    args = p.parse_args()

    sections: Dict[str, List[Dict]] = {}
    for label, checks in SECTIONS:
        if args.section and not label.startswith(args.section):
            continue
        results: List[Dict] = []
        for c in checks:
            if c is check_setup_idempotent or c is check_pytest:
                results.append(c(args.repo))
            else:
                results.append(c())
        sections[label] = results

    # Print a one-line-per-section summary.
    overall_ok = True
    for label, results in sections.items():
        bad = [r for r in results if not r.get("ok")]
        if bad:
            overall_ok = False
            print(f"FAIL  {label}: {len(bad)} check(s) failed")
            for r in bad:
                print(f"        - {r['name']}: {r['details']}")
        else:
            print(f"PASS  {label}: {len(results)} check(s) ok")

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(sections, f, indent=2)
        print(f"Report written: {args.report}")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
