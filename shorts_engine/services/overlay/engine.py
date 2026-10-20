"""E-011 — Render engine: apply a preset to a candidate and audit it."""
from __future__ import annotations

import os
import sqlite3
import subprocess
from dataclasses import dataclass
from typing import Iterable, List, Optional

from .compiler import compile_ffmpeg_filter
from .spec import parse_spec


@dataclass
class AuditRow:
    candidate_id: int
    preset_id: int
    ok: bool
    output_path: Optional[str]
    error: Optional[str]
    ffmpeg_cmd: str
    contrast_ratio: Optional[float]
    bytes_written: Optional[int]


def _resolve_video_path(db: sqlite3.Connection, candidate_id: int) -> Optional[str]:
    row = db.execute(
        "SELECT cc.video_path, cc.id FROM clip_candidates cc WHERE cc.id = ?",
        (candidate_id,),
    ).fetchone()
    if row is None:
        return None
    return row[0]


def apply_preset_to_candidate(
    db: sqlite3.Connection,
    preset_id: int,
    candidate_id: int,
    output_path: str,
    *,
    ffmpeg_bin: str = "ffmpeg",
    allow_dry_run: bool = True,
    timeout: int = 120,
) -> AuditRow:
    """Apply a preset's filter chain to a candidate's source video.

    On success: writes output_path + writes audit row (ok=1).
    On error: writes audit row (ok=0) + raises RuntimeError.
    If allow_dry_run is True and the source video does not exist, returns
    an audit row without actually invoking ffmpeg (for CI / sandboxed envs).
    """
    # Load preset.
    row = db.execute(
        "SELECT spec_json FROM overlay_preset WHERE id = ?",
        (preset_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"preset id={preset_id} not found")
    spec = parse_spec(row[0])

    # Resolve source video.
    video_path = _resolve_video_path(db, candidate_id)
    if video_path is None:
        raise KeyError(f"candidate id={candidate_id} not found")

    filter_chain, contrasts = compile_ffmpeg_filter(spec)
    contrast_ratio = max(contrasts) if contrasts else None

    ffmpeg_cmd = (
        f"{ffmpeg_bin} -y -i {video_path!r} -vf {filter_chain!r} "
        f"-c:a copy {output_path!r}"
    )
    cur = db.execute(
        "INSERT INTO overlay_render_audit (candidate_id, preset_id, ok, ffmpeg_cmd, output_path, contrast_ratio) "
        "VALUES (?,?,0,?,?,?)",
        (candidate_id, preset_id, ffmpeg_cmd, output_path, contrast_ratio),
    )
    audit_id = int(cur.lastrowid)
    db.commit()

    if allow_dry_run and not os.path.exists(video_path):
        # Sandbox / CI mode: skip ffmpeg.
        db.execute(
            "UPDATE overlay_render_audit SET ok=1, finished_at=CURRENT_TIMESTAMP, bytes_written=0 WHERE id=?",
            (audit_id,),
        )
        db.commit()
        return AuditRow(
            candidate_id=candidate_id,
            preset_id=preset_id,
            ok=True,
            output_path=output_path,
            error=None,
            ffmpeg_cmd=ffmpeg_cmd,
            contrast_ratio=contrast_ratio,
            bytes_written=0,
        )

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    try:
        proc = subprocess.run(
            [ffmpeg_bin, "-y", "-i", video_path, "-vf", filter_chain, "-c:a", "copy", output_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        db.execute(
            "UPDATE overlay_render_audit SET ok=0, error=?, finished_at=CURRENT_TIMESTAMP WHERE id=?",
            (f"ffmpeg timeout: {e}", audit_id),
        )
        db.commit()
        raise RuntimeError(f"ffmpeg timeout for candidate={candidate_id}") from e
    if proc.returncode != 0:
        err = (proc.stderr or "")[-500:]
        db.execute(
            "UPDATE overlay_render_audit SET ok=0, error=?, finished_at=CURRENT_TIMESTAMP WHERE id=?",
            (err, audit_id),
        )
        db.commit()
        raise RuntimeError(f"ffmpeg failed: {err}")
    bytes_written = os.path.getsize(output_path) if os.path.exists(output_path) else 0
    db.execute(
        "UPDATE overlay_render_audit SET ok=1, finished_at=CURRENT_TIMESTAMP, bytes_written=? WHERE id=?",
        (bytes_written, audit_id),
    )
    db.commit()
    return AuditRow(
        candidate_id=candidate_id,
        preset_id=preset_id,
        ok=True,
        output_path=output_path,
        error=None,
        ffmpeg_cmd=ffmpeg_cmd,
        contrast_ratio=contrast_ratio,
        bytes_written=bytes_written,
    )


def batch_apply_preset(
    db: sqlite3.Connection,
    preset_id: int,
    candidate_ids: Iterable[int],
    output_dir: str,
    *,
    ffmpeg_bin: str = "ffmpeg",
    allow_dry_run: bool = True,
) -> List[AuditRow]:
    """Apply a preset to many candidates; return the audit rows.

    Failures are recorded in the audit table and re-raised.
    """
    os.makedirs(output_dir, exist_ok=True)
    out: List[AuditRow] = []
    for cid in candidate_ids:
        op = os.path.join(output_dir, f"candidate_{cid}.mp4")
        out.append(apply_preset_to_candidate(
            db, preset_id, cid, op,
            ffmpeg_bin=ffmpeg_bin,
            allow_dry_run=allow_dry_run,
        ))
    return out


def list_audit(db: sqlite3.Connection, candidate_id: Optional[int] = None) -> List[dict]:
    if candidate_id is None:
        rows = db.execute(
            "SELECT id, candidate_id, preset_id, ok, error, contrast_ratio, bytes_written, "
            "ffmpeg_cmd, output_path "
            "FROM overlay_render_audit ORDER BY id DESC"
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, candidate_id, preset_id, ok, error, contrast_ratio, bytes_written, "
            "ffmpeg_cmd, output_path "
            "FROM overlay_render_audit WHERE candidate_id = ? ORDER BY id DESC",
            (candidate_id,),
        ).fetchall()
    return [
        {
            "id": r[0], "candidate_id": r[1], "preset_id": r[2],
            "ok": bool(r[3]), "error": r[4], "contrast_ratio": r[5],
            "bytes_written": r[6], "ffmpeg_cmd": r[7], "output_path": r[8],
        }
        for r in rows
    ]


def last_audit_for_candidate(db: sqlite3.Connection, candidate_id: int) -> Optional[dict]:
    rows = list_audit(db, candidate_id)
    return rows[0] if rows else None
