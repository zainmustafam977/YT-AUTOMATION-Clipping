"""R4-8.4 — Feature-flag expiry gate.

The CI calls this tool on every push. It exits 0 when no feature flag
is past its expiry, and 1 when at least one is.

Usage:
    python tools/check_feature_flag_expiry.py
    python tools/check_feature_flag_expiry.py --current 1.5.0
"""
from __future__ import annotations

import argparse
import sys

from shorts_engine.services.feature_flags import check_expiries, FLAGS


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--current", default="", help="current version (semver); default from feature_flags module")
    args = p.parse_args()

    expired = list(check_expiries(args.current) if args.current else check_expiries())
    if expired:
        print(f"FAIL: {len(expired)} feature flag(s) past expiry:")
        for f in expired:
            print(f"  - {f.name} (expires in {f.expires_in})")
        print("Update the flag, extend the expiry, or remove the flag.")
        return 1
    print(f"OK: all {len(FLAGS)} feature flag(s) are within their expiry window.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
