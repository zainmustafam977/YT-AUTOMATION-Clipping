"""
Caption Style Template Engine: provides declarative presets, color conversions,
and custom template customization for YouTube Shorts subtitles.
"""

from typing import Dict, Any, List, Optional
from pydantic import BaseModel

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
    if not ass_str:
        return "#ffffff"
    clean_str = ass_str.strip()
    if not (clean_str.startswith("&H") or clean_str.startswith("&h")):
        return "#ffffff"
    clean = clean_str[2:].zfill(8)
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
    shadow_color: str = "&H80000000"
    background_box: bool = False
    box_color: str = "&H80000000"
    margin_vertical: int = 420            # Baseline margin from bottom
    max_words_per_line: int = 4
    case_transform: str = "uppercase"     # uppercase, lowercase, capitalize, none
    scale_pop: bool = True
    active_scale: int = 115
    animation_style: str = "none"         # none, scale_pop, bounce, karaoke
    secondary_highlight_color: Optional[str] = None

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
            "shadow_color": hex_to_ass_color(self.shadow_color),
            "background_box": self.background_box,
            "box_color": hex_to_ass_color(self.box_color),
            "margin_vertical": self.margin_vertical,
            "max_words_per_line": self.max_words_per_line,
            "case_transform": self.case_transform,
            "scale_pop": self.scale_pop,
            "active_scale": self.active_scale,
            "animation_style": self.animation_style,
            "secondary_highlight_color": hex_to_ass_color(self.secondary_highlight_color) if self.secondary_highlight_color else None,
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
            "shadow_hex": ass_to_hex_color(self.shadow_color),
            "background_box": self.background_box,
            "box_hex": ass_to_hex_color(self.box_color),
            "margin_vertical": self.margin_vertical,
            "max_words_per_line": self.max_words_per_line,
            "case_transform": self.case_transform,
            "scale_pop": self.scale_pop,
            "active_scale": self.active_scale,
            "animation_style": self.animation_style,
            "secondary_highlight_hex": ass_to_hex_color(self.secondary_highlight_color) if self.secondary_highlight_color else None,
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
        outline_width=2.0,
        shadow_depth=0.0,
        background_box=True,
        box_color="&H80000000",
        margin_vertical=settings.CAPTION_MARGIN_VERTICAL,
        max_words_per_line=3,
        case_transform="uppercase",
    ),
    "hormozi_bounce": CaptionStyleTemplate(
        template_id="hormozi_bounce",
        name="Alex Hormozi Bounce",
        description="Viral kinetic typography: uppercase bold text with dynamic pop-in bounce animation, neon yellow highlight, and heavy outline.",
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
        scale_pop=True,
        animation_style="bounce",
        active_scale=125,
    ),
    "neon_punch": CaptionStyleTemplate(
        template_id="neon_punch",
        name="Neon Punch Glow",
        description="High-energy cyberpunk aesthetic: electric spring green highlight with magenta glow and active scale bounce.",
        font_name="Arial Black",
        font_size=56,
        primary_color="&H00FFFFFF",
        highlight_color="&H0000FF7F",  # Spring Neon Green
        outline_color="&H00800080",    # Deep Purple/Magenta
        outline_width=4.5,
        shadow_depth=3.5,
        margin_vertical=settings.CAPTION_MARGIN_VERTICAL,
        max_words_per_line=3,
        case_transform="uppercase",
        scale_pop=True,
        animation_style="bounce",
        active_scale=120,
    ),
    "karaoke_sweep": CaptionStyleTemplate(
        template_id="karaoke_sweep",
        name="Karaoke Fluid Sweep",
        description="Smooth fluid karaoke-style color sweep across spoken words with electric cyan highlight.",
        font_name="Arial Black",
        font_size=56,
        primary_color="&H00FFFFFF",
        highlight_color="&H00FFFF00",  # Neon Cyan
        outline_color="&H00000000",
        outline_width=4.0,
        shadow_depth=2.0,
        margin_vertical=settings.CAPTION_MARGIN_VERTICAL,
        max_words_per_line=4,
        case_transform="uppercase",
        scale_pop=False,
        animation_style="karaoke",
    ),
    "minimal_clean": CaptionStyleTemplate(
        template_id="minimal_clean",
        name="Minimalist Editorial Clean",
        description="Clean understated luxury typography: off-white with warm gold highlight and soft shadow.",
        font_name="Arial",
        font_size=52,
        primary_color="&H00F0F0F0",
        highlight_color="&H00E0A838",  # Amber / Gold
        outline_color="&H00111111",
        outline_width=2.5,
        shadow_depth=1.5,
        margin_vertical=settings.CAPTION_MARGIN_VERTICAL,
        max_words_per_line=4,
        case_transform="none",
        scale_pop=False,
        animation_style="none",
    ),
}

