"""
Caption Style Template Engine: provides declarative presets, color conversions,
and custom template customization for YouTube Shorts subtitles.
"""

import re
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

from shorts_engine.config.settings import settings


def hex_to_ass_color(hex_str: str) -> str:
    """
    Converts standard HTML/CSS hex color (#RRGGBB or #RGB) into ASS subtitle format: &H00BBGGRR.
    If already in ASS format (&H... or &H00...), validates and returns directly.
    """
    if not hex_str:
        return "&H00FFFFFF"
    clean = hex_str.strip()
    if clean.startswith("&H") or clean.startswith("&h"):
        return clean.upper()

    clean = clean.lstrip("#")
    if len(clean) == 3:
        clean = "".join(c * 2 for c in clean)
    if len(clean) == 6:
        r = clean[0:2].upper()
        g = clean[2:4].upper()
        b = clean[4:6].upper()
        return f"&H00{b}{g}{r}"
    elif len(clean) == 8:
        # #AARRGGBB -> &HAABBGGRR
        a = clean[0:2].upper()
        r = clean[2:4].upper()
        g = clean[4:6].upper()
        b = clean[6:8].upper()
        return f"&H{a}{b}{g}{r}"
    return "&H00FFFFFF"


def ass_to_hex_color(ass_str: str) -> str:
    """Converts ASS color format (&H00BBGGRR) to HTML hex format (#RRGGBB)."""
    if not ass_str or not ass_str.startswith("&H"):
        return "#FFFFFF"
    clean = ass_str[2:].zfill(8)
    # format: AABBGGRR
    b = clean[2:4]
    g = clean[4:6]
    r = clean[6:8]
    return f"#{r}{g}{b}".lower()


class CaptionStyleTemplate(BaseModel):
    template_id: str
    name: str
    description: str
    font_name: str = "Arial Black"
    font_size: int = 56
    primary_color: str = "&H00FFFFFF"     # White
    highlight_color: str = "&H0000FFFF"   # Yellow
    outline_color: str = "&H00000000"     # Black
    outline_width: float = 4.0
    shadow_depth: float = 2.0
    margin_vertical: int = 420            # Baseline margin from bottom
    max_words_per_line: int = 4
    case_transform: str = "uppercase"     # uppercase, lowercase, capitalize, none

    def to_ass_overrides(self) -> Dict[str, Any]:
        """Maps template properties directly to ASSSubtitleGenerator override keys."""
        return {
            "font_name": self.font_name,
            "font_size": self.font_size,
            "primary_color": hex_to_ass_color(self.primary_color),
            "highlight_color": hex_to_ass_color(self.highlight_color),
            "outline_color": hex_to_ass_color(self.outline_color),
            "outline_width": self.outline_width,
            "shadow_depth": self.shadow_depth,
            "margin_vertical": self.margin_vertical,
            "max_words_per_line": self.max_words_per_line,
            "case_transform": self.case_transform,
        }

    def to_ui_dict(self) -> Dict[str, Any]:
        """Provides HTML/CSS-friendly representation for the dashboard UI."""
        return {
            "template_id": self.template_id,
            "name": self.name,
            "description": self.description,
            "font_name": self.font_name,
            "font_size": self.font_size,
            "primary_hex": ass_to_hex_color(self.primary_color),
            "highlight_hex": ass_to_hex_color(self.highlight_color),
            "outline_hex": ass_to_hex_color(self.outline_color),
            "outline_width": self.outline_width,
            "shadow_depth": self.shadow_depth,
            "margin_vertical": self.margin_vertical,
            "max_words_per_line": self.max_words_per_line,
            "case_transform": self.case_transform,
        }


