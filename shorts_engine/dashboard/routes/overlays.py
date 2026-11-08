"""E-011 — Overlay engine HTTP routes."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from shorts_engine.dashboard.auth import require_operator, require_viewer, verify_csrf
from shorts_engine.db.session import get_session, engine
from shorts_engine.db.sqlite_adapter import SqliteAdapter
from shorts_engine.services.overlay import (
    OverlaySpec,
    parse_spec,
    serialize_spec,
    validate_spec,
    list_builtin_presets,
    get_builtin_preset,
    upsert_preset,
    export_preset,
    export_all,
    import_preset,
    preset_sha256,
    seed_builtins_if_empty,
    compile_ffmpeg_filter,
    apply_preset_to_candidate,
    batch_apply_preset,
    list_audit,
    last_audit_for_candidate,
    preview_overlay_at_t,
    preview_ascii,
    preview_summary,
)


router = APIRouter(
    prefix="/api/overlays",
    tags=["overlays"],
    # Presets drive rendering, so every overlay endpoint requires a signed-in user.
    dependencies=[Depends(require_viewer)],
)

# Mutating endpoints additionally require the operator role and a CSRF token.
MUTATION_GUARDS = [Depends(require_operator), Depends(verify_csrf)]


def _sqlite():
    """Returns a sqlite3-compatible wrapper backed by a real SQLAlchemy connection.

    The wrapper presents a sqlite3.Connection-compatible interface so the overlay
    engine and presets modules (which use raw sqlite3 with positional `?`
    placeholders) keep working, while never exposing the raw driver connection
    to a direct close() — the root cause of the _sqlite() segfault under
    concurrent requests.
    """
    sa_conn = engine.connect()
    return SqliteAdapter(sa_conn)


def get_session():
    """Provide a SQLAlchemy session for endpoints that need one (e.g. batch_apply_preset).

    The overlay routes historically called ``get_session()`` from ``shorts_engine.db.session``;
    keep that import-compatible shim so existing service-layer call sites compile.
    """
    from shorts_engine.db.session import SessionLocal
    return SessionLocal()


class PresetUpsertIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = ""
    spec: dict
    created_by: str = "system"


class PresetImportIn(BaseModel):
    payload: dict
    verify_sha: bool = True


class BatchApplyIn(BaseModel):
    preset_id: int
    candidate_ids: list[int]
    output_dir: str
    allow_dry_run: bool = True


@router.get("/presets")
def list_presets():
    conn = _sqlite()
    try:
        seed_builtins_if_empty(conn)
        rows = conn.execute(
            "SELECT id, name, description, spec_sha256, is_builtin, created_at, updated_at "
            "FROM overlay_preset ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    return {
        "presets": [
            {
                "id": r[0], "name": r[1], "description": r[2], "spec_sha256": r[3],
                "is_builtin": bool(r[4]), "created_at": str(r[5]), "updated_at": str(r[6]),
            }
            for r in rows
        ],
        "builtins": list_builtin_presets(),
    }


@router.get("/presets/export-all")
def export_all_p():
    conn = _sqlite()
    try:
        return {"presets": export_all(conn)}
    finally:
        conn.close()


@router.get("/presets/{name}")
def get_preset(name: str):
    conn = _sqlite()
    try:
        try:
            return export_preset(conn, name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"preset {name} not found")
    finally:
        conn.close()


@router.post("/presets", dependencies=MUTATION_GUARDS)
def upsert(body: PresetUpsertIn):
    spec = parse_spec(body.spec)
    errs = validate_spec(spec)
    if errs:
        raise HTTPException(status_code=400, detail={"validation_errors": errs})
    spec.name = body.name
    spec.description = body.description
    conn = _sqlite()
    try:
        seed_builtins_if_empty(conn)
        row_id = upsert_preset(conn, spec, created_by=body.created_by)
    finally:
        conn.close()
    return {"id": row_id, "name": spec.name, "spec_sha256": preset_sha256(spec)}


@router.post("/presets/import", dependencies=MUTATION_GUARDS)
def import_p(body: PresetImportIn):
    conn = _sqlite()
    try:
        seed_builtins_if_empty(conn)
        try:
            row_id, verified = import_preset(conn, body.payload, verify_sha=body.verify_sha)
        except (ValueError, KeyError) as e:
            raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()
    return {"id": row_id, "verified": verified}


@router.get("/presets/export-all")
def export_all_p():
    conn = _sqlite()
    try:
        return {"presets": export_all(conn)}
    finally:
        conn.close()


@router.post("/presets/{name}/compile", dependencies=MUTATION_GUARDS)
def compile_p(name: str, video_duration: Optional[float] = None):
    conn = _sqlite()
    try:
        try:
            payload = export_preset(conn, name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"preset {name} not found")
    finally:
        conn.close()
    spec = parse_spec(payload["spec"])
    chain, contrasts = compile_ffmpeg_filter(spec, video_duration=video_duration)
    return {"name": name, "filter_chain": chain, "contrasts": contrasts}


@router.post("/presets/{name}/validate", dependencies=MUTATION_GUARDS)
def validate_p(name: Optional[str] = None, spec: Optional[dict] = Body(None)):
    if spec is not None:
        s = parse_spec(spec)
    elif name is not None:
        conn = _sqlite()
        try:
            try:
                payload = export_preset(conn, name)
            except KeyError:
                raise HTTPException(status_code=404, detail=f"preset {name} not found")
        finally:
            conn.close()
        s = parse_spec(payload["spec"])
    else:
        raise HTTPException(status_code=400, detail="spec or name required")
    errs = validate_spec(s)
    return {"valid": not errs, "errors": errs, "summary": preview_summary(s)}


@router.post("/apply", dependencies=MUTATION_GUARDS)
def apply(body: BatchApplyIn):
    conn = _sqlite()
    try:
        seed_builtins_if_empty(conn)
        try:
            rows = batch_apply_preset(
                conn,
                body.preset_id,
                body.candidate_ids,
                body.output_dir,
                allow_dry_run=body.allow_dry_run,
            )
        except (KeyError, RuntimeError) as e:
            raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()
    return {
        "applied": [
            {
                "candidate_id": r.candidate_id, "preset_id": r.preset_id, "ok": r.ok,
                "output_path": r.output_path, "error": r.error, "contrast_ratio": r.contrast_ratio,
                "bytes_written": r.bytes_written,
            }
            for r in rows
        ]
    }


@router.get("/audit")
def audit(candidate_id: Optional[int] = Query(None)):
    conn = _sqlite()
    try:
        rows = list_audit(conn, candidate_id=candidate_id)
    finally:
        conn.close()
    return {"audit": rows}


@router.get("/preview/{name}")
def preview(name: str, t: float = 0.0, ascii: bool = False):
    conn = _sqlite()
    try:
        try:
            payload = export_preset(conn, name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"preset {name} not found")
    finally:
        conn.close()
    s2 = parse_spec(payload["spec"])
    rec = preview_overlay_at_t(s2, t)
    body = {"name": name, "t": t, "layers": rec, "summary": preview_summary(s2)}
    if ascii:
        body["ascii"] = preview_ascii(s2, t)
    return body


@router.post("/seed-builtins", dependencies=MUTATION_GUARDS)
def seed_builtins():
    conn = _sqlite()
    try:
        n = seed_builtins_if_empty(conn)
    finally:
        conn.close()
    return {"seeded": n}
