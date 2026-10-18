"""R4-8.6 — Refresh contract-test fixtures.

Owner-only. Connects to the real platform API and overwrites the
recorded fixtures under ``tests/contract/fixtures/``.

Usage:
    SHORTS_ENGINE_REFRESH_FIXTURES=1 \
    YOUTUBE_API_KEY=<real> \
        python tools/refresh_contract_fixtures.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "contract" / "fixtures"
API_VERSION = "v3"


def _stamp(name: str, payload: dict) -> None:
    payload.setdefault("api_version", API_VERSION)
    payload["recorded_at"] = time.strftime("%Y-%m-%d")
    payload["recorded_by"] = "owner-fixture-refresh"
    out = FIXTURES_DIR / f"{name}.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"  wrote {out}")


def refresh_youtube_token() -> None:
    """Stub: owner must paste a real token response here.

    This function is intentionally a no-op stub so the tool can be
    shipped without leaking credentials. The owner replaces the body
    with the real call.
    """
    raise NotImplementedError(
        "Owner must implement this with the real OAuth call; "
        "see docs/HANDOVER/runbook.md 'Refresh contract fixtures'."
    )


def refresh_channels_list() -> None:
    raise NotImplementedError("Owner must implement with real channels.list?mine=true")


def refresh_video_insert() -> None:
    raise NotImplementedError("Owner must implement with real videos.insert")


def main() -> int:
    if os.environ.get("SHORTS_ENGINE_REFRESH_FIXTURES") != "1":
        print("ERROR: set SHORTS_ENGINE_REFRESH_FIXTURES=1 to run.")
        return 2
    if not FIXTURES_DIR.exists():
        FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    print("Refreshing contract fixtures from live APIs...")
    try:
        refresh_youtube_token()
    except NotImplementedError as e:
        print(f"  youtube_token_response: SKIPPED ({e})")
    try:
        refresh_channels_list()
    except NotImplementedError as e:
        print(f"  youtube_channels_list_mine: SKIPPED ({e})")
    try:
        refresh_video_insert()
    except NotImplementedError as e:
        print(f"  youtube_video_insert_response: SKIPPED ({e})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
