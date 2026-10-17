"""E-011 / E-012 — Built-in overlay presets + persistence + import/export."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Dict, List, Optional, Tuple

from .spec import OverlaySpec, OverlayLayer, LayerType, TextStyle, StickerStyle, ShapeStyle, parse_spec, serialize_spec


# --- Built-in presets (ship with the engine) ---------------------------------

BUILTIN_PRESETS: List[OverlaySpec] = [
    OverlaySpec(
        name="builtin:title-card",
        description="Big title at top, subtitle below, used for intro frames.",
        layers=[
            OverlayLayer(
                layer_type=LayerType.TEXT,
                text="YOUR TITLE HERE",
                x=0.10, y=0.10,
                text_style=TextStyle(size=72, color="#FFFFFF", outline_color="#000000", outline_width=3),
                z=10,
            ),
            OverlayLayer(
                layer_type=LayerType.TEXT,
                text="subtitle goes here",
                x=0.10, y=0.20,
                text_style=TextStyle(size=40, color="#FFEB3B", outline_color="#000000", outline_width=2),
                z=10,
            ),
        ],
    ),
    OverlaySpec(
        name="builtin:lower-third",
        description="Name + handle pinned to lower-third of frame.",
        layers=[
            OverlayLayer(
                layer_type=LayerType.SHAPE,
                x=0.05, y=0.75,
                shape=ShapeStyle(shape="rect", width=900, height=140, color="#000000", opacity=0.6, border=0),
                z=5,
            ),
            OverlayLayer(
                layer_type=LayerType.TEXT,
                text="@creator",
                x=0.08, y=0.78,
                text_style=TextStyle(size=44, color="#FFFFFF", outline_color="#000000", outline_width=0),
                z=10,
            ),
        ],
    ),
    OverlaySpec(
        name="builtin:cta-endcard",
        description="End-of-video call to action with a star sticker.",
        layers=[
            OverlayLayer(
                layer_type=LayerType.TEXT,
                text="LIKE & SUBSCRIBE",
                x=0.20, y=0.45,
                text_style=TextStyle(size=80, color="#FF1744", outline_color="#FFFFFF", outline_width=4),
                z=10,
            ),
            OverlayLayer(
                layer_type=LayerType.STICKER,
                x=0.10, y=0.40,
                sticker=StickerStyle(kind="star", size=96, color="#FFD700"),
                z=11,
            ),
        ],
    ),
]


def list_builtin_presets() -> List[str]:
    return [p.name for p in BUILTIN_PRESETS]


def get_builtin_preset(name: str) -> Optional[OverlaySpec]:
    for p in BUILTIN_PRESETS:
        if p.name == name:
            return p
    return None


def preset_sha256(spec: OverlaySpec) -> str:
    """SHA-256 of the canonical serialization (used for tamper detection)."""
    return hashlib.sha256(serialize_spec(spec).encode("utf-8")).hexdigest()


def upsert_preset(
    db: sqlite3.Connection,
    spec: OverlaySpec,
    *,
    created_by: str = "system",
    is_builtin: bool = False,
) -> int:
    """Insert or update a preset. Returns the row id."""
    sha = preset_sha256(spec)
    payload = serialize_spec(spec)
    cur = db.execute(
        "SELECT id FROM overlay_preset WHERE name = ?",
        (spec.name,),
    )
    row = cur.fetchone()
    if row is not None:
        db.execute(
            "UPDATE overlay_preset SET description=?, spec_json=?, spec_sha256=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (spec.description, payload, sha, row[0]),
        )
        db.commit()
        return int(row[0])
    cur = db.execute(
        "INSERT INTO overlay_preset (name, description, spec_json, spec_sha256, created_by, is_builtin, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
        (spec.name, spec.description, payload, sha, created_by, 1 if is_builtin else 0),
    )
    db.commit()
    return int(cur.lastrowid)


def export_preset(db: sqlite3.Connection, name: str) -> dict:
    row = db.execute(
        "SELECT name, description, spec_json, spec_sha256 FROM overlay_preset WHERE name = ?",
        (name,),
    ).fetchone()
    if row is None:
        raise KeyError(f"preset {name!r} not found")
    return {
        "name": row[0],
        "description": row[1],
        "spec": json.loads(row[2]),
        "spec_sha256": row[3],
    }


def export_all(db: sqlite3.Connection) -> List[dict]:
    rows = db.execute(
        "SELECT name FROM overlay_preset ORDER BY name"
    ).fetchall()
    return [export_preset(db, r[0]) for r in rows]


def import_preset(db: sqlite3.Connection, payload: dict, *, verify_sha: bool = True) -> Tuple[int, bool]:
    """Import a preset. Returns (row_id, sha_verified). Raises ValueError on bad payload."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")
    if "spec" not in payload or "name" not in payload:
        raise ValueError("payload must have 'name' and 'spec'")
    spec = parse_spec(payload["spec"])
    provided_sha = payload.get("spec_sha256", "")
    sha_verified = False
    if provided_sha:
        # Verify SHA against the *original* spec (before any rename).
        if provided_sha != preset_sha256(spec):
            raise ValueError("spec_sha256 mismatch — preset tampered with")
        sha_verified = verify_sha
    # Honor the payload's name (allows renaming during import).
    spec.name = payload.get("name", spec.name)
    if "description" in payload:
        spec.description = payload["description"]
    # If no sha was provided, sha_verified stays False; the row's spec_sha256 will
    # be (re)computed by upsert_preset on insert.
    row_id = upsert_preset(db, spec)
    return (row_id, sha_verified)


def seed_builtins_if_empty(db: sqlite3.Connection) -> int:
    """Seed built-in presets if none exist. Returns number of presets inserted."""
    cur = db.execute("SELECT COUNT(*) FROM overlay_preset")
    n = cur.fetchone()[0]
    if n > 0:
        return 0
    for p in BUILTIN_PRESETS:
        upsert_preset(db, p, created_by="system", is_builtin=True)
    return len(BUILTIN_PRESETS)
