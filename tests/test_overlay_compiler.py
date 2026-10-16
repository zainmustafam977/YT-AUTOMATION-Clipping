"""Tests for E-011 compiler: OverlaySpec -> ffmpeg filter chain."""
import pytest

from shorts_engine.services.overlay.spec import (
    OverlaySpec, OverlayLayer, LayerType, TextStyle, ShapeStyle, StickerStyle,
)
from shorts_engine.services.overlay.compiler import (
    compile_ffmpeg_filter,
    escape_drawtext,
    compile_drawtext_filter,
)


def test_escape_drawtext_basic():
    out = escape_drawtext("hello: world")
    assert "\\:" in out
    assert "hello" in out
    assert "world" in out


def test_escape_drawtext_quotes_and_backslashes():
    out = escape_drawtext("it's a \\ path")
    # Backslash becomes \\
    assert "\\\\" in out
    # Single quote becomes \'
    assert "\\'" in out


def test_escape_drawtext_strips_control_chars():
    out = escape_drawtext("hi\x00there\x07bad")
    assert "\x00" not in out
    assert "\x07" not in out
    assert "hi" in out
    assert "there" in out


def test_compile_text_only():
    spec = OverlaySpec(name="t", base_w=1080, base_h=1920, layers=[
        OverlayLayer(layer_type=LayerType.TEXT, text="Hello", x=0.1, y=0.1, z=1,
                     text_style=TextStyle(size=48, color="#FFFFFF", outline_color="#000000", outline_width=2)),
    ])
    chain, contrasts = compile_ffmpeg_filter(spec)
    assert "drawtext" in chain
    assert "Hello" in chain
    assert "x=108" in chain or "x=108" in chain  # 0.1 * 1080
    assert len(contrasts) == 1
    assert contrasts[0] > 1.0


def test_compile_shape_rect():
    spec = OverlaySpec(name="s", base_w=1080, base_h=1920, layers=[
        OverlayLayer(layer_type=LayerType.SHAPE, x=0.05, y=0.05, z=0,
                     shape=ShapeStyle(shape="rect", width=200, height=100, color="#FF0000", opacity=0.5, border=2)),
    ])
    chain, _ = compile_ffmpeg_filter(spec)
    assert "drawbox" in chain
    assert "#FF0000" in chain


def test_compile_sticker():
    spec = OverlaySpec(name="sk", base_w=1080, base_h=1920, layers=[
        OverlayLayer(layer_type=LayerType.STICKER, x=0.1, y=0.1, z=0,
                     sticker=StickerStyle(kind="star", size=64, color="#FFD700")),
    ])
    chain, _ = compile_ffmpeg_filter(spec)
    assert "drawtext" in chain
    assert "★" in chain


def test_compile_layer_order_by_z():
    spec = OverlaySpec(name="ord", base_w=100, base_h=100, layers=[
        OverlayLayer(layer_type=LayerType.TEXT, text="A", x=0.1, y=0.1, z=2, text_style=TextStyle()),
        OverlayLayer(layer_type=LayerType.TEXT, text="B", x=0.1, y=0.1, z=1, text_style=TextStyle()),
    ])
    chain, _ = compile_ffmpeg_filter(spec)
    pos_a = chain.find("text='A'") if "text='A'" in chain else chain.find("A")
    pos_b = chain.find("text='B'") if "text='B'" in chain else chain.find("B")
    # Both appear in chain; lower-z layer should appear first
    # (z=1 B drawn first, then z=2 A on top).
    # We confirm B's drawtext precedes A's drawtext in the chain string.
    if pos_a != -1 and pos_b != -1:
        assert pos_b < pos_a


def test_compile_with_video_duration_filters_out_of_range():
    spec = OverlaySpec(name="dur", base_w=100, base_h=100, layers=[
        OverlayLayer(layer_type=LayerType.TEXT, text="X", x=0, y=0, z=0, start=10, end=20,
                     text_style=TextStyle()),
        OverlayLayer(layer_type=LayerType.TEXT, text="Y", x=0, y=0, z=0, start=0, end=5,
                     text_style=TextStyle()),
    ])
    # video_duration=6 — should drop X (start=10 > 6) and keep Y (end=5 < 6).
    chain, _ = compile_ffmpeg_filter(spec, video_duration=6.0)
    assert "X" not in chain
    assert "Y" in chain


def test_compile_with_video_duration_clips_end():
    spec = OverlaySpec(name="clip", base_w=100, base_h=100, layers=[
        OverlayLayer(layer_type=LayerType.TEXT, text="long", x=0, y=0, z=0, start=0, end=999,
                     text_style=TextStyle()),
    ])
    chain, _ = compile_ffmpeg_filter(spec, video_duration=5.0)
    assert "enable='between(t,0,5.0)'" in chain


def test_compile_uses_enable_clause():
    spec = OverlaySpec(name="en", base_w=100, base_h=100, layers=[
        OverlayLayer(layer_type=LayerType.TEXT, text="A", x=0, y=0, z=0, start=2, end=4,
                     text_style=TextStyle()),
    ])
    chain, _ = compile_ffmpeg_filter(spec, video_duration=10.0)
    assert "enable=" in chain
    assert "between(t,2,4)" in chain


def test_compile_text_no_text_returns_empty():
    spec = OverlaySpec(name="x", layers=[
        OverlayLayer(layer_type=LayerType.TEXT, x=0, y=0, z=0, text_style=TextStyle()),
    ])
    chain, contrasts = compile_ffmpeg_filter(spec)
    assert chain == ""
    assert contrasts == []


def test_compile_multiple_text_contrasts():
    spec = OverlaySpec(name="mc", base_w=100, base_h=100, layers=[
        OverlayLayer(layer_type=LayerType.TEXT, text="white", x=0, y=0, z=0,
                     text_style=TextStyle(color="#FFFFFF", outline_color="#000")),
        OverlayLayer(layer_type=LayerType.TEXT, text="yellow", x=0, y=0, z=0,
                     text_style=TextStyle(color="#FFFF00", outline_color="#000")),
    ])
    _, contrasts = compile_ffmpeg_filter(spec)
    assert len(contrasts) == 2
    assert all(c >= 1.0 for c in contrasts)


def test_compile_image_overlay():
    spec = OverlaySpec(name="img", base_w=100, base_h=100, layers=[
        OverlayLayer(layer_type=LayerType.IMAGE, x=0.5, y=0.5, z=0, image_path="/tmp/x.png"),
    ])
    chain, _ = compile_ffmpeg_filter(spec)
    assert "overlay" in chain
    assert "[1:v]" in chain
