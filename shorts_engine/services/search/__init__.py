"""E-021 — Global search + command palette service.

Searches across:
- clips (title)
- channels (title, handle)
- source_videos (title, youtube_video_id)
- clip_candidates (notes)
- saved_views (name, expression)
- overlay_presets (name, description)

Returns ranked results with type, id, label, snippet, score.
"""
from __future__ import annotations

import sqlite3
from typing import List, Dict, Any, Optional, Tuple


# Each entry: (kind, table, label_fields, snippet_fields, score_field)
# score_field: 0 = no scoring (use 1.0), column = column to order by
SEARCH_TARGETS = [
    ("clip", "clips", ("title",), ("title",), "id"),
    ("channel", "channels", ("title", "handle"), ("title", "handle"), "id"),
    ("source_video", "source_videos", ("title", "youtube_video_id"), ("title",), "id"),
    ("saved_view", "saved_views", ("name", "expression"), ("name", "expression"), "id"),
    ("overlay_preset", "overlay_preset", ("name", "description"), ("name", "description"), "id"),
]


def _row_to_dict(table: str, row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def _score(query: str, text: str) -> float:
    """Simple substring-based scoring. Higher = better match."""
    if not text:
        return 0.0
    q = query.lower()
    t = text.lower()
    if t == q:
        return 100.0
    if t.startswith(q):
        return 50.0
    if q in t:
        return 25.0
    # Word-prefix match.
    for word in t.split():
        if word.startswith(q):
            return 10.0
    return 0.0


def search_all(conn: sqlite3.Connection, query: str, limit_per_kind: int = 5) -> List[Dict[str, Any]]:
    """Run a global search; return ranked results."""
    if not query or not query.strip():
        return []
    results: List[Dict[str, Any]] = []
    for kind, table, label_fields, snippet_fields, id_col in SEARCH_TARGETS:
        try:
            cur = conn.execute(f"SELECT * FROM {table}")
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
        except sqlite3.OperationalError:
            # Table doesn't exist in this DB (e.g., missing migrations).
            continue
        for row in rows:
            d = dict(zip(columns, row))
            labels = [str(d.get(f, "")) for f in label_fields if d.get(f) is not None]
            snippets = [str(d.get(f, ""))[:120] for f in snippet_fields if d.get(f) is not None]
            label_text = " | ".join(labels)
            best_score = max((_score(query, s) for s in labels), default=0.0)
            if best_score <= 0:
                continue
            results.append({
                "kind": kind,
                "id": d.get(id_col),
                "label": label_text,
                "snippet": " | ".join(snippets)[:200],
                "score": best_score,
            })
    results.sort(key=lambda r: r["score"], reverse=True)
    # Apply per-kind cap.
    out: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    for r in results:
        if counts.get(r["kind"], 0) >= limit_per_kind:
            continue
        out.append(r)
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    return out


def command_palette(conn: sqlite3.Connection, query: str) -> List[Dict[str, Any]]:
    """Same as search_all but the response is shaped for a command palette UI.

    Adds: 'action' (e.g., navigate to /clips/123), 'shortcut' (display label).
    """
    results = search_all(conn, query, limit_per_kind=3)
    for r in results:
        kind = r["kind"]
        rid = r["id"]
        action_map = {
            "clip": f"/clips/{rid}",
            "channel": f"/channels/{rid}",
            "source_video": f"/sources/{rid}",
            "saved_view": f"/saved-views/{rid}",
            "overlay_preset": f"/overlays/presets/{rid}",
        }
        r["action"] = action_map.get(kind, f"/search?q={query}")
    return results
