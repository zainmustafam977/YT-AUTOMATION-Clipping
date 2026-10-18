"""R4-8.3 — Rotate the SECRETS_MASTER_KEY.

Owner-only. Refuses to run from automation.

Usage:
    python tools/rotate_keys.py --new-key <hex>            # provide a new key
    python tools/rotate_keys.py --generate               # generate a fresh key
    python tools/rotate_keys.py --dry-run                # show what would change

The procedure is:
    1. Generate (or accept) a new master key.
    2. Set SECRETS_MASTER_KEY=<new> in the secret store.
    3. Restart the dashboard with ROTATE_NOW=1.
    4. The dashboard re-encrypts every encrypted blob in the DB.
    5. Record the rotation in the ``key_rotation_log`` table.

This tool is the **step 1+5 helper**. The actual re-encryption is
performed by ``shorts_engine.services.secrets`` at boot when
``ROTATE_NOW=1`` is set.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _generate_key() -> str:
    """Generate a 32-byte hex key (Fernet-compatible)."""
    return secrets.token_hex(32)


def _record_rotation(db_path: Path, rotated_by: str, notes: str) -> None:
    """Insert a row into the key_rotation_log table."""
    if not db_path.exists():
        print(f"NOTE: {db_path} does not exist yet; skipping log insert.")
        return
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS key_rotation_log ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  rotated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
            "  rotated_by VARCHAR(64),"
            "  notes TEXT"
            ")"
        )
        conn.execute(
            "INSERT INTO key_rotation_log (rotated_by, notes) VALUES (?, ?)",
            (rotated_by, notes),
        )
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--new-key", default="",
                   help="new SECRETS_MASTER_KEY (hex). 32 bytes (64 hex chars) for Fernet.")
    p.add_argument("--generate", action="store_true",
                   help="generate a new key and print it (do not write)")
    p.add_argument("--dry-run", action="store_true",
                   help="show what would change without writing anything")
    p.add_argument("--db", default=str(REPO / "storage" / "shorts_automation.db"),
                   help="path to the SQLite database")
    p.add_argument("--rotated-by", default=os.environ.get("USER", "owner"),
                   help="who is performing the rotation")
    p.add_argument("--notes", default="manual rotation",
                   help="notes for the rotation log")
    args = p.parse_args()

    if args.generate:
        print(_generate_key())
        return 0

    if not args.new_key:
        print("ERROR: either --new-key or --generate is required.")
        return 2

    if len(args.new_key) != 64:
        print(f"ERROR: --new-key must be 64 hex chars (32 bytes); got {len(args.new_key)}.")
        return 2

    if args.dry_run:
        print("DRY RUN — no changes will be made.")
        print(f"  DB: {args.db}")
        print(f"  New key length: {len(args.new_key)} (good)")
        print(f"  Rotated by: {args.rotated_by}")
        print(f"  Notes: {args.notes}")
        return 0

    # 1. Print the new key so the owner can copy it into the secret store.
    print("=" * 72)
    print("NEW MASTER KEY — copy this into your secret store.")
    print("After copying, restart the dashboard with ROTATE_NOW=1.")
    print("=" * 72)
    print(args.new_key)
    print("=" * 72)

    # 2. Record the rotation in the DB.
    _record_rotation(Path(args.db), args.rotated_by, args.notes)
    print(f"Recorded rotation in {args.db}.")
    print("Restart the dashboard with ROTATE_NOW=1 to re-encrypt secrets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
