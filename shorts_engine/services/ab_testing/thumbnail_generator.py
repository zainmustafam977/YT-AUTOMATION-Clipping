"""
Autonomous Multi-Variant Thumbnail Generator for A/B Testing on YouTube Shorts.
Generates 3 visually distinct, high-CTR 9:16 portrait covers:
- Variant A (Hook Impact): Early frame (1.0s) with bold yellow typography and deep black shadow.
- Variant B (Curiosity Peak): Mid-clip frame (40% duration) with electric cyan question typography.
- Variant C (Contrarian Statement): Late-clip frame (70% duration) with stark white text on crimson banner.
100% offline, zero external API costs.
"""

import os
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from PIL import Image, ImageEnhance, ImageDraw, ImageFont

from shorts_engine.services.rendering.thumbnail_generator import ThumbnailGenerator
from shorts_engine.services.execution.watchdog import SubprocessWatchdog

logger = logging.getLogger(__name__)


class ThumbnailVariantGenerator:
    """Generates 3 diverse YouTube Shorts thumbnail variants for Bayesian A/B testing."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = Path(output_dir) if output_dir else None

    @staticmethod
    def _get_font(font_size: int = 72) -> ImageFont.ImageFont:
        font_candidates = [
            "C:/Windows/Fonts/impact.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/segoeuib.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        ]
        for font_path in font_candidates:
            if os.path.exists(font_path):
                try:
                    return ImageFont.truetype(font_path, font_size)
                except Exception:
                    pass
        return ImageFont.load_default()

    @staticmethod
    def _wrap_text(text: str, max_chars_per_line: int = 18, max_lines: int = 3) -> List[str]:
        words = text.strip().upper().split()
        if len(words) > 12:
            words = words[:12]
        lines = []
        cur_line = []
        cur_len = 0
        for w in words:
            if cur_len + len(w) + 1 <= max_chars_per_line:
                cur_line.append(w)
                cur_len += len(w) + 1
            else:
                if cur_line:
                    lines.append(" ".join(cur_line))
                cur_line = [w]
                cur_len = len(w)
        if cur_line:
            lines.append(" ".join(cur_line))
        return lines[:max_lines]

    def generate_variant_a(
        self,
        video_path: Path,
        output_path: Path,
        hook_text: Optional[str] = None,
        timestamp_sec: float = 1.0,
    ) -> Path:
        """Variant A: Hook Impact - High saturation, yellow typography with black drop shadow."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_frame = output_path.with_name(f"temp_a_{output_path.name}")
        extracted = ThumbnailGenerator.extract_frame_ffmpeg(video_path, temp_frame, timestamp_sec=timestamp_sec)
        if not extracted:
            ThumbnailGenerator.extract_frame_ffmpeg(video_path, temp_frame, timestamp_sec=0.5)

        try:
            with Image.open(temp_frame) as img:
                img = img.convert("RGBA")
                if img.size != (1080, 1920):
                    img = img.resize((1080, 1920), Image.Resampling.LANCZOS)
                w, h = 1080, 1920

                # Subtle contrast & color boost
                img = ImageEnhance.Contrast(img).enhance(1.20)
                img = ImageEnhance.Color(img).enhance(1.20)

                # Vignette overlay top and bottom
                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw_ov = ImageDraw.Draw(overlay)
                for y in range(int(h * 0.75), h):
                    alpha = int(150 * ((y - (h * 0.75)) / (h * 0.25)))
                    draw_ov.line([(0, y), (w, y)], fill=(0, 0, 0, min(150, alpha)))
                for y in range(0, int(h * 0.15)):
                    alpha = int(110 * ((int(h * 0.15) - y) / (h * 0.15)))
                    draw_ov.line([(0, y), (w, y)], fill=(0, 0, 0, min(110, alpha)))
                img = Image.alpha_composite(img, overlay)

                if hook_text and hook_text.strip():
                    draw = ImageDraw.Draw(img)
                    font = self._get_font(font_size=74)
                    lines = self._wrap_text(hook_text, max_chars_per_line=16)
                    line_height = int(74 * 1.25)
                    start_y = int(h * 0.38) - ((len(lines) * line_height) // 2)

                    for i, line in enumerate(lines):
                        line_y = start_y + (i * line_height)
                        try:
                            bbox = draw.textbbox((0, 0), line, font=font)
                            line_w = bbox[2] - bbox[0]
                        except Exception:
                            line_w = len(line) * int(74 * 0.55)
                        line_x = max(50, (w - line_w) // 2)

                        # Heavy black drop shadow
                        for dx in (-5, 0, 5):
                            for dy in (-5, 0, 5):
                                draw.text((line_x + dx, line_y + dy), line, font=font, fill=(0, 0, 0, 255))
                        # Vibrant yellow text
                        draw.text((line_x, line_y), line, font=font, fill=(255, 230, 0, 255))

                final_rgb = img.convert("RGB")
                final_rgb.save(output_path, "JPEG", quality=92, optimize=True)
                return output_path
        finally:
            if temp_frame.exists():
                try:
                    temp_frame.unlink()
                except Exception:
                    pass

    def generate_variant_b(
        self,
        video_path: Path,
        output_path: Path,
        hook_text: Optional[str] = None,
        timestamp_sec: float = 3.0,
    ) -> Path:
        """Variant B: Curiosity Peak - Mid-action frame with electric cyan typography and magenta glow."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_frame = output_path.with_name(f"temp_b_{output_path.name}")
        extracted = ThumbnailGenerator.extract_frame_ffmpeg(video_path, temp_frame, timestamp_sec=timestamp_sec)
        if not extracted:
            ThumbnailGenerator.extract_frame_ffmpeg(video_path, temp_frame, timestamp_sec=1.0)

        try:
            with Image.open(temp_frame) as img:
                img = img.convert("RGBA")
                if img.size != (1080, 1920):
                    img = img.resize((1080, 1920), Image.Resampling.LANCZOS)
                w, h = 1080, 1920

                img = ImageEnhance.Contrast(img).enhance(1.15)
                img = ImageEnhance.Color(img).enhance(1.25)

                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw_ov = ImageDraw.Draw(overlay)
                for y in range(int(h * 0.70), h):
                    alpha = int(160 * ((y - (h * 0.70)) / (h * 0.30)))
                    draw_ov.line([(0, y), (w, y)], fill=(0, 0, 0, min(160, alpha)))
                img = Image.alpha_composite(img, overlay)

                if hook_text and hook_text.strip():
                    draw = ImageDraw.Draw(img)
                    font = self._get_font(font_size=72)
                    # Format as question if not already
                    q_text = hook_text.strip()
                    if not q_text.endswith("?"):
                        q_text += "?"
                    lines = self._wrap_text(q_text, max_chars_per_line=16)
                    line_height = int(72 * 1.25)
                    start_y = int(h * 0.40) - ((len(lines) * line_height) // 2)

                    for i, line in enumerate(lines):
                        line_y = start_y + (i * line_height)
                        try:
                            bbox = draw.textbbox((0, 0), line, font=font)
                            line_w = bbox[2] - bbox[0]
                        except Exception:
                            line_w = len(line) * int(72 * 0.55)
                        line_x = max(50, (w - line_w) // 2)

                        # Magenta outline glow
                        for dx in (-4, 0, 4):
                            for dy in (-4, 0, 4):
                                draw.text((line_x + dx, line_y + dy), line, font=font, fill=(128, 0, 128, 255))
                        # Electric Cyan text
                        draw.text((line_x, line_y), line, font=font, fill=(0, 255, 255, 255))

                final_rgb = img.convert("RGB")
                final_rgb.save(output_path, "JPEG", quality=92, optimize=True)
                return output_path
        finally:
            if temp_frame.exists():
                try:
                    temp_frame.unlink()
                except Exception:
                    pass

    def generate_variant_c(
        self,
        video_path: Path,
        output_path: Path,
        hook_text: Optional[str] = None,
        timestamp_sec: float = 6.0,
    ) -> Path:
        """Variant C: Contrarian Punch - Stark white typography with crimson red banner box backing."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_frame = output_path.with_name(f"temp_c_{output_path.name}")
        extracted = ThumbnailGenerator.extract_frame_ffmpeg(video_path, temp_frame, timestamp_sec=timestamp_sec)
        if not extracted:
            ThumbnailGenerator.extract_frame_ffmpeg(video_path, temp_frame, timestamp_sec=1.5)

        try:
            with Image.open(temp_frame) as img:
                img = img.convert("RGBA")
                if img.size != (1080, 1920):
                    img = img.resize((1080, 1920), Image.Resampling.LANCZOS)
                w, h = 1080, 1920

                img = ImageEnhance.Contrast(img).enhance(1.25)

                if hook_text and hook_text.strip():
                    font = self._get_font(font_size=70)
                    lines = self._wrap_text(hook_text, max_chars_per_line=15)
                    line_height = int(70 * 1.30)
                    total_box_h = len(lines) * line_height + 40
                    start_y = int(h * 0.42) - (total_box_h // 2)

                    # Red backing banner overlay
                    banner = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                    draw_b = ImageDraw.Draw(banner)
                    draw_b.rectangle(
                        [(40, start_y - 20), (w - 40, start_y + total_box_h - 20)],
                        fill=(220, 20, 40, 225),  # Crimson Red 88% opacity
                    )
                    img = Image.alpha_composite(img, banner)

                    draw = ImageDraw.Draw(img)
                    for i, line in enumerate(lines):
                        line_y = start_y + (i * line_height)
                        try:
                            bbox = draw.textbbox((0, 0), line, font=font)
                            line_w = bbox[2] - bbox[0]
                        except Exception:
                            line_w = len(line) * int(70 * 0.55)
                        line_x = max(50, (w - line_w) // 2)

                        # Drop shadow
                        draw.text((line_x + 3, line_y + 3), line, font=font, fill=(0, 0, 0, 230))
                        # Pure white bold text
                        draw.text((line_x, line_y), line, font=font, fill=(255, 255, 255, 255))

                final_rgb = img.convert("RGB")
                final_rgb.save(output_path, "JPEG", quality=92, optimize=True)
                return output_path
        finally:
            if temp_frame.exists():
                try:
                    temp_frame.unlink()
                except Exception:
                    pass

    def generate_3_variants(
        self,
        video_path: Path,
        output_dir: Path,
        hook_text: Optional[str] = None,
        duration_sec: float = 30.0,
    ) -> List[Dict[str, Any]]:
        """
        Generates 3 diverse YouTube Shorts thumbnail variants (A, B, C) and saves them to output_dir.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(video_path).stem

        t_a = 1.0
        t_b = max(1.5, min(duration_sec * 0.40, duration_sec - 1.0))
        t_c = max(2.0, min(duration_sec * 0.70, duration_sec - 0.5))

        out_a = output_dir / f"{stem}_var_a.jpg"
        out_b = output_dir / f"{stem}_var_b.jpg"
        out_c = output_dir / f"{stem}_var_c.jpg"

        res_a = self.generate_variant_a(video_path, out_a, hook_text=hook_text, timestamp_sec=t_a)
        res_b = self.generate_variant_b(video_path, out_b, hook_text=hook_text, timestamp_sec=t_b)
        res_c = self.generate_variant_c(video_path, out_c, hook_text=hook_text, timestamp_sec=t_c)

        return [
            {
                "variant_id": "var_a",
                "archetype": "hook_impact",
                "thumbnail_path": str(res_a),
                "timestamp_sec": t_a,
                "label": "Hook Impact (Yellow)",
            },
            {
                "variant_id": "var_b",
                "archetype": "curiosity_peak",
                "thumbnail_path": str(res_b),
                "timestamp_sec": t_b,
                "label": "Curiosity Peak (Cyan)",
            },
            {
                "variant_id": "var_c",
                "archetype": "contrarian_punch",
                "thumbnail_path": str(res_c),
                "timestamp_sec": t_c,
                "label": "Contrarian Punch (Red Banner)",
            },
        ]
