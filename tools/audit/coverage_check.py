#!/usr/bin/env python3
"""
==============================================================================
coverage_check.py - Auditing Completeness Gate & Coverage Verification
==============================================================================
Enumerates every file in the repository (tracked + untracked non-ignored),
counts lines, merges ledger ranges, checks assessment cards, and verifies:
(a) Unread line ranges
(b) Files changed since they were read (SHA256 mismatch)
(c) Files or chunks with no assessment cards
(d) Files missing from the ledger entirely
Exits non-zero on any gap.
==============================================================================
"""

import sys
import os
import hashlib
import json
import fnmatch
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple, Set, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LEDGER_PATH = REPO_ROOT / "docs" / "audit" / "_ledger.jsonl"
EXEMPTIONS_PATH = REPO_ROOT / "docs" / "audit" / "_exemptions.md"
CARDS_DIR_A = REPO_ROOT / "docs" / "audit" / "cards"
CARDS_DIR_B = REPO_ROOT / "docs" / "audit" / "02_module_reviews"

# Common binary extensions automatically classified if not text
BINARY_EXTENSIONS = {
    ".onnx", ".png", ".jpg", ".jpeg", ".ico", ".gif", ".mp4", ".wav",
    ".mp3", ".ogg", ".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm",
    ".gz", ".zip", ".tar", ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin",
    ".docx", ".pdf"
}

# Audit runtime ledger files that mutate during the audit process itself
INTERNAL_AUDIT_STATE_FILES = {
    "docs/audit/_ledger.jsonl",
    "docs/audit/_state.json",
    "docs/audit/_progress.md",
}


def compute_file_sha256(file_path: Path) -> str:
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def count_file_lines(file_path: Path) -> int:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except UnicodeDecodeError:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                return sum(1 for _ in f)
        except Exception:
            return 0


