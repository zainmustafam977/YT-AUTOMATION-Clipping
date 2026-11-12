"""E-011 — Preview an overlay at a specific time without rendering video.

Useful for the dashboard: shows a JSON / ASCII description of which layers
are visible at t, with bounding boxes in pixels.
"""
from __future__ import annotations

from typing import List, Optional, Dict, Any

from .spec import OverlaySpec, LayerType


def preview_overlay_at_t(
    spec: OverlaySpec, t: float, base_w: Optional[int] = None, base_h: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Returns one record per layer visible at time t (sorted by z)."""
    base_w = base_w or spec.base_w
    base_h = base_h or spec.base_h
    out: List[Dict[str, Any]] = []
    for layer in sorted(spec.layers, key=lambda L: L.z):
        if not (layer.start <= t <= layer.end):
            continue
        rec: Dict[str, Any] = {
            "kind": layer.layer_type.value,
            "x_px": int(round(layer.x * base_w)),
            "y_px": int(round(layer.y * base_h)),
            "z": layer.z,
            "start": layer.start,
            "end": layer.end,
        }
        if layer.layer_type == LayerType.TEXT and layer.text:
            rec["text"] = layer.text
            rec["color"] = (layer.text_style.color if layer.text_style else "#FFFFFF")
        elif layer.layer_type == LayerType.IMAGE and layer.image_path:
            rec["image_path"] = layer.image_path
        elif layer.layer_type == LayerType.STICKER and layer.sticker:
            rec["sticker_kind"] = layer.sticker.kind
            rec["color"] = layer.sticker.color
        elif layer.layer_type == LayerType.SHAPE and layer.shape:
            rec["shape_kind"] = layer.shape.shape
            rec["color"] = layer.shape.color
            rec["w_px"] = layer.shape.width
            rec["h_px"] = layer.shape.height
        out.append(rec)
    return out


def preview_ascii(spec: OverlaySpec, t: float = 0.0, width: int = 40, height: int = 12) -> str:
    """Tiny ASCII grid showing where each layer sits at time t (for tests / CLI)."""
    base_w = spec.base_w
    base_h = spec.base_h
    grid: List[List[str]] = [[" "] * width for _ in range(height)]
    for rec in preview_overlay_at_t(spec, t, base_w, base_h):
        x = int(rec["x_px"] / base_w * (width - 1))
        y = int(rec["y_px"] / base_h * (height - 1))
        if 0 <= x < width and 0 <= y < height:
            ch = {
                "text": "T",
                "image": "I",
                "sticker": "S",
                "shape": "B",
            }.get(rec["kind"], "?")
            grid[y][x] = ch
    return "\n".join("".join(row) for row in grid)


def preview_summary(spec: OverlaySpec) -> Dict[str, Any]:
    """High-level summary: counts by layer type, time range, base dims."""
    types: Dict[str, int] = {}
    earliest: Optional[float] = None
    latest: Optional[float] = None
    for layer in spec.layers:
        types[layer.layer_type.value] = types.get(layer.layer_type.value, 0) + 1
        if earliest is None or layer.start < earliest:
            earliest = layer.start
        if latest is None or layer.end > latest:
            latest = layer.end
    return {
        "name": spec.name,
        "base": f"{spec.base_w}x{spec.base_h}",
        "layer_count": len(spec.layers),
        "layer_types": types,
        "earliest_start": earliest,
        "latest_end": latest,
    }