TEMPLATE_ALIASES: Dict[str, str] = {
    "cyberpunk": "neon",
    "high_contrast": "high_contrast_box",
    "hormozi_animated": "hormozi_bounce",
    "minimal": "minimal_clean",
    "karaoke": "karaoke_sweep",
}


def get_caption_template(preset_or_id: Optional[str] = None, db: Optional[Any] = None) -> CaptionStyleTemplate:
    """Returns the requested template or falls back to the default preset."""
    raw_key = (preset_or_id or settings.DEFAULT_CAPTION_PRESET or "hormozi").strip()
    key = raw_key.lower()

    # Direct alias resolution
    resolved_key = TEMPLATE_ALIASES.get(key, key)
    if resolved_key in BUILTIN_TEMPLATES:
        return BUILTIN_TEMPLATES[resolved_key]

    # Try DB lookup if session provided
    if db is not None:
        try:
            from shorts_engine.db.models import CaptionPreset
            preset = db.query(CaptionPreset).filter(
                (CaptionPreset.id == raw_key) | (CaptionPreset.name.ilike(raw_key))
            ).first()
            if preset:
                return CaptionStyleTemplate(
                    template_id=preset.id,
                    name=preset.name,
                    description=preset.description or "",
                    font_name=preset.font_family,
                    font_size=preset.font_size,
                    primary_color=preset.primary_color,
                    highlight_color=preset.highlight_color,
                    outline_color=preset.outline_color,
                    outline_width=preset.outline_width,
                    shadow_depth=preset.shadow_depth,
                    shadow_color=preset.shadow_color,
                    background_box=preset.background_box,
                    box_color=preset.box_color,
                    margin_vertical=preset.margin_vertical,
                    max_words_per_line=preset.max_words_per_line,
                    case_transform=preset.case_transform,
                    scale_pop=preset.scale_pop,
                    active_scale=preset.active_scale,
                )
        except Exception:
            pass

    return BUILTIN_TEMPLATES["hormozi"]


