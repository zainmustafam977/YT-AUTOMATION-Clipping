"""F-15 — Triage primitives for the operator console.

Computes a prioritized list of items that need attention: failed jobs, stale
clips, low-CTR uploads, queued-too-long, etc.

Each item has: id, kind, label, priority (1-5, 5 = critical), reason, action_url.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any


@dataclass
class TriageItem:
    kind: str
    id: str
    label: str
    priority: int  # 1=info, 5=critical
    reason: str
    action_url: str
    metadata: Dict[str, Any]


def triage_stuck_pipeline_jobs(conn: sqlite3.Connection, limit: int = 20) -> List[TriageItem]:
    """Pipeline jobs that have been RUNNING for > 1 hour — likely stuck."""
    out: List[TriageItem] = []
    try:
        rows = conn.execute(
            "SELECT id, entity_id, stage, started_at FROM pipeline_jobs "
            "WHERE status = 'RUNNING' AND started_at < datetime('now', '-1 hour') "
            "ORDER BY started_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    for r in rows:
        out.append(TriageItem(
            kind="stuck_pipeline_job",
            id=str(r[0]),
            label=f"{r[2]} (entity {r[1]})",
            priority=3,
            reason=f"running since {r[3]} (>1h)",
            action_url=f"/jobs/{r[0]}",
            metadata={"entity_id": r[1], "stage": r[2], "started_at": str(r[3])},
        ))
    return out


def triage_failed_pipeline_jobs(conn: sqlite3.Connection, limit: int = 20) -> List[TriageItem]:
    """Pipeline jobs in FAILED state with attempt_count >= max_attempts."""
    out: List[TriageItem] = []
    try:
        rows = conn.execute(
            "SELECT id, entity_id, stage, error, attempt_count FROM pipeline_jobs "
            "WHERE status = 'FAILED' ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    for r in rows:
        priority = 5 if r[4] and r[4] >= 3 else 4
        out.append(TriageItem(
            kind="failed_pipeline_job",
            id=str(r[0]),
            label=f"{r[2]} (entity {r[1]})",
            priority=priority,
            reason=str(r[3])[:120] if r[3] else "no error message",
            action_url=f"/jobs/{r[0]}",
            metadata={"entity_id": r[1], "stage": r[2], "attempts": r[4]},
        ))
    return out


def triage_low_ctr_clips(conn: sqlite3.Connection, limit: int = 20) -> List[TriageItem]:
    """Clips with low CTR (< 1%) that have been published for at least 7 days."""
    out: List[TriageItem] = []
    try:
        rows = conn.execute(
            "SELECT id, title, ctr, published_at FROM clips "
            "WHERE status = 'PUBLISHED' AND ctr < 0.01 AND impressions > 100 "
            "AND published_at < datetime('now', '-7 days') "
            "ORDER BY ctr ASC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    for r in rows:
        out.append(TriageItem(
            kind="low_ctr_clip",
            id=str(r[0]),
            label=r[1] or f"clip {r[0]}",
            priority=2,
            reason=f"CTR {r[2]:.2%} after 7+ days",
            action_url=f"/clips/{r[0]}",
            metadata={"ctr": r[2], "published_at": str(r[3])},
        ))
    return out


def triage_queued_too_long(conn: sqlite3.Connection, limit: int = 20) -> List[TriageItem]:
    """Pipeline jobs in PENDING state for > 24 hours."""
    out: List[TriageItem] = []
    try:
        rows = conn.execute(
            "SELECT id, entity_id, stage, created_at FROM pipeline_jobs "
            "WHERE status = 'PENDING' AND created_at < datetime('now', '-24 hours') "
            "ORDER BY created_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    for r in rows:
        out.append(TriageItem(
            kind="queued_too_long",
            id=str(r[0]),
            label=f"{r[2]} (entity {r[1]})",
            priority=2,
            reason=f"pending since {r[3]} (>24h)",
            action_url=f"/jobs/{r[0]}",
            metadata={"entity_id": r[1], "stage": r[2]},
        ))
    return out


def triage_dlq(conn: sqlite3.Connection, limit: int = 20) -> List[TriageItem]:
    """Webhook deliveries in the dead-letter table."""
    out: List[TriageItem] = []
    try:
        rows = conn.execute(
            "SELECT id, webhook_id, last_error, attempts FROM webhook_dead_letter "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    for r in rows:
        out.append(TriageItem(
            kind="webhook_dead_letter",
            id=str(r[0]),
            label=f"webhook {r[1]}",
            priority=4,
            reason=str(r[2])[:120] if r[2] else "no error",
            action_url=f"/webhooks/dead-letter/{r[0]}",
            metadata={"webhook_id": r[1], "attempts": r[3]},
        ))
    return out


def triage_oauth_token_expiring(conn: sqlite3.Connection, limit: int = 20) -> List[TriageItem]:
    """R4-8.7: OAuth tokens with no recent successful use are flagged.

    The encrypted_oauth_token column is updated whenever a token is used.
    If a channel's last_verified_at is older than 30 days, the operator
    must re-authorise.
    """
    out: List[TriageItem] = []
    try:
        rows = conn.execute(
            "SELECT id, title, handle, last_verified_at FROM channels "
            "WHERE last_verified_at IS NOT NULL "
            "AND last_verified_at < datetime('now', '-30 days') "
            "ORDER BY last_verified_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    for r in rows:
        out.append(TriageItem(
            kind="oauth_token_expiring",
            id=str(r[0]),
            label=r[1] or r[2] or f"channel {r[0]}",
            priority=3,
            reason=f"last verified {r[3]} (>30 days ago)",
            action_url=f"/channels/{r[0]}",
            metadata={"last_verified_at": str(r[3])},
        ))
    return out


def triage_oauth_no_token(conn: sqlite3.Connection, limit: int = 20) -> List[TriageItem]:
    """R4-8.7: channels with no OAuth token at all."""
    out: List[TriageItem] = []
    try:
        rows = conn.execute(
            "SELECT id, title, handle FROM channels "
            "WHERE encrypted_oauth_token IS NULL OR encrypted_oauth_token = '' "
            "ORDER BY title LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    for r in rows:
        out.append(TriageItem(
            kind="oauth_no_token",
            id=str(r[0]),
            label=r[1] or r[2] or f"channel {r[0]}",
            priority=5,
            reason="no OAuth token configured",
            action_url=f"/channels/{r[0]}",
            metadata={},
        ))
    return out


def triage_quota_at_warning(conn: sqlite3.Connection, limit: int = 20) -> List[TriageItem]:
    """R4-8.7: channels whose daily quota is at >= 80% of the YouTube allowance.

    The quota_ledger table records per-day usage. The default YouTube
    upload-quota allowance is 10,000 units; we warn at 80% (8,000) and
    alert at 100% (10,000).
    """
    out: List[TriageItem] = []
    DAILY_QUOTA_UNITS = 10000
    try:
        rows = conn.execute(
            "SELECT channel_id, date, units_used FROM quota_ledger "
            "WHERE date = date('now') AND units_used >= ? "
            "ORDER BY units_used DESC LIMIT ?",
            (int(DAILY_QUOTA_UNITS * 0.8), limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    for r in rows:
        pct = (r[2] / DAILY_QUOTA_UNITS) * 100.0
        priority = 5 if r[2] >= DAILY_QUOTA_UNITS else 4
        out.append(TriageItem(
            kind="quota_at_warning",
            id=str(r[0]),
            label=f"channel {r[0]}",
            priority=priority,
            reason=f"{r[2]}/{DAILY_QUOTA_UNITS} units ({pct:.0f}%) used today",
            action_url=f"/channels/{r[0]}",
            metadata={"units_used": r[2], "date": str(r[1])},
        ))
    return out


def triage_master_key_age(conn: sqlite3.Connection, limit: int = 20) -> List[TriageItem]:
    """R4-8.3: flag if the SECRETS_MASTER_KEY is older than 180 days.

    The key age is stored in a small `key_rotation_log` table (created
    here if missing). Owners can extend by writing a row at rotation time.
    """
    out: List[TriageItem] = []
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS key_rotation_log ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  rotated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
            "  rotated_by VARCHAR(64),"
            "  notes TEXT"
            ")"
        )
        conn.commit()
    except sqlite3.OperationalError:
        return out
    try:
        rows = conn.execute(
            "SELECT id, rotated_at, rotated_by FROM key_rotation_log "
            "ORDER BY rotated_at DESC LIMIT 1"
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    if not rows:
        out.append(TriageItem(
            kind="master_key_no_rotation_log",
            id="0",
            label="SECRETS_MASTER_KEY",
            priority=2,
            reason="no rotation log entry (record the first rotation)",
            action_url="/admin/keys",
            metadata={},
        ))
        return out
    last = rows[0]
    try:
        from datetime import datetime, timedelta
        rotated = datetime.fromisoformat(str(last[1]))
        age = datetime.utcnow() - rotated
        if age > timedelta(days=180):
            out.append(TriageItem(
                kind="master_key_old",
                id=str(last[0]),
                label="SECRETS_MASTER_KEY",
                priority=4,
                reason=f"rotated {int(age.days)} days ago (>180)",
                action_url="/admin/keys",
                metadata={"rotated_at": str(last[1]), "rotated_by": last[2]},
            ))
    except Exception:
        pass
    return out


def gather_all(conn: sqlite3.Connection, limit_per_kind: int = 20) -> List[TriageItem]:
    """Run all triage checks; return sorted by priority (desc) then by reason."""
    items: List[TriageItem] = []
    items += triage_stuck_pipeline_jobs(conn, limit_per_kind)
    items += triage_failed_pipeline_jobs(conn, limit_per_kind)
    items += triage_low_ctr_clips(conn, limit_per_kind)
    items += triage_queued_too_long(conn, limit_per_kind)
    items += triage_dlq(conn, limit_per_kind)
    items += triage_oauth_token_expiring(conn, limit_per_kind)
    items += triage_oauth_no_token(conn, limit_per_kind)
    items += triage_quota_at_warning(conn, limit_per_kind)
    items += triage_master_key_age(conn, limit_per_kind)
    items.sort(key=lambda i: (-i.priority, i.reason))
    return items


def to_dicts(items: List[TriageItem]) -> List[Dict[str, Any]]:
    return [asdict(i) for i in items]