# Curated Built-in Preset Library
BUILTIN_TEMPLATES: Dict[str, CaptionStyleTemplate] = {
    "hormozi": CaptionStyleTemplate(
        template_id="hormozi",
        name="Alex Hormozi Viral",
        description="High-converting viral style: uppercase bold text with neon yellow active word highlight and heavy black outline.",
        font_name="Arial Black",
        font_size=58,
        primary_color="&H00FFFFFF",
        highlight_color="&H0000FFFF",  # Neon Yellow
        outline_color="&H00000000",
        outline_width=4.8,
        shadow_depth=2.5,
        margin_vertical=settings.CAPTION_MARGIN_VERTICAL,
        max_words_per_line=3,
        case_transform="uppercase",
    ),
    "mrbeast": CaptionStyleTemplate(
        template_id="mrbeast",
        name="MrBeast Impact",
        description="Energetic YouTube gaming/challenge style: ultra-heavy typeface with electric cyan highlight and sharp contrast.",
        font_name="Arial Black",
        font_size=60,
        primary_color="&H00FFFFFF",
        highlight_color="&H00FFFF00",  # Neon Cyan
        outline_color="&H00000000",
        outline_width=5.2,
        shadow_depth=3.0,
        margin_vertical=settings.CAPTION_MARGIN_VERTICAL,
        max_words_per_line=3,
        case_transform="uppercase",
    ),
    "neon": CaptionStyleTemplate(
        template_id="neon",
        name="Cyberpunk Neon",
        description="Futuristic aesthetic: vibrant green active words against magenta outline glow, ideal for tech and gaming channels.",
        font_name="Arial Black",
        font_size=56,
        primary_color="&H00FFFFFF",
        highlight_color="&H0000FF7F",  # Spring Neon Green
        outline_color="&H00800080",    # Deep Purple/Magenta
        outline_width=4.0,
        shadow_depth=3.5,
        margin_vertical=settings.CAPTION_MARGIN_VERTICAL,
        max_words_per_line=4,
        case_transform="uppercase",
    ),
    "minimalist": CaptionStyleTemplate(
        template_id="minimalist",
        name="Minimalist Editorial",
        description="Clean, understated luxury typography: off-white typography with subtle drop shadow, perfect for podcast/documentary content.",
        font_name="Arial",
        font_size=50,
        primary_color="&H00F0F0F0",
        highlight_color="&H00E0A838",  # Subtle Gold/Amber
        outline_color="&H00111111",
        outline_width=2.5,
        shadow_depth=1.5,
        margin_vertical=settings.CAPTION_MARGIN_VERTICAL,
        max_words_per_line=5,
        case_transform="none",
    ),
    "high_contrast_box": CaptionStyleTemplate(
        template_id="high_contrast_box",
        name="High Contrast Box",
        description="Maximum readability on busy footage: solid black backing with pure white and punchy yellow highlight.",
        font_name="Arial Black",
        font_size=54,
        primary_color="&H00FFFFFF",
        highlight_color="&H0000E5FF",  # Amber Yellow
        outline_color="&H00000000",
        outline_width=6.0,
        shadow_depth=0.0,
        margin_vertical=settings.CAPTION_MARGIN_VERTICAL,
        max_words_per_line=3,
        case_transform="uppercase",
    ),
}


def get_caption_template(preset_or_id: Optional[str] = None) -> CaptionStyleTemplate:
    """Returns the requested template or falls back to the default preset."""
    key = (preset_or_id or settings.DEFAULT_CAPTION_PRESET or "hormozi").lower().strip()
    return BUILTIN_TEMPLATES.get(key, BUILTIN_TEMPLATES["hormozi"])


def list_caption_templates() -> List[Dict[str, Any]]:
    """Returns a list of all available templates formatted for the dashboard UI."""
    return [t.to_ui_dict() for t in BUILTIN_TEMPLATES.values()]


def parse_custom_template(data: Dict[str, Any]) -> CaptionStyleTemplate:
    """Parses custom operator input or JSON payload into a validated CaptionStyleTemplate."""
    p_color = hex_to_ass_color(data.get("primary_color") or data.get("primary_hex") or "&H00FFFFFF")
    h_color = hex_to_ass_color(data.get("highlight_color") or data.get("highlight_hex") or "&H0000FFFF")
    o_color = hex_to_ass_color(data.get("outline_color") or data.get("outline_hex") or "&H00000000")

    return CaptionStyleTemplate(
        template_id=data.get("template_id") or "custom",
        name=data.get("name") or "Custom Style",
        description=data.get("description") or "Operator customized caption template",
        font_name=data.get("font_name") or "Arial Black",
        font_size=int(data.get("font_size", 56)),
        primary_color=p_color,
        highlight_color=h_color,
        outline_color=o_color,
        outline_width=float(data.get("outline_width", 4.0)),
        shadow_depth=float(data.get("shadow_depth", 2.0)),
        margin_vertical=int(data.get("margin_vertical", settings.CAPTION_MARGIN_VERTICAL)),
        max_words_per_line=int(data.get("max_words_per_line", 4)),
        case_transform=data.get("case_transform", "uppercase"),
    )
