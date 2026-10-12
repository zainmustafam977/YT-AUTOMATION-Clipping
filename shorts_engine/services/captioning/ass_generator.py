"""
Karaoke-style ASS Subtitle Generator: creates styled, word-synchronized vertical captions
optimized for mobile screens and compliant with YouTube Shorts safe-zone guidelines.
"""

import logging
import re
import sys
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Set
from shorts_engine.config.settings import settings
from shorts_engine.services.transcription.formatter import WordSegment

logger = logging.getLogger(__name__)

DEFAULT_BUNDLED_FONT_NAME = "DejaVu Sans"
DEFAULT_BUNDLED_FONT_FILE = settings.ROOT_DIR / "assets" / "fonts" / "DejaVuSans-Bold.ttf"
DEFAULT_EMOJI_FONT = "Segoe UI Emoji" if sys.platform == "win32" else "Noto Color Emoji"

_FONT_CMAP_CACHE: Dict[str, Set[int]] = {}

UNICODE_REPLACEMENTS = {
    "\u201c": '"', "\u201d": '"',  # smart double quotes
    "\u2018": "'", "\u2019": "'",  # smart single quotes / apostrophes
    "\u2014": "-", "\u2013": "-", "\u2015": "-",  # dashes
    "\u2026": "...",  # ellipsis
    "\u00a0": " ", "\u2002": " ", "\u2003": " ", "\u2009": " ",  # spaces
    "\u2022": "*", "\u00b7": "*",  # bullets
    "\u00ab": '"', "\u00bb": '"',  # guillemets
}

DROP_CHARACTERS = {
    "\u200b", "\u200c", "\u200d", "\ufeff",  # zero-width spaces, joiners, BOM
    "\x00", "\x01", "\x02", "\x03", "\x04", "\x05", "\x06", "\x07", "\x08",
    "\x0b", "\x0c", "\x0e", "\x0f",
}


def get_font_supported_codepoints(font_name: Optional[str] = None, font_file: Optional[Path] = None) -> Set[int]:
    """
    Extracts and caches the set of unicode codepoints supported by the bundled font.
    Prefers the bundled project font in assets/fonts to eliminate host system dependencies.
    """
    target = font_file
    if target is None:
        fonts_dir = getattr(settings, "FONTS_DIR", settings.ROOT_DIR / "assets" / "fonts")
        if font_name and ("bold" in font_name.lower()):
            candidate = fonts_dir / "DejaVuSans-Bold.ttf"
        elif font_name:
            candidate = fonts_dir / f"{font_name.replace(' ', '')}.ttf"
            if not candidate.exists():
                candidate = fonts_dir / "DejaVuSans-Bold.ttf"
        else:
            candidate = fonts_dir / "DejaVuSans-Bold.ttf"
        target = candidate if candidate.exists() else DEFAULT_BUNDLED_FONT_FILE

    if not target or not Path(target).exists():
        return set()

    cache_key = str(Path(target).resolve())
    if cache_key in _FONT_CMAP_CACHE:
        return _FONT_CMAP_CACHE[cache_key]

    try:
        from fontTools.ttLib import TTFont
        tt = TTFont(target)
        cmap = tt.getBestCmap()
        codepoints = set(cmap.keys()) if cmap else set()
        _FONT_CMAP_CACHE[cache_key] = codepoints
        return codepoints
    except Exception as e:
        logger.debug("Failed to extract font codepoint table from %s: %s", target, e)
        return set()


def _sanitize_non_emoji_span(text: str, font_codepoints: Optional[Set[int]] = None) -> str:
    clean_chars = []
    for char in text:
        if char in DROP_CHARACTERS:
            continue
        code = ord(char)
        # Always allow standard printable ASCII and characters present in font cmap
        if (font_codepoints and code in font_codepoints) or (32 <= code <= 126):
            clean_chars.append(char)
            continue
        # Common typographic substitution
        if char in UNICODE_REPLACEMENTS:
            clean_chars.append(UNICODE_REPLACEMENTS[char])
            continue
        # NFKD decomposition (strips combining accents to keep base character)
        decomp = unicodedata.normalize("NFKD", char)
        base = "".join(c for c in decomp if not unicodedata.combining(c))
        if base and all((font_codepoints and ord(b) in font_codepoints) or (32 <= ord(b) <= 126) for b in base):
            clean_chars.append(base)
        else:
            clean_chars.append("?")
    return "".join(clean_chars)


