"""F-15 — Triage / needs-attention HTTP routes."""
from __future__ import annotations

from fastapi import APIRouter, Query

from shorts_engine.db.session import engine
from shorts_engine.services.triage import gather_all, to_dicts


router = APIRouter(prefix="/api/triage", tags=["triage"])


def _sqlite():
    return engine.connect().connection.driver_connection


@router.get("/needs-attention")
def needs_attention(limit: int = Query(20, ge=1, le=100)):
    conn = _sqlite()
    try:
        items = gather_all(conn, limit_per_kind=limit)
    finally:
        conn.close()
    return {
        "items": to_dicts(items),
        "count": len(items),
        "by_priority": {
            str(p): sum(1 for i in items if i.priority == p)
            for p in range(1, 6)
        },
    }


@router.get("/by-kind/{kind}")
def by_kind(kind: str, limit: int = Query(20, ge=1, le=100)):
    conn = _sqlite()
    try:
        items = [i for i in gather_all(conn, limit_per_kind=limit) if i.kind == kind]
    finally:
        conn.close()
    return {"kind": kind, "items": to_dicts(items), "count": len(items)}
