"""
Distribution Bundle Packaging Utility (PLAN-P0-07).
Packages project distribution archives using fnmatch allow-before-deny matching
to guarantee zero leakage of secret keys, database files, auth tokens, or cookies.
"""

import fnmatch
import zipfile
import logging
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)

# Explicit Allow-Before-Deny Pattern Registry
ALLOW_PATTERNS = [
    ".env.example",
    "*/.env.example",
]

DENY_PATTERNS = [
    # Version control & cache
    ".git*",
    "*/.git*",
    "*.pytest_cache*",
    "*__pycache__*",
    "*venv*",
    "downloads*",
    "*/downloads*",
    "*.zip",

    # Environment & secret credentials
    ".env",
    ".env.*",
    "*/.env",
    "*/.env.*",
    "*.key",
    "*.pem",

    # Database & journal state
    "storage/*.db",
    "*.db",
    "*.db-wal",
    "*.db-shm",
    "*.sqlite*",

    # API credentials, OAuth tokens, and session cookies
    "client_secret*.json",
    "*/client_secret*.json",
    "credentials*.json",
    "*/credentials*.json",
    "token*.json",
    "*/token*.json",
    "*cookie*.txt",
    "*/*cookie*.txt",
]


def should_exclude_file(path_str: Union[str, Path]) -> bool:
    """
    Evaluates whether a relative file path should be excluded from the bundle.
    Enforces strict Allow-Before-Deny ordering:
    1. If the path matches any ALLOW_PATTERN, it is explicitly preserved.
    2. Otherwise, if it matches any DENY_PATTERN, it is excluded.
    3. If neither matches, it is included.
    """
    norm_path = Path(path_str).as_posix()
    if norm_path.startswith("./"):
        norm_path = norm_path[2:]
    filename = Path(norm_path).name

    # Step 1: Check Allow Patterns (explicit allow-before-deny)
    for pattern in ALLOW_PATTERNS:
        if fnmatch.fnmatch(norm_path, pattern) or fnmatch.fnmatch(filename, pattern):
            return False

    # Step 2: Check Deny Patterns
    for pattern in DENY_PATTERNS:
        if fnmatch.fnmatch(norm_path, pattern) or fnmatch.fnmatch(filename, pattern):
            return True

    return False


def create_distribution_bundle(root_dir: Union[str, Path], out_zip_path: Union[str, Path]) -> int:
    """
    Scans root_dir and compiles an allow-before-deny filtered distribution zip archive.
    Returns the total number of files packaged.
    """
    root = Path(root_dir).resolve()
    out_zip = Path(out_zip_path).resolve()
    out_zip.parent.mkdir(parents=True, exist_ok=True)

    packaged_count = 0
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in root.rglob("*"):
            if not p.is_file():
                continue

            rel_p = p.relative_to(root)
            rel_str = str(rel_p).replace("\\", "/")

            # Skip the output zip itself if inside root
            if p.resolve() == out_zip:
                continue

            if not should_exclude_file(rel_str):
                zf.write(p, rel_str)
                packaged_count += 1

    return packaged_count


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent
    bundle_name = "shorts_automation_m3_bundle.zip"
    target_zip = project_root / bundle_name
    print(f"Creating distribution bundle: {target_zip}...")
    count = create_distribution_bundle(project_root, target_zip)
    print(f"Successfully packaged {count} files into {bundle_name} ({target_zip.stat().st_size:,} bytes).")