def sanitize_word_for_font(word: str, font_codepoints: Optional[Set[int]] = None) -> str:
    """
    Handles characters missing from the font (fallback or safe replacement).
    - Preserves emojis (including flags, variation selectors, and ZWJ sequences)
    - Replaces typographic quotes, dashes, ellipsis with safe ASCII equivalents
    - Normalizes accented / diacritical characters to base ASCII via NFKD decomposition
    - Strips non-printable / zero-width characters
    - Falls back to safe printable '?' for unsupported glyphs
    """
    if not word:
        return word

    if font_codepoints is None:
        font_codepoints = get_font_supported_codepoints()

    # If emojis are present, preserve multi-codepoint emoji spans (flags, ZWJ, variation selectors)
    if not EMOJI_PATTERN.search(word):
        return _sanitize_non_emoji_span(word, font_codepoints)

    parts = []
    last_end = 0
    for match in EMOJI_PATTERN.finditer(word):
        start, end = match.span()
        if start > last_end:
            parts.append(_sanitize_non_emoji_span(word[last_end:start], font_codepoints))
        parts.append(match.group(0))
        last_end = end
    if last_end < len(word):
        parts.append(_sanitize_non_emoji_span(word[last_end:], font_codepoints))
    return "".join(parts)


def escape_ass_dialogue_text(text: str) -> str:
    """
    Escapes ASS override control characters in caption dialogue text:
    - '\\' -> '\\\\' (prevents tag interpretation and command expansion)
    - '{' -> '\\{'  (prevents opening an override tag block)
    - '}' -> '\\}'  (prevents closing an override tag block prematurely)

    Order is critical: backslashes must be escaped FIRST so that the backslashes
    introduced to escape curly braces are not themselves doubled.
    """
    if not text:
        return text
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


EMOJI_PATTERN = re.compile(
    r"("
    r"[\U0001F1E6-\U0001F1FF]{2}"  # Regional Indicator pairs (flags)
    r"|"
    r"[\U0001F300-\U0001FAFF\u2600-\u27BF\u2300-\u23FF\u25A0-\u25FF\u2B00-\u2BFF]"
    r"[\uFE00-\uFE0F]?"            # Optional Variation Selector
    r"(?:\u200D[\U0001F300-\U0001FAFF\u2600-\u27BF\u2300-\u23FF\u25A0-\u25FF\u2B00-\u2BFF][\uFE00-\uFE0F]?)*"  # ZWJ combinations
    r")",
    flags=re.UNICODE,
)


def format_ass_timestamp(seconds: float) -> str:
    """Converts seconds into ASS timestamp format: H:MM:SS.cs (centiseconds)."""
    seconds = max(0.0, seconds)
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis == 100:
        secs += 1
        centis = 0
    return f"{hrs}:{mins:02d}:{secs:02d}.{centis:02d}"


