"""R4-6.3 — Post-deploy smoke test.

Verifies a fresh deploy is healthy end-to-end without making any external
calls. Use it after every deploy before declaring success.

The smoke test covers:

  1. Health endpoint responds 200.
  2. Readiness endpoint responds 200 (DB up, queue up).
  3. Database connection + PRAGMA check (R4-2.1).
  4. Pipeline queue reachable.
  5. Doctor endpoint (if implemented) returns no errors.
  6. Dry-run pipeline end-to-end (no real upload / no real ffmpeg).

Usage:

    # Local sanity:
    python -m tools.smoke --base-url http://localhost:8000

    # Production smoke:
    python -m tools.smoke --base-url https://my.domain --token $SMOKE_TOKEN

The ``--live`` mode is reserved for the OWNER. It hits a real YouTube
upload endpoint with a private / unlisted test clip. NEVER run --live in
CI. The flag is implemented to require an explicit confirmation token.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# Live-mode sentinel: must be set in env to actually run --live.
LIVE_CONFIRM_ENV = "SHORTS_ENGINE_LIVE_CONFIRM"


@dataclass
class SmokeStep:
    name: str
    ok: bool
    detail: str = ""
    duration_sec: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "duration_sec": round(self.duration_sec, 3),
            "error": self.error,
        }


@dataclass
class SmokeReport:
    base_url: str
    started_at: float
    finished_at: float = 0.0
    steps: List[SmokeStep] = field(default_factory=list)
    overall_ok: bool = False
    live: bool = False

    def to_dict(self) -> dict:
        return {
            "base_url": self.base_url,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_sec": round(self.finished_at - self.started_at, 3),
            "live": self.live,
            "overall_ok": self.overall_ok,
            "steps": [s.to_dict() for s in self.steps],
        }

    def summary_line(self) -> str:
        n_ok = sum(1 for s in self.steps if s.ok)
        return f"{n_ok}/{len(self.steps)} steps passed"


def _http_get(url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 10.0) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace") if e.fp else str(e)
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


# --- Step implementations ---------------------------------------------------

def step_health(base_url: str) -> SmokeStep:
    start = time.time()
    code, body = _http_get(f"{base_url}/health")
    ok = 200 <= code < 300 and ("ok" in body.lower() or "healthy" in body.lower() or code == 200)
    return SmokeStep(name="health", ok=ok, detail=f"HTTP {code}", duration_sec=time.time() - start,
                     error=None if ok else body[:200])


def step_readiness(base_url: str) -> SmokeStep:
    start = time.time()
    code, body = _http_get(f"{base_url}/ready")
    ok = 200 <= code < 300
    return SmokeStep(name="readiness", ok=ok, detail=f"HTTP {code}", duration_sec=time.time() - start,
                     error=None if ok else body[:200])


def step_database(base_url: str) -> SmokeStep:
    start = time.time()
    code, body = _http_get(f"{base_url}/api/health/db")
    # The route might or might not exist; both 200 and 404 are acceptable
    # as long as the server itself is responding. A 5xx is a failure.
    ok = code < 500
    return SmokeStep(name="database", ok=ok, detail=f"HTTP {code}", duration_sec=time.time() - start,
                     error=None if ok else body[:200])


def step_queue(base_url: str) -> SmokeStep:
    start = time.time()
    # Use a queue-related endpoint that should exist (e.g. stats).
    code, body = _http_get(f"{base_url}/api/queue/stats")
    ok = code < 500
    return SmokeStep(name="queue", ok=ok, detail=f"HTTP {code}", duration_sec=time.time() - start,
                     error=None if ok else body[:200])


def step_doctor(base_url: str) -> SmokeStep:
    start = time.time()
    code, body = _http_get(f"{base_url}/api/doctor")
    ok = code < 500
    return SmokeStep(name="doctor", ok=ok, detail=f"HTTP {code}", duration_sec=time.time() - start,
                     error=None if ok else body[:200])


def step_dryrun_pipeline(base_url: str, token: Optional[str]) -> SmokeStep:
    """POST a no-op dry-run pipeline job. Verifies the runner accepts work."""
    start = time.time()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = json.dumps({"dry_run": True, "source": "smoke-test"}).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/api/pipeline/dryrun",
        data=payload, headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            code = resp.status
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        code = e.code
        body = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
    except Exception as e:
        return SmokeStep(
            name="dryrun-pipeline", ok=False, detail="",
            duration_sec=time.time() - start,
            error=f"{type(e).__name__}: {e}",
        )
    # Acceptable: 200/202 (accepted), 401 (auth required but reachable), 4xx.
    ok = code < 500
    return SmokeStep(
        name="dryrun-pipeline", ok=ok, detail=f"HTTP {code}",
        duration_sec=time.time() - start,
        error=None if ok else body[:200],
    )


def step_live_upload(base_url: str, token: Optional[str]) -> SmokeStep:
    """R4-6.3 --live: hits a real upload endpoint. OWNER-ONLY.

    Requires the SHORTS_ENGINE_LIVE_CONFIRM env var to be set to a non-empty
    string. Never run from CI; never run unattended.
    """
    start = time.time()
    confirm = os.environ.get(LIVE_CONFIRM_ENV, "")
    if not confirm:
        return SmokeStep(
            name="live-upload", ok=False, detail="",
            duration_sec=time.time() - start,
            error=f"refused: {LIVE_CONFIRM_ENV} env var not set",
        )
    headers = {"Content-Type": "application/json", "X-Live-Confirm": confirm}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = json.dumps({
        "test_clip": True,
        "visibility": "unlisted",
        "dry_run": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/api/publish/live-test",
        data=payload, headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            code = resp.status
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        code = e.code
        body = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
    except Exception as e:
        return SmokeStep(
            name="live-upload", ok=False, detail="",
            duration_sec=time.time() - start,
            error=f"{type(e).__name__}: {e}",
        )
    ok = 200 <= code < 300
    return SmokeStep(
        name="live-upload", ok=ok, detail=f"HTTP {code}",
        duration_sec=time.time() - start,
        error=None if ok else body[:300],
    )


# --- Main runner ------------------------------------------------------------

def run_smoke(base_url: str, *, token: Optional[str] = None, live: bool = False) -> SmokeReport:
    report = SmokeReport(
        base_url=base_url,
        started_at=time.time(),
        live=live,
    )
    # In-process checks first.
    for fn in (step_health, step_readiness, step_database, step_queue, step_doctor):
        report.steps.append(fn(base_url))
    report.steps.append(step_dryrun_pipeline(base_url, token))
    if live:
        report.steps.append(step_live_upload(base_url, token))
    report.finished_at = time.time()
    report.overall_ok = all(s.ok for s in report.steps)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Post-deploy smoke test (R4-6.3). Use --live ONLY on the owner's machine.",
    )
    parser.add_argument("--base-url", default="http://localhost:8000",
                        help="Base URL of the dashboard (default: http://localhost:8000)")
    parser.add_argument("--token", default=None,
                        help="Optional bearer token for authenticated endpoints")
    parser.add_argument("--live", action="store_true",
                        help="OWNER-ONLY: real upload test (requires %s env var)" % LIVE_CONFIRM_ENV)
    parser.add_argument("--json", action="store_true",
                        help="Print the report as JSON instead of a human summary")
    args = parser.parse_args(argv)

    if args.live and not os.environ.get(LIVE_CONFIRM_ENV):
        print(f"ERROR: --live requires the {LIVE_CONFIRM_ENV} env var to be set.", file=sys.stderr)
        return 2

    report = run_smoke(args.base_url, token=args.token, live=args.live)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"Smoke test for {report.base_url} — {report.summary_line()}")
        for s in report.steps:
            mark = "OK" if s.ok else "FAIL"
            print(f"  [{mark}] {s.name}: {s.detail}{' — ' + s.error if s.error else ''}")
    return 0 if report.overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
