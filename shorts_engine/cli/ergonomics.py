"""E-023 — CLI ergonomics helpers (--json, --yes, --dry-run, --filter, exit codes).

These utilities wrap subprocess invocations of the existing typer-based `cli.py`
so the main commands gain ergonomic flags without invasive changes. They are
also importable by tests for direct assertion of output formats and exit codes.
"""
from __future__ import annotations

import json
import sys
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Dict, Any


# Exit codes
EXIT_OK = 0
EXIT_GENERAL = 1
EXIT_USAGE = 2
EXIT_IO = 3
EXIT_PERMISSION = 4
EXIT_NOT_FOUND = 5
EXIT_VALIDATION = 6
EXIT_TIMEOUT = 7
EXIT_DEPENDENCY = 8


@dataclass
class CliResult:
    returncode: int
    stdout: str
    stderr: str
    parsed_json: Optional[Any] = None  # populated when --json succeeds
    dry_run: bool = False


def run_cli(
    cmd: List[str],
    *,
    json_output: bool = False,
    yes: bool = False,
    dry_run: bool = False,
    filter_expr: Optional[str] = None,
    timeout: int = 60,
) -> CliResult:
    """Run the `cli.py` subprocess with ergonomic flags.

    Flags are translated:
      --json     -> passes through; we attempt to parse stdout as JSON.
      --yes      -> passes through (non-interactive).
      --dry-run  -> passes through; we mark the result.
      --filter   -> passes through as --filter <expr>.
    """
    full = list(cmd)
    if json_output:
        full.append("--json")
    if yes:
        full.append("--yes")
    if dry_run:
        full.append("--dry-run")
    if filter_expr:
        full += ["--filter", filter_expr]
    try:
        proc = subprocess.run(
            full, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as e:
        return CliResult(
            returncode=EXIT_TIMEOUT,
            stdout=e.stdout or "",
            stderr=(e.stderr or "") + f"\ntimeout after {timeout}s",
            dry_run=dry_run,
        )
    parsed: Optional[Any] = None
    if json_output and proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            # Output wasn't JSON; leave parsed as None.
            pass
    return CliResult(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        parsed_json=parsed,
        dry_run=dry_run,
    )


def shell_completions(shell: str = "bash") -> str:
    """Returns a basic shell completion script for the typer app.

    Real typer apps use `typer.main.get_completion_script`; we return a
    well-known list of top-level commands so the dashboard operator can
    wire up completion by hand.
    """
    commands = [
        "init-db", "rotate-secrets", "backup-db", "list-backups", "restore-db",
        "process", "resume", "reconcile-stale", "status", "watch",
        "dashboard", "subscribe", "create-user", "version",
    ]
    if shell not in ("bash", "zsh", "sh"):
        return f"# unsupported shell: {shell}\n"
    lines = [f"# completion for {shell}"]
    for c in commands:
        lines.append(f"complete -W '{c}' cli.py 2>/dev/null || true")
    return "\n".join(lines) + "\n"


def get_version() -> str:
    """Returns a CLI version string."""
    from importlib.metadata import version as _v
    try:
        return _v("shorts-engine")
    except Exception:  # pragma: no cover
        return "0.0.0+local"


def get_status_json() -> Dict[str, Any]:
    """Returns a JSON-serializable status dict for `cli.py status --json`."""
    return {
        "version": get_version(),
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }


def parse_filter(expr: str) -> Dict[str, str]:
    """Parse a 'key=value,key2=value2' filter expression into a dict.

    Empty/None returns an empty dict. Invalid expressions raise ValueError.
    """
    if not expr:
        return {}
    out: Dict[str, str] = {}
    for part in expr.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"filter token must be key=value, got {part!r}")
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def matches(record: Dict[str, Any], filter_dict: Dict[str, str]) -> bool:
    """Return True iff record satisfies all key=value pairs in filter_dict."""
    for k, v in filter_dict.items():
        if str(record.get(k)) != v:
            return False
    return True