class ASSSubtitleGenerator:
    def __init__(
        self,
        font_name: str = settings.CAPTION_FONT_NAME,
        font_size: int = 56,  # Sized for 1080x1920 portrait canvas
        primary_color: str = "&H00FFFFFF",     # White
        highlight_color: str = "&H0000FFFF",   # Neon Yellow
        outline_color: str = "&H00000000",     # Solid Black
        outline_width: float = 4.0,
        shadow_depth: float = 2.0,
        margin_vertical: int = settings.CAPTION_MARGIN_VERTICAL,
        scale_pop: bool = False,
        active_scale: int = 115,
        animation_style: str = "none",
        secondary_highlight_color: Optional[str] = None,
        emoji_fallback: bool = True,
        emoji_font: Optional[str] = None,
    ):
        self.font_name = font_name
        self.font_size = font_size
        self.primary_color = primary_color
        self.highlight_color = highlight_color
        self.outline_color = outline_color
        self.outline_width = outline_width
        self.shadow_depth = shadow_depth
        self.margin_vertical = margin_vertical
        self.scale_pop = scale_pop
        self.active_scale = active_scale
        self.animation_style = animation_style
        self.secondary_highlight_color = secondary_highlight_color
        self.emoji_fallback = emoji_fallback
        self.emoji_font = emoji_font or DEFAULT_EMOJI_FONT

    def _format_word_emojis(self, text: str, font_name: str) -> str:
        """Wraps emoji sequences in Segoe UI Emoji font tags for Windows ASS rendering."""
        if not self.emoji_fallback or not text:
            return text
        if not EMOJI_PATTERN.search(text):
            return text
        parts = []
        last_end = 0
        for match in EMOJI_PATTERN.finditer(text):
            start, end = match.span()
            if start > last_end:
                parts.append(text[last_end:start])
            emoji_char = match.group(0)
            parts.append(f"{{\\fn{self.emoji_font}}}{emoji_char}{{\\fn{font_name}}}")
            last_end = end
        if last_end < len(text):
            parts.append(text[last_end:])
        return "".join(parts) if parts else text

    def generate_ass(
        self,
        words: List[WordSegment],
        segment_start_ms: int,
        segment_end_ms: int,
        output_path: Path,
        style_overrides: Optional[dict] = None,
        max_words_per_line: int = 4,
    ) -> Path:
        """
        Generates a word-synchronized ASS subtitle file for the segment.
        """
        seg_start_sec = segment_start_ms / 1000.0
        seg_end_sec = segment_end_ms / 1000.0

        # Filter words within segment
        segment_words = [
            w for w in words
            if w.end >= seg_start_sec and w.start <= seg_end_sec
        ]

        if not segment_words:
            # Create empty valid ASS file
            output_path.write_text(self._generate_header(), encoding="utf-8")
            return output_path

        # Apply style overrides dynamically
        font_name = self.font_name
        font_size = self.font_size
        primary_color = self.primary_color
        highlight_color = self.highlight_color
        outline_color = self.outline_color
        outline_width = self.outline_width
        shadow_depth = self.shadow_depth
        shadow_color = "&H80000000"
        background_box = False
        box_color = "&H80000000"
        margin_vert = self.margin_vertical
        words_per_line = max_words_per_line
        case_transform = "none"
        scale_pop = self.scale_pop
        active_scale = self.active_scale
        animation_style = self.animation_style
        secondary_highlight_color = self.secondary_highlight_color

        if style_overrides:
            font_name = style_overrides.get("font_name", font_name)
            font_size = int(style_overrides.get("font_size", font_size))
            primary_color = style_overrides.get("primary_color", primary_color)
            highlight_color = style_overrides.get("highlight_color", highlight_color)
            outline_color = style_overrides.get("outline_color", outline_color)
            outline_width = float(style_overrides.get("outline_width", outline_width))
            shadow_depth = float(style_overrides.get("shadow_depth", shadow_depth))
            shadow_color = style_overrides.get("shadow_color", shadow_color)
            background_box = bool(style_overrides.get("background_box", background_box))
            box_color = style_overrides.get("box_color", box_color)
            margin_vert = int(style_overrides.get("margin_vertical", margin_vert))
            words_per_line = int(style_overrides.get("max_words_per_line", words_per_line))
            case_transform = style_overrides.get("case_transform", case_transform)
            if "scale_pop" in style_overrides:
                scale_pop = bool(style_overrides["scale_pop"])
            if "active_scale" in style_overrides:
                active_scale = int(style_overrides["active_scale"])
            if "animation_style" in style_overrides:
                animation_style = str(style_overrides["animation_style"])
            if "secondary_highlight_color" in style_overrides:
                secondary_highlight_color = style_overrides["secondary_highlight_color"]

        content = []
        content.append(self._generate_header(
            font_name=font_name,
            font_size=font_size,
            primary_color=primary_color,
            outline_color=outline_color,
            outline=outline_width,
            shadow=shadow_depth,
            shadow_color=shadow_color,
            background_box=background_box,
            box_color=box_color,
            margin_vertical=margin_vert,
        ))
        content.append("[Events]")
        content.append("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")

        # Group words into chunks of words_per_line
        chunks = [
            segment_words[i : i + words_per_line]
            for i in range(0, len(segment_words), words_per_line)
        ]

        for chunk in chunks:
            # For each word in chunk, emit a dialogue slice highlighting the active word
            for active_idx, active_word in enumerate(chunk):
                w_start = max(0.0, active_word.start - seg_start_sec)
                w_end = max(w_start + 0.05, active_word.end - seg_start_sec)
                # Bridge minor inter-word pauses within the chunk up to 400ms to eliminate subtitle flickering
                if active_idx < len(chunk) - 1:
                    next_w_start = max(w_start, chunk[active_idx + 1].start - seg_start_sec)
                    if next_w_start > w_end and (next_w_start - w_end) <= 0.4:
                        w_end = next_w_start

                # Format words with active word highlighted
                line_parts = []
                font_codepoints = get_font_supported_codepoints(font_name=font_name)
                for idx, w in enumerate(chunk):
                    # Handle characters missing from font (fallback or safe replacement)
                    word_clean = sanitize_word_for_font(w.word, font_codepoints=font_codepoints)
                    if case_transform == "uppercase":
                        word_clean = word_clean.upper()
                    elif case_transform == "lowercase":
                        word_clean = word_clean.lower()
                    elif case_transform == "capitalize":
                        word_clean = word_clean.capitalize()

                    # Escape '{', '}', and backslash in dialogue text so literal speech never corrupts override blocks
                    word_escaped = escape_ass_dialogue_text(word_clean)
                    word_formatted = self._format_word_emojis(word_escaped, font_name)

                    if idx == active_idx:
                        hl_color = highlight_color
                        if secondary_highlight_color and (active_idx % 2 == 1):
                            hl_color = secondary_highlight_color

                        duration_ms = max(50, int((w_end - w_start) * 1000))
                        duration_cs = max(5, int((w_end - w_start) * 100))

                        if animation_style == "bounce":
                            t1 = min(70, max(20, duration_ms // 3))
                            t2 = min(140, max(40, (duration_ms * 2) // 3))
                            bounce_tag = f"{{\\t(0,{t1},\\fscx{active_scale}\\fscy{active_scale})\\t({t1},{t2},\\fscx105\\fscy105)\\c{hl_color}&}}"
                            line_parts.append(f"{bounce_tag}{word_formatted}{{\\fscx100\\fscy100\\c{primary_color}&}}")
                        elif animation_style == "karaoke":
                            line_parts.append(f"{{\\kf{duration_cs}\\c{hl_color}&}}{word_formatted}{{\\c{primary_color}&}}")
                        elif scale_pop or animation_style == "scale_pop":
                            line_parts.append(f"{{\\fscx{active_scale}\\fscy{active_scale}\\c{hl_color}&}}{word_formatted}{{\\fscx100\\fscy100\\c{primary_color}&}}")
                        else:
                            line_parts.append(f"{{\\c{hl_color}&}}{word_formatted}{{\\c{primary_color}&}}")
                    else:
                        line_parts.append(word_formatted)

                dialogue_text = " ".join(line_parts)
                start_ts = format_ass_timestamp(w_start)
                end_ts = format_ass_timestamp(w_end)

                line = f"Dialogue: 0,{start_ts},{end_ts},ShortsStyle,,0,0,{margin_vert},,{dialogue_text}"
                content.append(line)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(content), encoding="utf-8")
        return output_path

    def _generate_header(
        self,
        font_name: Optional[str] = None,
        font_size: Optional[int] = None,
        primary_color: Optional[str] = None,
        outline_color: Optional[str] = None,
        outline: Optional[float] = None,
        shadow: Optional[float] = None,
        shadow_color: Optional[str] = None,
        background_box: bool = False,
        box_color: Optional[str] = None,
        margin_vertical: Optional[int] = None,
    ) -> str:
        fn = font_name or self.font_name
        fs = font_size or self.font_size
        pc = primary_color or self.primary_color
        oc = outline_color or self.outline_color
        ol = outline if outline is not None else self.outline_width
        sh = shadow if shadow is not None else self.shadow_depth
        mv = margin_vertical if margin_vertical is not None else self.margin_vertical

        border_style = 3 if background_box else 1
        back_colour = (box_color if background_box and box_color else (shadow_color or "&H80000000"))

        return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {settings.TARGET_WIDTH}
PlayResY: {settings.TARGET_HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ShortsStyle,{fn},{fs},{pc},&H000000FF,{oc},{back_colour},-1,0,0,0,100,100,0,0,{border_style},{ol},{sh},2,40,40,{mv},1
"""
