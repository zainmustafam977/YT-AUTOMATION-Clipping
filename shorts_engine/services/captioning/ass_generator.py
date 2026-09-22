"""
Karaoke-style ASS Subtitle Generator: creates styled, word-synchronized vertical captions
optimized for mobile screens and compliant with YouTube Shorts safe-zone guidelines.
"""

from pathlib import Path
from typing import List, Dict, Any, Optional
from shorts_engine.config.settings import settings
from shorts_engine.services.transcription.formatter import WordSegment


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
    ):
        self.font_name = font_name
        self.font_size = font_size
        self.primary_color = primary_color
        self.highlight_color = highlight_color
        self.outline_color = outline_color
        self.outline_width = outline_width
        self.shadow_depth = shadow_depth
        self.margin_vertical = margin_vertical

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
        margin_vert = self.margin_vertical
        words_per_line = max_words_per_line
        case_transform = "none"

        if style_overrides:
            font_name = style_overrides.get("font_name", font_name)
            font_size = int(style_overrides.get("font_size", font_size))
            primary_color = style_overrides.get("primary_color", primary_color)
            highlight_color = style_overrides.get("highlight_color", highlight_color)
            outline_color = style_overrides.get("outline_color", outline_color)
            outline_width = float(style_overrides.get("outline_width", outline_width))
            shadow_depth = float(style_overrides.get("shadow_depth", shadow_depth))
            margin_vert = int(style_overrides.get("margin_vertical", margin_vert))
            words_per_line = int(style_overrides.get("max_words_per_line", words_per_line))
            case_transform = style_overrides.get("case_transform", case_transform)

        content = []
        content.append(self._generate_header(
            font_name=font_name,
            font_size=font_size,
            primary_color=primary_color,
            outline_color=outline_color,
            outline=outline_width,
            shadow=shadow_depth,
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
            chunk_start_sec = max(0.0, chunk[0].start - seg_start_sec)
            chunk_end_sec = max(chunk_start_sec + 0.5, chunk[-1].end - seg_start_sec)

            # For each word in chunk, emit a dialogue slice highlighting the active word
            for active_idx, active_word in enumerate(chunk):
                w_start = max(0.0, active_word.start - seg_start_sec)
                w_end = max(w_start + 0.05, active_word.end - seg_start_sec)

                # Format words with active word highlighted
                line_parts = []
                for idx, w in enumerate(chunk):
                    word_clean = w.word.replace("\\", "")
                    if case_transform == "uppercase":
                        word_clean = word_clean.upper()
                    elif case_transform == "lowercase":
                        word_clean = word_clean.lower()
                    elif case_transform == "capitalize":
                        word_clean = word_clean.capitalize()

                    if idx == active_idx:
                        line_parts.append(f"{{\\c{highlight_color}&}}{word_clean}{{\\c{primary_color}&}}")
                    else:
                        line_parts.append(word_clean)

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
        margin_vertical: Optional[int] = None,
    ) -> str:
        fn = font_name or self.font_name
        fs = font_size or self.font_size
        pc = primary_color or self.primary_color
        oc = outline_color or self.outline_color
        ol = outline if outline is not None else self.outline_width
        sh = shadow if shadow is not None else self.shadow_depth
        mv = margin_vertical if margin_vertical is not None else self.margin_vertical
        return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {settings.TARGET_WIDTH}
PlayResY: {settings.TARGET_HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ShortsStyle,{fn},{fs},{pc},&H000000FF,{oc},&H80000000,-1,0,0,0,100,100,0,0,1,{ol},{sh},2,40,40,{mv},1
"""
