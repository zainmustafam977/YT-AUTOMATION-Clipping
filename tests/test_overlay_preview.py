"""Tests for E-011 preview module."""
import pytest

from shorts_engine.services.overlay.spec import (
    OverlaySpec, OverlayLayer, LayerType, TextStyle, StickerStyle, ShapeStyle,
)
from shorts_engine.services.overlay.preview import (
    preview_overlay_at_t, preview_ascii, preview_summary,
)


def _spec():
    return OverlaySpec(
        name="preview-test",
        base_w=1000,
        base_h=1000,
        layers=[
            OverlayLayer(layer_type=LayerType.TEXT, text="A", x=0.1, y=0.1, z=0, start=0, end=5,
                         text_style=TextStyle(color="#FFFFFF")),
            OverlayLayer(layer_type=LayerType.TEXT, text="B", x=0.5, y=0.5, z=1, start=3, end=10,
                         text_style=TextStyle(color="#FF0000")),
            OverlayLayer(layer_type=LayerType.STICKER, x=0.2, y=0.2, z=0, start=0, end=15,
                         sticker=StickerStyle(kind="star", color="#FFD700")),
            OverlayLayer(layer_type=LayerType.SHAPE, x=0.7, y=0.7, z=0, start=0, end=15,
                         shape=ShapeStyle(shape="rect", width=200, height=100, color="#00FF00")),
        ],
    )


def test_preview_returns_visible_layers_only():
    spec = _spec()
    out = preview_overlay_at_t(spec, t=2.0)  # only A + sticker + shape
    texts = [r for r in out if r["kind"] == "text"]
    assert len(texts) == 1
    assert texts[0]["text"] == "A"


def test_preview_includes_overlapping_layers():
    spec = _spec()
    out = preview_overlay_at_t(spec, t=4.0)  # both A and B + sticker + shape
    texts = sorted(r["text"] for r in out if r["kind"] == "text")
    assert texts == ["A", "B"]


def test_preview_after_end():
    spec = _spec()
    out = preview_overlay_at_t(spec, t=20.0)  # all ended except A's end=5
    assert out == []


def test_preview_pixel_positions():
    spec = _spec()
    out = preview_overlay_at_t(spec, t=2.0, base_w=100, base_h=200)
    for rec in out:
        assert 0 <= rec["x_px"] < 100
        assert 0 <= rec["y_px"] < 200


def test_preview_includes_kind_specific_data():
    spec = _spec()
    out = preview_overlay_at_t(spec, t=4.0)
    kinds = {r["kind"] for r in out}
    assert kinds == {"text", "sticker", "shape"}


def test_preview_ascii_renders():
    spec = _spec()
    grid = preview_ascii(spec, t=4.0, width=20, height=5)
    assert isinstance(grid, str)
    lines = grid.split("\n")
    assert len(lines) == 5
    # At t=4, all four layers are visible (3, 4 in range)
    # T = text, S = sticker, B = shape
    assert "T" in grid or "S" in grid or "B" in grid


def test_preview_summary_counts():
    spec = _spec()
    summary = preview_summary(spec)
    assert summary["name"] == "preview-test"
    assert summary["base"] == "1000x1000"
    assert summary["layer_count"] == 4
    assert summary["layer_types"]["text"] == 2
    assert summary["layer_types"]["sticker"] == 1
    assert summary["layer_types"]["shape"] == 1
    assert summary["earliest_start"] == 0.0
    assert summary["latest_end"] == 15.0


def test_preview_at_t_custom_base_dims():
    spec = OverlaySpec(name="x", base_w=720, base_h=1280, layers=[
        OverlayLayer(layer_type=LayerType.TEXT, text="A", x=0.5, y=0.25, z=0,
                     text_style=TextStyle()),
    ])
    out = preview_overlay_at_t(spec, t=0.0, base_w=1440, base_h=2560)
    # x=0.5 -> 720px in custom dim
    assert any(r["x_px"] == 720 and r["y_px"] == 640 for r in out)


def test_preview_z_order_in_output():
    spec = OverlaySpec(name="z", layers=[
        OverlayLayer(layer_type=LayerType.TEXT, text="A", x=0, y=0, z=2, text_style=TextStyle()),
        OverlayLayer(layer_type=LayerType.TEXT, text="B", x=0, y=0, z=1, text_style=TextStyle()),
    ])
    out = preview_overlay_at_t(spec, t=0.0)
    # z ascending: B (z=1) first, then A (z=2).
    assert out[0]["text"] == "B"
    assert out[1]["text"] == "A"
