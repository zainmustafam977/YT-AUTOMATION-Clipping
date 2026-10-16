"""E-011 — WCAG 2.x contrast computation for overlay text legibility."""
from __future__ import annotations

from typing import Tuple


def _channel_to_linear(c: int) -> float:
    """sRGB channel [0..255] -> relative luminance component."""
    cs = c / 255.0
    return cs / 12.92 if cs <= 0.03928 else ((cs + 0.055) / 1.055) ** 2.4


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    s = hex_color.strip()
    if s.startswith("#") and len(s) == 4:
        s = "#" + "".join(ch * 2 for ch in s[1:])
    if not s.startswith("#") or len(s) != 7:
        raise ValueError(f"color must be #RRGGBB, got {hex_color!r}")
    return (int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16))


def rel_luminance(hex_color: str) -> float:
    """WCAG relative luminance of an #RRGGBB color."""
    r, g, b = _hex_to_rgb(hex_color)
    return 0.2126 * _channel_to_linear(r) + 0.7152 * _channel_to_linear(g) + 0.0722 * _channel_to_linear(b)


def wcag_contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    """WCAG 2.x contrast ratio between two #RRGGBB colors. Range: 1.0..21.0."""
    l1 = rel_luminance(fg_hex)
    l2 = rel_luminance(bg_hex)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


_BW = ("#FFFFFF", "#000000")


def best_contrast_color(bg_hex: str, candidates=("#FFFFFF", "#000000", "#FFEB3B", "#03A9F4")) -> Tuple[str, float]:
    """Returns (best_color, contrast_ratio) — picks the candidate with highest contrast against bg."""
    best = candidates[0]
    best_r = wcag_contrast_ratio(best, bg_hex)
    for c in candidates[1:]:
        r = wcag_contrast_ratio(c, bg_hex)
        if r > best_r:
            best = c
            best_r = r
    return best, best_r
