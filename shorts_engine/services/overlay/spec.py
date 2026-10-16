"""E-011 — Overlay spec: data model + serialization + validation."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Optional, Tuple


class LayerType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    STICKER = "sticker"
    SHAPE = "shape"


@dataclass
class TextStyle:
    font: str = "DejaVuSans-Bold"
    size: int = 48
    color: str = "#FFFFFF"            # hex
    outline_color: str = "#000000"    # hex, "" to disable
    outline_width: int = 2
    shadow: bool = False


@dataclass
class ImageStyle:
    width: Optional[int] = None       # px; None = original
    height: Optional[int] = None
    opacity: float = 1.0              # 0..1


@dataclass
class StickerStyle:
    kind: str = "star"                # star | heart | arrow | fire | thumbs
    size: int = 64
    color: str = "#FFD700"


@dataclass
class ShapeStyle:
    shape: str = "rect"               # rect | ellipse | line
    width: int = 100
    height: int = 60
    color: str = "#FF0000"
    opacity: float = 0.6              # 0..1
    border: int = 0                   # px
    border_color: str = "#000000"


@dataclass
class OverlayLayer:
    layer_type: LayerType
    start: float = 0.0                # seconds
    end: float = 9999.0               # seconds
    x: float = 0.5                    # fraction of frame width, anchor = top-left of layer
    y: float = 0.5                    # fraction of frame height
    z: int = 0                        # z-order; higher draws on top
    text: Optional[str] = None        # for TEXT
    text_style: Optional[TextStyle] = None
    image_path: Optional[str] = None  # for IMAGE
    image_style: Optional[ImageStyle] = None
    sticker: Optional[StickerStyle] = None
    shape: Optional[ShapeStyle] = None

    def to_dict(self) -> dict:
        d = {
            "layer_type": self.layer_type.value,
            "start": self.start,
            "end": self.end,
            "x": self.x,
            "y": self.y,
            "z": self.z,
        }
        if self.text is not None:
            d["text"] = self.text
        if self.text_style is not None:
            d["text_style"] = asdict(self.text_style)
        if self.image_path is not None:
            d["image_path"] = self.image_path
        if self.image_style is not None:
            d["image_style"] = asdict(self.image_style)
        if self.sticker is not None:
            d["sticker"] = asdict(self.sticker)
        if self.shape is not None:
            d["shape"] = asdict(self.shape)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "OverlayLayer":
        ts = None
        if "text_style" in d and d["text_style"] is not None:
            ts = TextStyle(**d["text_style"])
        ist = None
        if "image_style" in d and d["image_style"] is not None:
            ist = ImageStyle(**d["image_style"])
        st = None
        if "sticker" in d and d["sticker"] is not None:
            st = StickerStyle(**d["sticker"])
        sh = None
        if "shape" in d and d["shape"] is not None:
            sh = ShapeStyle(**d["shape"])
        return cls(
            layer_type=LayerType(d["layer_type"]),
            start=float(d.get("start", 0.0)),
            end=float(d.get("end", 9999.0)),
            x=float(d.get("x", 0.5)),
            y=float(d.get("y", 0.5)),
            z=int(d.get("z", 0)),
            text=d.get("text"),
            text_style=ts,
            image_path=d.get("image_path"),
            image_style=ist,
            sticker=st,
            shape=sh,
        )


@dataclass
class OverlaySpec:
    name: str = "untitled"
    description: str = ""
    base_w: int = 1080
    base_h: int = 1920
    layers: List[OverlayLayer] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "base_w": self.base_w,
            "base_h": self.base_h,
            "layers": [layer.to_dict() for layer in self.layers],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "OverlaySpec":
        return cls(
            name=str(d.get("name", "untitled")),
            description=str(d.get("description", "")),
            base_w=int(d.get("base_w", 1080)),
            base_h=int(d.get("base_h", 1920)),
            layers=[OverlayLayer.from_dict(ld) for ld in d.get("layers", [])],
        )


def serialize_spec(spec: OverlaySpec) -> str:
    return json.dumps(spec.to_dict(), sort_keys=True, separators=(",", ":"))


def parse_spec(payload) -> OverlaySpec:
    """Accepts a dict, JSON string, or OverlaySpec; returns OverlaySpec."""
    if isinstance(payload, OverlaySpec):
        return payload
    if isinstance(payload, str):
        return OverlaySpec.from_dict(json.loads(payload))
    if isinstance(payload, dict):
        return OverlaySpec.from_dict(payload)
    raise TypeError(f"parse_spec: unsupported type {type(payload)!r}")


_HEX_COLOR_RE = None


def _hex_color(c: str) -> str:
    global _HEX_COLOR_RE
    if not isinstance(c, str):
        raise ValueError(f"color must be str, got {type(c).__name__}")
    s = c.strip()
    if not s.startswith("#") or len(s) not in (4, 7):
        raise ValueError(f"color must be #RGB or #RRGGBB, got {c!r}")
    if len(s) == 4:
        # #abc -> #aabbcc
        s = "#" + "".join(ch * 2 for ch in s[1:])
    return s.upper()


def validate_spec(spec: OverlaySpec) -> List[str]:
    """Returns a list of human-readable errors. Empty list = valid."""
    errs: List[str] = []
    if not spec.name or not spec.name.strip():
        errs.append("name is required")
    if spec.base_w <= 0 or spec.base_h <= 0:
        errs.append(f"base dimensions must be positive, got {spec.base_w}x{spec.base_h}")
    if len(spec.layers) > 64:
        errs.append(f"too many layers ({len(spec.layers)}), max 64")
    for i, layer in enumerate(spec.layers):
        if layer.end < layer.start:
            errs.append(f"layer[{i}]: end ({layer.end}) < start ({layer.start})")
        if not 0.0 <= layer.x <= 1.0:
            errs.append(f"layer[{i}]: x out of [0,1]: {layer.x}")
        if not 0.0 <= layer.y <= 1.0:
            errs.append(f"layer[{i}]: y out of [0,1]: {layer.y}")
        if layer.layer_type == LayerType.TEXT:
            if not layer.text or not layer.text.strip():
                errs.append(f"layer[{i}]: text is required for TEXT type")
            if layer.text_style is None:
                errs.append(f"layer[{i}]: text_style is required for TEXT type")
            else:
                try:
                    _hex_color(layer.text_style.color)
                    _hex_color(layer.text_style.outline_color)
                except ValueError as e:
                    errs.append(f"layer[{i}]: text_style color invalid: {e}")
        elif layer.layer_type == LayerType.IMAGE:
            if not layer.image_path:
                errs.append(f"layer[{i}]: image_path is required for IMAGE type")
        elif layer.layer_type == LayerType.STICKER:
            if layer.sticker is None:
                errs.append(f"layer[{i}]: sticker style is required for STICKER type")
            else:
                try:
                    _hex_color(layer.sticker.color)
                except ValueError as e:
                    errs.append(f"layer[{i}]: sticker color invalid: {e}")
        elif layer.layer_type == LayerType.SHAPE:
            if layer.shape is None:
                errs.append(f"layer[{i}]: shape style is required for SHAPE type")
            else:
                try:
                    _hex_color(layer.shape.color)
                    _hex_color(layer.shape.border_color)
                except ValueError as e:
                    errs.append(f"layer[{i}]: shape color invalid: {e}")
                if not 0.0 <= layer.shape.opacity <= 1.0:
                    errs.append(f"layer[{i}]: shape opacity must be in [0,1]")
    return errs
