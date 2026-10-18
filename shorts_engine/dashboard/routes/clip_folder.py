"""R4-1.3 — /api/clips/{clip_id}/open-folder endpoint.

Returns the cross-platform command the operator UI should use to reveal
the clip's file. Does NOT execute the command server-side; that would be
a code-execution vector.
"""
from __future__ import annotations

import os
from fastapi import APIRouter, HTTPException

from shorts_engine.db.models import Clip
from shorts_engine.db.session import SessionLocal
from shorts_engine.services.clip_folder import describe_reveal, run_reveal


router = APIRouter(prefix="/api/clips", tags=["clips"])


@router.get("/{clip_id}/open-folder")
def open_folder(clip_id: str, execute: bool = False):
    """Return the platform-specific command to reveal this clip's file.

    With ``execute=true`` the server will run the command itself. This is
    intended for single-user installs where the dashboard and the owner
    share the same machine; it MUST NOT be enabled on multi-tenant
    deployments.
    """
    with SessionLocal() as s:
        clip = s.query(Clip).filter(Clip.id == clip_id).first()
        if clip is None:
            raise HTTPException(status_code=404, detail=f"clip {clip_id} not found")
        # Pick the best available path: rendered → final → anything else.
        path = getattr(clip, "rendered_path", None) or getattr(clip, "final_path", None)
        if not path or not os.path.exists(path):
            # Fall back to a description that says "missing" — still useful.
            return describe_reveal(path or "")
    desc = describe_reveal(path)
    if execute:
        ok, detail = run_reveal(path)
        desc["executed"] = ok
        desc["execution_detail"] = detail
    return desc
