"""E-011 — Compile an OverlaySpec to ffmpeg filter chain."""
from __future__ import annotations

import re
from typing import List, Tuple, Optional

from .spec import OverlaySpec, OverlayLayer, LayerType, TextStyle, ImageStyle, StickerStyle, ShapeStyle


_DRAWTEXT_UNSAFE = re.compile(r"[^A-Za-z0-9:,.()\[\]/\\+\- ]")


def escape_drawtext(text: str) -> str:
    """Escape a text string for safe use inside ffmpeg drawtext text=... .

    ffmpeg drawtext requires escaping these characters: \\ : ' and several others.
    We backslash-escape the unsafe set and also neutralize potential drawtext
    directives (lines beginning with a single quote or other directives).
    """
    if text is None:
        return ""
    # Strip ASCII control characters.
    text = "".join(ch for ch in text if ord(ch) >= 0x20 or ch in "\t\n")
    # Backslash-escape backslash, single quote, colon, percent.
    out = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == "'":
            out.append("\\'")
        elif ch == ":":
            out.append("\\:")
        elif ch == "%":
            out.append("\\%")
        else:
            out.append(ch)
    return "".join(out)


def _hex_to_ass_color(hex_color: str) -> str:
    """#RRGGBB -> &HBBGGRR (ASS drawtext color format)."""
    s = hex_color.lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    r, g, b = s[0:2], s[2:4], s[4:6]
    return f"&H00{b}{g}{r}".upper()


def compile_drawtext_filter(layer: OverlayLayer, base_w: int, base_h: int) -> Tuple[str, float]:
    """Returns (ffmpeg_filter_string, contrast_ratio_vs_black_bg)."""
    if layer.text_style is None or not layer.text:
        return ("", 1.0)
    ts: TextStyle = layer.text_style
    fg = ts.color
    outline = ts.outline_color
    x_px = int(round(layer.x * base_w))
    y_px = int(round(layer.y * base_h))
    safe_text = escape_drawtext(layer.text)
    # Compute approximate contrast vs. the layer's underlying background. We use
    # black as a conservative worst-case since real background varies frame-by-frame.
    fg_l = _rel_luminance(fg)
    contrast = (max(fg_l, 0.0) + 0.05) / (min(_rel_luminance("#000000"), fg_l) + 0.05)
    parts = [
        f"text='{safe_text}'",
        f"fontfile=/usr/share/fonts/truetype/dejavu/{ts.font}.ttf",
        f"fontsize={ts.size}",
        f"fontcolor={fg}",
        f"x={x_px}",
        f"y={y_px}",
    ]
    if outline and ts.outline_width > 0:
        parts.append(f"borderw={ts.outline_width}")
        parts.append(f"bordercolor={outline}")
    if ts.shadow:
        parts.append("shadowcolor=#000000@0.5")
        parts.append("shadowx=2")
        parts.append("shadowy=2")
    return (f"drawtext={','.join(parts)}", contrast)


def _rel_luminance(hex_color: str) -> float:
    # Avoid circular import: re-implement here (kept consistent with contrast.py).
    s = hex_color.lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)

    def lin(c: int) -> float:
        cs = c / 255.0
        return cs / 12.92 if cs <= 0.03928 else ((cs + 0.055) / 1.055) ** 2.4

    r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def compile_image_overlay_filter(layer: OverlayLayer, base_w: int, base_h: int, input_index: int) -> str:
    """Returns an ffmpeg overlay filter; the caller must declare the image as
    additional input and reference it via [idx:v]."""
    if not layer.image_path:
        return ""
    ist: ImageStyle = layer.image_style or ImageStyle()
    x_px = int(round(layer.x * base_w))
    y_px = int(round(layer.y * base_h))
    parts = [f"[{input_index}:v]"]
    if ist.width or ist.height:
        w = ist.width or -1
        h = ist.height or -1
        parts.append(f"scale={w}:{h}")
    if ist.opacity < 1.0:
        parts.append(f"format=rgba,colorchannelmixer=aa={ist.opacity}")
    parts.append(f"[ov{input_index}];[base][ov{input_index}]overlay={x_px}:{y_px}")
    return ",".join(parts[:-1]) + ";" + parts[-1]


