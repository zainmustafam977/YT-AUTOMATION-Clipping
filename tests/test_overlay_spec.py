"""Tests for E-011 OverlaySpec serialization + validation."""
import json
import pytest

from shorts_engine.services.overlay.spec import (
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


def test_serialize_round_trip():
    spec = OverlaySpec(
        name="hello",
        description="test",
        base_w=720,
        base_h=1280,
        layers=[
            OverlayLayer(layer_type=LayerType.TEXT, text="hi", x=0.1, y=0.2, z=1,
                          text_style=TextStyle(size=24, color="#FFFFFF", outline_color="#000000", outline_width=2)),
            OverlayLayer(layer_type=LayerType.SHAPE, x=0.5, y=0.5, z=0,
                          shape=ShapeStyle(shape="rect", width=100, height=100, color="#FF0000", opacity=0.5)),
        ],
    )
    payload = serialize_spec(spec)
    assert isinstance(payload, str)
    spec2 = parse_spec(payload)
    assert spec2.name == "hello"
    assert spec2.base_w == 720
    assert spec2.base_h == 1280
    assert len(spec2.layers) == 2
    assert spec2.layers[0].layer_type == LayerType.TEXT
    assert spec2.layers[0].text == "hi"
    assert spec2.layers[1].layer_type == LayerType.SHAPE


def test_parse_accepts_dict():
    spec = OverlaySpec(name="a")
    d = spec.to_dict()
    s2 = parse_spec(d)
    assert isinstance(s2, OverlaySpec)
    assert s2.name == "a"


def test_validate_ok():
    spec = OverlaySpec(
        name="ok",
        layers=[OverlayLayer(layer_type=LayerType.TEXT, text="x", x=0.5, y=0.5,
                             text_style=TextStyle(color="#FFFFFF", outline_color="#000000"))],
    )
    errs = validate_spec(spec)
    assert errs == []


def test_validate_text_missing():
    spec = OverlaySpec(name="ok", layers=[OverlayLayer(layer_type=LayerType.TEXT)])
    errs = validate_spec(spec)
    assert any("text" in e for e in errs)
    assert any("text_style" in e for e in errs)


def test_validate_bad_color():
    spec = OverlaySpec(name="ok", layers=[OverlayLayer(
        layer_type=LayerType.TEXT, text="x", text_style=TextStyle(color="not-a-color", outline_color="#000000")
    )])
    errs = validate_spec(spec)
    assert any("color" in e for e in errs)


def test_validate_bad_color_short_form():
    spec = OverlaySpec(name="ok", layers=[OverlayLayer(
        layer_type=LayerType.TEXT, text="x", text_style=TextStyle(color="#FFF", outline_color="#000000")
    )])
    errs = validate_spec(spec)
    assert errs == []  # #FFF is valid shorthand


def test_validate_out_of_bounds_xy():
    spec = OverlaySpec(name="ok", layers=[OverlayLayer(layer_type=LayerType.TEXT, text="x", x=1.5, y=0.5,
                                                         text_style=TextStyle(color="#FFF", outline_color="#000"))])
    errs = validate_spec(spec)
    assert any("x" in e for e in errs)


def test_validate_end_before_start():
    spec = OverlaySpec(name="ok", layers=[OverlayLayer(layer_type=LayerType.SHAPE, x=0.5, y=0.5, start=10, end=5,
                                                         shape=ShapeStyle())])
    errs = validate_spec(spec)
    assert any("end" in e and "start" in e for e in errs)


def test_validate_too_many_layers():
    spec = OverlaySpec(name="ok", layers=[OverlayLayer(layer_type=LayerType.SHAPE, x=0, y=0,
                                                         shape=ShapeStyle()) for _ in range(65)])
    errs = validate_spec(spec)
    assert any("max 64" in e for e in errs)


def test_validate_image_path_required():
    spec = OverlaySpec(name="ok", layers=[OverlayLayer(layer_type=LayerType.IMAGE, x=0, y=0)])
    errs = validate_spec(spec)
    assert any("image_path" in e for e in errs)


def test_layer_to_from_dict_round_trip():
    layer = OverlayLayer(layer_type=LayerType.STICKER, x=0.3, y=0.3, z=2,
                         sticker=StickerStyle(kind="star", size=48, color="#FFD700"))
    d = layer.to_dict()
    layer2 = OverlayLayer.from_dict(d)
    assert layer2.layer_type == LayerType.STICKER
    assert layer2.x == 0.3
    assert layer2.sticker.kind == "star"
    assert layer2.sticker.color == "#FFD700"


def test_validate_name_required():
    spec = OverlaySpec(name="", layers=[])
    errs = validate_spec(spec)
    assert any("name" in e for e in errs)


def test_validate_shape_opacity_out_of_range():
    spec = OverlaySpec(name="ok", layers=[OverlayLayer(layer_type=LayerType.SHAPE, x=0, y=0,
                                                         shape=ShapeStyle(opacity=1.5))])
    errs = validate_spec(spec)
    assert any("opacity" in e for e in errs)


def test_validate_sticker_missing_style():
    spec = OverlaySpec(name="ok", layers=[OverlayLayer(layer_type=LayerType.STICKER, x=0, y=0)])
    errs = validate_spec(spec)
    assert any("sticker" in e for e in errs)
