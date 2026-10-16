"""E-011 — Multi-layer overlay engine.

Public surface:
    OverlaySpec, OverlayLayer, LayerType, TextStyle, ImageStyle, StickerStyle, ShapeStyle
    compile_ffmpeg_filter(spec, base_w, base_h, video_duration) -> filter_chain, contrast_ratios
    apply_preset_to_candidate(preset, candidate, video_path, output_path) -> audit_row
    preview_overlay_at_t(spec, t_seconds, base_w, base_h) -> dict (label, kind, color_estimate)
    wcag_contrast_ratio(fg_hex, bg_hex) -> float
"""
from .spec import (
    OverlaySpec,
    OverlayLayer,
    LayerType,
    TextStyle,
    ImageStyle,
    StickerStyle,
    ShapeStyle,
    parse_spec,
    serialize_spec,
    validate_spec,
)
from .compiler import (
    compile_ffmpeg_filter,
    compile_drawtext_filter,
    compile_image_overlay_filter,
    compile_shape_filter,
    escape_drawtext,
)
from .contrast import wcag_contrast_ratio, best_contrast_color, rel_luminance
from .presets import (
    list_builtin_presets,
    get_builtin_preset,
    upsert_preset,
    export_preset,
    export_all,
    import_preset,
    preset_sha256,
    seed_builtins_if_empty,
)
from .engine import apply_preset_to_candidate, batch_apply_preset, list_audit, last_audit_for_candidate
from .preview import preview_overlay_at_t, preview_ascii, preview_summary

__all__ = [
    "OverlaySpec",
    "OverlayLayer",
    "LayerType",
    "TextStyle",
    "ImageStyle",
    "StickerStyle",
    "ShapeStyle",
    "parse_spec",
    "serialize_spec",
    "validate_spec",
    "compile_ffmpeg_filter",
    "compile_drawtext_filter",
    "compile_image_overlay_filter",
    "compile_shape_filter",
    "escape_drawtext",
    "wcag_contrast_ratio",
    "best_contrast_color",
    "rel_luminance",
    "list_builtin_presets",
    "get_builtin_preset",
    "upsert_preset",
    "export_preset",
    "export_all",
    "import_preset",
    "preset_sha256",
    "seed_builtins_if_empty",
    "apply_preset_to_candidate",
    "batch_apply_preset",
    "list_audit",
    "last_audit_for_candidate",
    "preview_overlay_at_t",
    "preview_ascii",
    "preview_summary",
]
