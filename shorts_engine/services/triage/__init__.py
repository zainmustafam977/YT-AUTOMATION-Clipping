"""F-15 — Triage primitives for the operator console.

Computes a prioritized list of items that need attention: failed jobs, stale
clips, low-CTR uploads, queued-too-long, etc.

Each item has: id, kind, label, priority (1-5, 5 = critical), reason, action_url.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, asdict
from typing import Any, Dict, List


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


def gather_all(conn: sqlite3.Connection, limit_per_kind: int = 20) -> List[TriageItem]:
    """Run all triage checks; return sorted by priority (desc) then by reason."""
    items: List[TriageItem] = []
    items += triage_stuck_pipeline_jobs(conn, limit_per_kind)
    items += triage_failed_pipeline_jobs(conn, limit_per_kind)
    items += triage_low_ctr_clips(conn, limit_per_kind)
    items += triage_queued_too_long(conn, limit_per_kind)
    items += triage_dlq(conn, limit_per_kind)
    items.sort(key=lambda i: (-i.priority, i.reason))
    return items


def to_dicts(items: List[TriageItem]) -> List[Dict[str, Any]]:
    return [asdict(i) for i in items]
