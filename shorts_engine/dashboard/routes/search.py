"""E-021 — Global search + command palette HTTP routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from shorts_engine.db.session import engine
from shorts_engine.services.search import search_all, command_palette


router = APIRouter(prefix="/api", tags=["search"])


def _sqlite():
    return engine.connect().connection.driver_connection


@router.get("/search")
def search(q: str = Query(..., min_length=1), limit_per_kind: int = 5):
    conn = _sqlite()
    try:
        results = search_all(conn, q, limit_per_kind=limit_per_kind)
    finally:
        conn.close()
    return {"query": q, "results": results, "count": len(results)}


@router.get("/command-palette")
def palette(q: str = Query(..., min_length=1)):
    conn = _sqlite()
    try:
        results = command_palette(conn, q)
    finally:
        conn.close()
    return {"query": q, "commands": results, "count": len(results)}