def load_exemptions() -> Set[str]:
    exemptions = set()
    if not EXEMPTIONS_PATH.exists():
        return exemptions
    with open(EXEMPTIONS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Handle markdown table rows: | pattern | classification | reason |
            if line.startswith("|") and line.endswith("|"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                if cells and not cells[0].startswith("---") and cells[0].lower() != "pattern / file":
                    pat = cells[0].strip("`").strip("'").strip('"').strip()
                    if pat:
                        exemptions.add(pat.replace("\\", "/"))
                continue
            # Handle bullet list: - pattern: reason
            parts = line.lstrip("-* ").split(":")
            pat = parts[0].strip().strip("`").strip("'").strip('"')
            if pat:
                exemptions.add(pat.replace("\\", "/"))
    return exemptions


def is_exempt(rel_path: str, exemptions: Set[str]) -> bool:
    if rel_path in INTERNAL_AUDIT_STATE_FILES:
        return True
    ext = Path(rel_path).suffix.lower()
    if ext in BINARY_EXTENSIONS:
        return True
    for pat in exemptions:
        if fnmatch.fnmatch(rel_path, pat) or rel_path.startswith(pat.rstrip("/") + "/"):
            return True
    return False


def get_all_repo_files() -> List[str]:
    # 1. Tracked files
    try:
        res = subprocess.run(
            ["git", "ls-files"],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        tracked = [f.strip().replace("\\", "/") for f in res.stdout.splitlines() if f.strip()]
    except Exception as e:
        print(f"Error listing tracked git files: {e}", file=sys.stderr)
        tracked = []

    # 2. Untracked non-ignored files
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        untracked = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.startswith("?? "):
                file_rel = line[3:].strip().replace("\\", "/")
                untracked.append(file_rel)
    except Exception as e:
        print(f"Error listing untracked git files: {e}", file=sys.stderr)
        untracked = []

    all_files = sorted(list(set(tracked + untracked)))
    return all_files


def merge_ranges(ranges: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    if not ranges:
        return []
    sorted_ranges = sorted(ranges, key=lambda r: (r[0], r[1]))
    merged = [sorted_ranges[0]]
    for cur_start, cur_end in sorted_ranges[1:]:
        last_start, last_end = merged[-1]
        if cur_start <= last_end + 1:
            merged[-1] = (last_start, max(last_end, cur_end))
        else:
            merged.append((cur_start, cur_end))
    return merged


def find_unread_gaps(total_lines: int, merged_ranges: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    if total_lines == 0:
        return []
    if not merged_ranges:
        return [(1, total_lines)]

    gaps = []
    current = 1
    for start, end in merged_ranges:
        if start > current:
            gaps.append((current, start - 1))
        current = max(current, end + 1)

    if current <= total_lines:
        gaps.append((current, total_lines))

    return gaps


def check_card_exists(rel_path: str) -> bool:
    card_a = CARDS_DIR_A / f"{rel_path}.md"
    card_b = CARDS_DIR_B / f"{rel_path}.md"
    if card_a.exists() and card_a.stat().st_size > 0:
        return True
    if card_b.exists() and card_b.stat().st_size > 0:
        return True
    return False


def main():
    print("=" * 78)
    print("AUDIT COMPLETENESS GATE: Line-by-Line Tooling Coverage Verification")
    print("=" * 78)

    exemptions = load_exemptions()
    all_repo_files = get_all_repo_files()

    # Filter to non-exempt files that exist on disk
    target_files = []
    exempt_count = 0
    for f in all_repo_files:
        full_p = REPO_ROOT / f
        if not full_p.is_file():
            continue
        if is_exempt(f, exemptions):
            exempt_count += 1
            continue
        target_files.append(f)

    # Parse ledger
    ledger_records: Dict[str, List[Dict]] = {}
    if LEDGER_PATH.exists():
        with open(LEDGER_PATH, "r", encoding="utf-8") as lf:
            for line in lf:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    rf = rec.get("file", "").replace("\\", "/")
                    if rf:
                        ledger_records.setdefault(rf, []).append(rec)
                except Exception:
                    pass

    missing_from_ledger = []
    unread_ranges_by_file = {}
    hash_mismatches = []
    missing_cards = []

    total_lines_target = 0
    total_lines_read = 0

    for rel_path in target_files:
        full_p = REPO_ROOT / rel_path
        file_lines = count_file_lines(full_p)
        total_lines_target += file_lines

        if rel_path not in ledger_records:
            missing_from_ledger.append((rel_path, file_lines))
            missing_cards.append(rel_path)
            continue

        records = ledger_records[rel_path]
        raw_ranges = [(r["start"], r["end"]) for r in records]
        merged = merge_ranges(raw_ranges)

        # Calculate covered lines
        covered_lines = sum((e - s + 1) for s, e in merged)
        total_lines_read += min(covered_lines, file_lines)

        gaps = find_unread_gaps(file_lines, merged)
        if gaps:
            unread_ranges_by_file[rel_path] = (gaps, file_lines)

        # Check latest recorded file hash against current on disk
        latest_rec = records[-1]
        recorded_sha256 = latest_rec.get("file_sha256", "")
        current_sha256 = compute_file_sha256(full_p)
        if recorded_sha256 and recorded_sha256 != current_sha256:
            hash_mismatches.append((rel_path, recorded_sha256[:12], current_sha256[:12]))

        # Check assessment card
        if not check_card_exists(rel_path):
            missing_cards.append(rel_path)

    # Summary Display
    print(f"Total Repository Files Identified: {len(all_repo_files)}")
    print(f"Exempted Files (binary/media/lock): {exempt_count}")
    print(f"Audited Target Files:             {len(target_files)}")
    print(f"Total Target Code Lines:           {total_lines_target:,}")
    print(f"Total Lines Verified in Ledger:    {total_lines_read:,}")
    pct_covered = (total_lines_read / total_lines_target * 100.0) if total_lines_target > 0 else 100.0
    print(f"Line-Level Read Coverage:          {pct_covered:.2f}%\n")

    has_gaps = False

    if missing_from_ledger:
        has_gaps = True
        print(f"[!] (d) Files completely missing from ledger ({len(missing_from_ledger)}):")
        for f, l in missing_from_ledger[:25]:
            print(f"    - {f} ({l} lines)")
        if len(missing_from_ledger) > 25:
            print(f"    ... and {len(missing_from_ledger) - 25} more files.")

    if unread_ranges_by_file:
        has_gaps = True
        print(f"\n[!] (a) Files with unread line ranges ({len(unread_ranges_by_file)}):")
        for f, (gaps, total) in list(unread_ranges_by_file.items())[:25]:
            gaps_str = ", ".join(f"{s}-{e}" for s, e in gaps)
            print(f"    - {f} (total: {total} lines) -> Gaps: [{gaps_str}]")
        if len(unread_ranges_by_file) > 25:
            print(f"    ... and {len(unread_ranges_by_file) - 25} more files.")

    if hash_mismatches:
        has_gaps = True
        print(f"\n[!] (b) Files modified since read ({len(hash_mismatches)}):")
        for f, r_hash, c_hash in hash_mismatches[:25]:
            print(f"    - {f} (ledger SHA: {r_hash}... vs disk SHA: {c_hash}...)")

    if missing_cards:
        has_gaps = True
        print(f"\n[!] (c) Files missing assessment cards ({len(missing_cards)}):")
        for f in missing_cards[:25]:
            print(f"    - docs/audit/cards/{f}.md")
        if len(missing_cards) > 25:
            print(f"    ... and {len(missing_cards) - 25} more files.")

    print("\n" + "=" * 78)
    if has_gaps:
        print("[FAIL] Audit Completeness Gate FAILED: Line-level or card gaps remain.")
        print("=" * 78)
        sys.exit(1)
    else:
        print("[PASS] Audit Completeness Gate PASSED: 100% of lines read & cards verified.")
        print("=" * 78)
        sys.exit(0)


if __name__ == "__main__":
    main()