def list_caption_templates(db: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Returns a list of all available templates (builtins + custom DB presets) formatted for the UI."""
    results = [t.to_ui_dict() for t in BUILTIN_TEMPLATES.values()]
    if db is not None:
        try:
            from shorts_engine.db.models import CaptionPreset
            custom_presets = db.query(CaptionPreset).filter(CaptionPreset.is_builtin.is_(False)).all()
            for cp in custom_presets:
                results.append(cp.to_dict())
        except Exception:
            pass
    return results


def parse_custom_template(data: Dict[str, Any]) -> CaptionStyleTemplate:
    """Parses custom operator input or JSON payload into a validated CaptionStyleTemplate."""
    p_color = hex_to_ass_color(data.get("primary_color") or data.get("primary_hex") or "&H00FFFFFF")
    h_color = hex_to_ass_color(data.get("highlight_color") or data.get("highlight_hex") or "&H0000FFFF")
    o_color = hex_to_ass_color(data.get("outline_color") or data.get("outline_hex") or "&H00000000")
    s_color = hex_to_ass_color(data.get("shadow_color") or data.get("shadow_hex") or "&H80000000")
    b_color = hex_to_ass_color(data.get("box_color") or data.get("box_hex") or "&H80000000")
    bg_box = bool(data.get("background_box", False) or data.get("box_enabled", False))
    scale_pop = bool(data.get("scale_pop", True))
    active_scale = int(data.get("active_scale", 115))

    return CaptionStyleTemplate(
        template_id=data.get("template_id") or "custom",
        name=data.get("name") or "Custom Style",
        description=data.get("description") or "Operator customized caption template",
        font_name=data.get("font_name") or data.get("font_family") or "Arial Black",
        font_size=int(data.get("font_size", 56)),
        primary_color=p_color,
        highlight_color=h_color,
        outline_color=o_color,
        outline_width=float(data.get("outline_width", 4.0)),
        shadow_depth=float(data.get("shadow_depth", 2.0)),
        shadow_color=s_color,
        background_box=bg_box,
        box_color=b_color,
        margin_vertical=int(data.get("margin_vertical", settings.CAPTION_MARGIN_VERTICAL)),
        max_words_per_line=int(data.get("max_words_per_line", 4)),
        case_transform=data.get("case_transform", "uppercase"),
        scale_pop=scale_pop,
        active_scale=active_scale,
    )


def save_preset_to_db(db: Any, data: Dict[str, Any]) -> Any:
    """Creates or updates a custom CaptionPreset in the database."""
    from shorts_engine.db.models import CaptionPreset

    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("Caption preset name is required")

    reserved = set(BUILTIN_TEMPLATES.keys()) | set(TEMPLATE_ALIASES.keys())
    if name.lower() in reserved:
        raise ValueError(f"'{name}' is a reserved built-in preset identifier")

    existing = db.query(CaptionPreset).filter(CaptionPreset.name == name).first()
    if not existing and data.get("id"):
        existing = db.query(CaptionPreset).filter(CaptionPreset.id == data["id"]).first()

    p_color = hex_to_ass_color(data.get("primary_color") or data.get("primary_hex") or "&H00FFFFFF")
    h_color = hex_to_ass_color(data.get("highlight_color") or data.get("highlight_hex") or "&H0000FFFF")
    o_color = hex_to_ass_color(data.get("outline_color") or data.get("outline_hex") or "&H00000000")
    s_color = hex_to_ass_color(data.get("shadow_color") or data.get("shadow_hex") or "&H80000000")
    b_color = hex_to_ass_color(data.get("box_color") or data.get("box_hex") or "&H80000000")
    bg_box = bool(data.get("background_box", False) or data.get("box_enabled", False))

    if existing:
        existing.description = data.get("description", existing.description)
        existing.font_family = data.get("font_family") or data.get("font_name", existing.font_family)
        existing.font_size = int(data.get("font_size", existing.font_size))
        existing.primary_color = p_color
        existing.highlight_color = h_color
        existing.outline_color = o_color
        existing.outline_width = float(data.get("outline_width", existing.outline_width))
        existing.shadow_depth = float(data.get("shadow_depth", existing.shadow_depth))
        existing.shadow_color = s_color
        existing.background_box = bg_box
        existing.box_color = b_color
        existing.margin_vertical = int(data.get("margin_vertical", existing.margin_vertical))
        existing.max_words_per_line = int(data.get("max_words_per_line", existing.max_words_per_line))
        existing.case_transform = data.get("case_transform", existing.case_transform)
        existing.scale_pop = bool(data.get("scale_pop", existing.scale_pop))
        existing.active_scale = int(data.get("active_scale", existing.active_scale))
        db.commit()
        db.refresh(existing)
        return existing
    else:
        preset = CaptionPreset(
            name=name,
            description=data.get("description", ""),
            font_family=data.get("font_family") or data.get("font_name", "Arial Black"),
            font_size=int(data.get("font_size", 56)),
            primary_color=p_color,
            highlight_color=h_color,
            outline_color=o_color,
            outline_width=float(data.get("outline_width", 4.0)),
            shadow_depth=float(data.get("shadow_depth", 2.0)),
            shadow_color=s_color,
            background_box=bg_box,
            box_color=b_color,
            margin_vertical=int(data.get("margin_vertical", settings.CAPTION_MARGIN_VERTICAL)),
            max_words_per_line=int(data.get("max_words_per_line", 4)),
            case_transform=data.get("case_transform", "uppercase"),
            scale_pop=bool(data.get("scale_pop", True)),
            active_scale=int(data.get("active_scale", 115)),
            is_builtin=False,
        )
        db.add(preset)
        db.commit()
        db.refresh(preset)
        return preset


def delete_preset_from_db(db: Any, preset_id: str) -> bool:
    """Deletes a custom preset from the database. Builtins cannot be deleted."""
    from shorts_engine.db.models import CaptionPreset
    preset = db.query(CaptionPreset).filter(CaptionPreset.id == preset_id).first()
    if not preset or preset.is_builtin:
        return False
    db.delete(preset)
    db.commit()
    return True

