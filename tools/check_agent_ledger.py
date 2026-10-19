"""tools/check_agent_ledger.py — CI checker for the ledger.

Validates:
  1. The ledger exists and parses.
  2. Every row has the required keys.
  3. Every status is in the allowed vocabulary.
  4. Every F-ID has a row, and titles match the audit report (R2).
  5. Generated files (FINDINGS, ENHANCEMENTS, TRACEABILITY, STATE,
     FINAL_REPORT, FILE_MANIFEST) match what gen_agent_docs.py would
     write.
  6. There are no TODO cells, no empty cells in required places.
  7. No placeholder values (e.g. '<this>') anywhere in a row.
  8. Every cited commit id is a well-formed oid that exists in this repo.
  9. `round4` rows must carry evidence/tests/files/commits, not just a status.

Run in CI:
    python tools/check_agent_ledger.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Set

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
LEDGER = REPO_ROOT / "docs" / "agent" / "ledger.yaml"

ALLOWED_STATUSES: Set[str] = {
    "CONFIRMED",
    "PARTIAL",
    "FALSE-POSITIVE",
    "ALREADY-FIXED",
    "IN-PROGRESS",
    "DONE-VERIFIED",
    "DONE-UNVERIFIED",
    "BLOCKED",
    "DEFERRED",
    "WONT-FIX",
}

# Per-type required keys.
REQUIRED_KEYS_BY_TYPE: Dict[str, Set[str]] = {
    "finding": {"id", "type", "title", "status"},
    "enhancement": {"id", "type", "title", "status"},
    "repair": {"id", "type", "title", "status"},
    "ease_of_use": {"id", "type", "title", "status"},
    "engine_gap": {"id", "type", "title", "status"},
    "batch_op": {"id", "type", "title", "status"},
    "sabotage": {"id", "type", "title", "status"},
    "e2e": {"id", "type", "title", "status"},
    "scale": {"id", "type", "title", "status"},
    # quality_attr uses before/after; status is implicit.
    "quality_attr": {"id", "type", "title", "before", "after"},
    # R4 wave rows carry the evidence that justifies their status. Without
    # these a row is an unverifiable assertion (audit finding C27).
    "round4": {
        "id", "type", "title", "status",
        "evidence", "tests", "files", "commits",
    },
}

# Values that mean "someone meant to fill this in later". Never acceptable in a
# committed ledger row — they are how unverified work gets reported as done.
PLACEHOLDER_VALUES = {
    "<this>", "<this commit>", "<pending>", "<tbd>", "<todo>",
    "tbd", "todo", "fixme", "xxx", "???", "n/a", "-",
}

# A git object id, abbreviated (7) or full (40).
HEX_RE = re.compile(r"^[0-9a-f]{7,40}$")

# Audit report titles (R2) — must match verbatim.
AUDIT_TITLES: Dict[str, str] = {
    "F-01": "Single-process orchestrator, no checkpoints",
    "F-02": "Local filesystem is the system of record",
    "F-03": "Retry in front of irreversible publish, no idempotency",
    "F-04": "OAuth refresh tokens stored in the DB",
    "F-05": "Untrusted intake run unsandboxed",
    "F-06": "No idempotency at the ingestion boundary",
    "F-07": "GPU/CPU contention, no scheduling",
    "F-08": "No tracing or metrics",
    "F-09": "Volatile in-process event bus",
    "F-10": "LLM clip selection non-deterministic and unmeasured",
    "F-11": "Quality gate is a human bottleneck",
    "F-12": "Burnt-in-only captions",
    "F-13": "A/B title mutations on live posts",
    "F-14": "No backup and recovery discipline",
    "F-15": "Operator console lacks triage primitives",
    "F-16": "No test strategy for non-deterministic stages",
    "F-17": "Rights and provenance unmodelled",
    "F-18": "Render output not verified or atomically published",
    "F-19": "Configuration not schema-validated, no feature flags",
    "F-20": "Per-job cost invisible",
    "F-21": "Partial platform failures unmodelled",
    "F-22": "Audio mastering optional and unverified",
    "F-23": "Reframing confidence not surfaced",
    "F-24": "Single-node deploy, no probes",
}

DOCS_TO_CHECK = [
    "FINDINGS.md",
    "ENHANCEMENTS.md",
    "TRACEABILITY.md",
    "STATE.md",
    "FINAL_REPORT.md",
    "FILE_MANIFEST.md",
]


def load_ledger() -> List[Dict[str, Any]]:
    if not LEDGER.exists():
        sys.exit(f"FAIL: missing ledger at {LEDGER}")
    docs = list(yaml.safe_load_all(LEDGER.read_text(encoding="utf-8")))
    rows: List[Dict[str, Any]] = []
    for d in docs:
        if d is None:
            continue
        if isinstance(d, list):
            for item in d:
                if isinstance(item, dict):
                    if "_meta" in item or "id" not in item:
                        continue
                    rows.append(item)
        elif isinstance(d, dict):
            if "_meta" in d or "id" not in d:
                continue
            rows.append(d)
    return rows


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def warn(msg: str) -> None:
    print(f"WARN: {msg}")


def check_required_keys(rows: List[Dict[str, Any]]) -> None:
    for r in rows:
        rtype = r.get("type")
        required = REQUIRED_KEYS_BY_TYPE.get(rtype, {"id", "type", "title", "status"})
        missing = required - set(r.keys())
        if missing:
            fail(f"row {r.get('id', '?')!r} (type={rtype}) missing keys: {missing}")


def check_statuses(rows: List[Dict[str, Any]]) -> None:
    bad = []
    for r in rows:
        rtype = r.get("type")
        if rtype == "quality_attr":
            # quality_attr rows have before/after, not a single status.
            continue
        s = r.get("status")
        if s not in ALLOWED_STATUSES:
            bad.append((r.get("id"), s))
    if bad:
        for rid, s in bad:
            print(f"  bad status: {rid} = {s!r}")
        fail(f"{len(bad)} rows have disallowed statuses")


def check_audit_titles(rows: List[Dict[str, Any]]) -> None:
    by_id = {r["id"]: r for r in rows}
    for fid, expected in AUDIT_TITLES.items():
        if fid not in by_id:
            fail(f"audit title missing for {fid}")
        got = by_id[fid].get("title", "")
        if got != expected:
            fail(
                f"audit title mismatch for {fid}: expected "
                f"{expected!r}, got {got!r}"
            )


def check_no_todo(rows: List[Dict[str, Any]]) -> None:
    for r in rows:
        for k, v in r.items():
            if isinstance(v, str) and "TODO" in v:
                fail(f"row {r['id']} has TODO in field {k!r}: {v!r}")


def _cells(r: Dict[str, Any]):
    """Yield (field, scalar) pairs, descending into list/tuple fields."""
    for k, v in r.items():
        if isinstance(v, str):
            yield k, v
        elif isinstance(v, (list, tuple)):
            for item in v:
                if isinstance(item, str):
                    yield k, item


def check_required_values(rows: List[Dict[str, Any]]) -> None:
    """Required keys must be present AND carry a value, not an empty one.

    An empty `notes` cell is fine (it is optional), but a required `evidence`
    or `commits` that is empty gives a row nothing to back its status with.
    """
    for r in rows:
        rtype = r.get("type")
        required = REQUIRED_KEYS_BY_TYPE.get(rtype, {"id", "type", "title", "status"})
        for key in sorted(required):
            v = r.get(key)
            if v is None:
                continue  # presence is covered by check_required_keys
            if isinstance(v, str) and not v.strip():
                fail(f"row {r.get('id', '?')!r} (type={rtype}) has empty required field {key!r}")
            elif isinstance(v, (list, tuple, dict)) and len(v) == 0:
                fail(f"row {r.get('id', '?')!r} (type={rtype}) has empty required field {key!r}")


def check_placeholders(rows: List[Dict[str, Any]]) -> None:
    """Reject unfilled placeholder values such as '<this>'."""
    for r in rows:
        for k, v in _cells(r):
            if v.strip().lower() in PLACEHOLDER_VALUES:
                fail(
                    f"row {r.get('id', '?')!r} has placeholder in field {k!r}: {v!r} "
                    "(replace with real evidence, or record the true status)"
                )


def _is_shallow_clone() -> bool:
    """True when the checkout has truncated history (actions/checkout default)."""
    if (REPO_ROOT / ".git" / "shallow").exists():
        return True
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        ).stdout.strip()
    except OSError:
        return False
    return out == "true"


def check_commit_ids(rows: List[Dict[str, Any]]) -> None:
    """Every cited commit id must look like a git oid AND exist in this repo.

    Requires full history. Under a shallow checkout every ancestor oid is
    missing, which used to be reported as "commit ... does not exist in this
    repository (never invent commit ids)" - a false accusation of
    fabrication that cost several CI rounds to diagnose. Detect it up front
    and say what to do instead.
    """
    if _is_shallow_clone():
        fail(
            "this is a shallow clone, so cited commit ids cannot be verified. "
            "Re-run with full history, e.g. "
            "`git fetch --unshallow`, or for GitHub Actions "
            "`uses: actions/checkout@v4` with `with: fetch-depth: 0`. "
            "The missing ids below are a truncated-history artefact, not "
            "invented commit ids."
        )
    cited: Dict[str, str] = {}
    for r in rows:
        commits = r.get("commits")
        if commits is None:
            continue
        if isinstance(commits, str):
            commits = [commits]
        for c in commits:
            if not isinstance(c, str) or not HEX_RE.match(c.strip().lower()):
                fail(
                    f"row {r.get('id', '?')!r} cites a non-oid commit value: {c!r}"
                )
                continue
            cited.setdefault(c.strip().lower(), r.get("id", "?"))
    if not cited:
        return
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
    except OSError:
        warn("cannot run git; skipping commit-existence verification")
        return
    if probe.returncode != 0:
        warn("not inside a git work tree; skipping commit-existence verification")
        return
    payload = "".join(f"{c}\n" for c in cited)
    res = subprocess.run(
        ["git", "cat-file", "--batch-check"],
        cwd=REPO_ROOT, input=payload, capture_output=True, text=True,
    )
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        oid, kind = parts[0], parts[1]
        if kind == "missing":
            fail(
                f"row {cited.get(oid, '?')!r} cites commit {oid!r} which does not "
                "exist in this repository (never invent commit ids)"
            )


def check_dup_ids(rows: List[Dict[str, Any]]) -> None:
    seen: Counter = Counter()
    for r in rows:
        seen[r["id"]] += 1
    for rid, n in seen.items():
        if n > 1:
            fail(f"duplicate ledger id: {rid} ({n} rows)")


def check_generated_files(rows: List[Dict[str, Any]]) -> None:
    """Regenerate into a temp file and diff against the committed file."""
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import gen_agent_docs  # type: ignore

    expected = {
        "FINDINGS.md": gen_agent_docs.render_findings(rows),
        "ENHANCEMENTS.md": gen_agent_docs.render_enhancements(rows),
        "TRACEABILITY.md": gen_agent_docs.render_traceability(rows),
        "STATE.md": gen_agent_docs.render_state(rows),
        "FINAL_REPORT.md": gen_agent_docs.render_final_report(rows),
        # FILE_MANIFEST is generated too; it was silently drifting because it
        # was never checked (see the 2330-vs-2345 file-count drift).
        "FILE_MANIFEST.md": gen_agent_docs.render_file_manifest(),
    }
    for name, want in expected.items():
        path = REPO_ROOT / "docs" / "agent" / name
        if not path.exists():
            warn(f"{path} does not exist; will be created by gen")
            continue
        got = path.read_text(encoding="utf-8")
        if got != want:
            fail(f"{path} is out of date with the ledger. Re-run gen_agent_docs.py")


def main() -> int:
    print("Loading ledger ...")
    rows = load_ledger()
    print(f"  {len(rows)} rows")
    check_required_keys(rows)
    check_statuses(rows)
    check_audit_titles(rows)
    check_no_todo(rows)
    check_required_values(rows)
    check_placeholders(rows)
    check_commit_ids(rows)
    check_dup_ids(rows)
    check_generated_files(rows)
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