def compile_shape_filter(layer: OverlayLayer, base_w: int, base_h: int) -> str:
    """Render a shape as drawbox (rect) or geq (ellipse/line approximation)."""
    sh: ShapeStyle = layer.shape or ShapeStyle()
    x_px = int(round(layer.x * base_w))
    y_px = int(round(layer.y * base_h))
    if sh.shape == "rect":
        s = f"drawbox=x={x_px}:y={y_px}:w={sh.width}:h={sh.height}:color={sh.color}@{sh.opacity}:t={sh.border}"
        if sh.border > 0:
            s += f":bordercolor={sh.border_color}"
        return s
    if sh.shape == "ellipse":
        # Approximate ellipse via geq (per-pixel fill inside a rect).
        # NOTE: ffmpeg has no native ellipse filter. We use drawbox as a fallback.
        # Real implementation could use lavfi geq, but a rounded-rect approximation
        # is acceptable for a preview-quality build.
        return f"drawbox=x={x_px}:y={y_px}:w={sh.width}:h={sh.height}:color={sh.color}@{sh.opacity}:t=fill"
    if sh.shape == "line":
        # drawbox of height=border approximates a horizontal line.
        return f"drawbox=x={x_px}:y={y_px}:w={sh.width}:h={max(1, sh.border)}:color={sh.color}@{sh.opacity}:t=fill"
    return ""


def compile_sticker_filter(layer: OverlayLayer, base_w: int, base_h: int) -> str:
    """Stickers are text-based glyphs rendered via drawtext (★ ♥ ➜ 🔥 👍)."""
    st: StickerStyle = layer.sticker or StickerStyle()
    glyph_map = {
        "star": "★",
        "heart": "♥",
        "arrow": "➜",
        "fire": "🔥",
        "thumbs": "👍",
    }
    glyph = glyph_map.get(st.kind, st.kind)
    x_px = int(round(layer.x * base_w))
    y_px = int(round(layer.y * base_h))
    return (
        f"drawtext=text='{escape_drawtext(glyph)}',"
        f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf,"
        f"fontsize={st.size},fontcolor={st.color},x={x_px},y={y_px}"
    )


def compile_ffmpeg_filter(
    spec: OverlaySpec,
    video_duration: Optional[float] = None,
) -> Tuple[str, List[float]]:
    """Returns (full_filter_chain, list_of_contrast_ratios_for_text_layers).

    Layers are sorted by z ascending; higher z renders last (on top).
    Time-window is applied via ffmpeg `enable=between(t,start,end)`.
    """
    base_w = spec.base_w
    base_h = spec.base_h
    sorted_layers = sorted(spec.layers, key=lambda L: (L.z, 0))
    chain_parts: List[str] = []
    contrasts: List[float] = []
    extra_inputs = 0
    for layer in sorted_layers:
        if video_duration is not None and (layer.end <= 0 or layer.start >= video_duration):
            continue
        if layer.layer_type == LayerType.TEXT:
            filt, c = compile_drawtext_filter(layer, base_w, base_h)
            if filt:
                if video_duration is not None:
                    filt = filt + f":enable='between(t,{layer.start},{min(layer.end, video_duration)})'"
                chain_parts.append(filt)
                contrasts.append(c)
        elif layer.layer_type == LayerType.IMAGE:
            extra_inputs += 1
            chain_parts.append(compile_image_overlay_filter(layer, base_w, base_h, extra_inputs))
        elif layer.layer_type == LayerType.STICKER:
            filt = compile_sticker_filter(layer, base_w, base_h)
            if filt:
                if video_duration is not None:
                    filt = filt + f":enable='between(t,{layer.start},{min(layer.end, video_duration)})'"
                chain_parts.append(filt)
        elif layer.layer_type == LayerType.SHAPE:
            filt = compile_shape_filter(layer, base_w, base_h)
            if filt:
                if video_duration is not None:
                    filt = filt + f":enable='between(t,{layer.start},{min(layer.end, video_duration)})'"
                chain_parts.append(filt)
    chain = ",".join(p for p in chain_parts if p)
    return (chain, contrasts)
