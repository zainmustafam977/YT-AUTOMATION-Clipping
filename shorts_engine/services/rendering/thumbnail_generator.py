"""
Zero-Cost Viral Thumbnail / Cover Generator for YouTube Shorts.
Extracts a high-impact frame from the clip's hook window (0.5s - 3.0s) using FFmpeg,
applies subtle HDR contrast and saturation enhancements, and overlays viral hook typography
safely positioned outside YouTube Shorts UI dead-zones (100% offline, 0ms API cost).
"""

import os
import subprocess
import logging
from pathlib import Path
from typing import Optional, List
from PIL import Image, ImageEnhance, ImageDraw, ImageFont

from shorts_engine.services.execution.watchdog import SubprocessWatchdog

logger = logging.getLogger(__name__)


class class_or_instance_method:
    """Descriptor enabling a method to be invoked as both a classmethod and instancemethod."""
    def __init__(self, func):
        self.func = func

    def __get__(self, instance, owner):
        target = instance if instance is not None else owner
        def wrapper(*args, **kwargs):
            return self.func(target, *args, **kwargs)
        return wrapper


class ThumbnailGenerator:
    """Generates high-CTR 9:16 portrait video covers from rendered clips."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = Path(output_dir) if output_dir else None

    @classmethod
    def extract_frame_ffmpeg(cls, video_path: Path, output_path: Path, timestamp_sec: float = 1.5) -> bool:
        """Extracts a single frame from video at timestamp using FFmpeg."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", str(max(0.2, timestamp_sec)),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            str(output_path),
        ]
        try:
            res = SubprocessWatchdog.run(cmd, timeout_sec=30.0)
            return res.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0
        except Exception as e:
            logger.warning("FFmpeg frame extraction failed at %.2fs: %s", timestamp_sec, e)
            return False

    def _extract_frame_ffmpeg(self, video_path: Path, output_path: Path, timestamp_sec: float = 1.5) -> bool:
        return self.extract_frame_ffmpeg(video_path, output_path, timestamp_sec=timestamp_sec)

    @class_or_instance_method
    def generate_thumbnail(
        self_or_cls,
        video_path: Path,
        output_path: Optional[Path] = None,
        hook_text: Optional[str] = None,
        timestamp_sec: float = 1.5,
    ) -> Path:
        """
        Generates a 1080x1920 YouTube Shorts cover image with optional viral typography.
        """
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        if output_path is None:
            out_dir = getattr(self_or_cls, "output_dir", None) or video_path.parent
            output_path = out_dir / f"{video_path.stem}_thumb.jpg"
        else:
            output_path = Path(output_path)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_frame = output_path.with_name(f"temp_{output_path.name}")
        extracted = self_or_cls.extract_frame_ffmpeg(video_path, temp_frame, timestamp_sec=timestamp_sec)
        if not extracted:
            self_or_cls.extract_frame_ffmpeg(video_path, temp_frame, timestamp_sec=0.5)

        if not temp_frame.exists():
            raise RuntimeError(f"Could not extract frame from {video_path}")

        try:
            with Image.open(temp_frame) as img:
                img = img.convert("RGBA")
                w, h = img.size

                if (w, h) != (1080, 1920):
                    img = img.resize((1080, 1920), Image.Resampling.LANCZOS)
                    w, h = 1080, 1920

                enhancer_con = ImageEnhance.Contrast(img)
                img = enhancer_con.enhance(1.15)
                enhancer_col = ImageEnhance.Color(img)
                img = enhancer_col.enhance(1.15)

                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw_ov = ImageDraw.Draw(overlay)
                for y in range(int(h * 0.78), h):
                    alpha = int(140 * ((y - (h * 0.78)) / (h * 0.22)))
                    draw_ov.line([(0, y), (w, y)], fill=(0, 0, 0, min(140, alpha)))
                for y in range(0, int(h * 0.15)):
                    alpha = int(100 * ((int(h * 0.15) - y) / (h * 0.15)))
                    draw_ov.line([(0, y), (w, y)], fill=(0, 0, 0, min(100, alpha)))

                img = Image.alpha_composite(img, overlay)

                if hook_text and hook_text.strip():
                    draw = ImageDraw.Draw(img)
                    clean_hook = hook_text.strip().upper()
                    words = clean_hook.split()
                    if len(words) > 10:
                        clean_hook = " ".join(words[:10]) + "..."

                    lines: List[str] = []
                    cur_line = []
                    cur_len = 0
                    for word in clean_hook.split():
                        if cur_len + len(word) + 1 <= 18:
                            cur_line.append(word)
                            cur_len += len(word) + 1
                        else:
                            if cur_line:
                                lines.append(" ".join(cur_line))
                            cur_line = [word]
                            cur_len = len(word)
                    if cur_line:
                        lines.append(" ".join(cur_line))
                    lines = lines[:3]

                    font = None
                    font_size = 72
                    for font_path in [
                        "C:/Windows/Fonts/impact.ttf",
                        "C:/Windows/Fonts/arialbd.ttf",
                        "C:/Windows/Fonts/segoeuib.ttf",
                        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    ]:
                        if os.path.exists(font_path):
                            try:
                                font = ImageFont.truetype(font_path, font_size)
                                break
                            except Exception:
                                pass
                    if font is None:
                        font = ImageFont.load_default()

                    line_height = int(font_size * 1.25)
                    total_text_h = len(lines) * line_height
                    start_y = int(h * 0.38) - (total_text_h // 2)

                    for i, line in enumerate(lines):
                        line_y = start_y + (i * line_height)
                        try:
                            bbox = draw.textbbox((0, 0), line, font=font)
                            line_w = bbox[2] - bbox[0]
                        except Exception:
                            line_w = len(line) * int(font_size * 0.55)
                        line_x = max(50, (w - line_w) // 2)

                        shadow_offset = 4
                        for dx in (-shadow_offset, 0, shadow_offset):
                            for dy in (-shadow_offset, 0, shadow_offset):
                                draw.text((line_x + dx, line_y + dy), line, font=font, fill=(0, 0, 0, 255))

                        draw.text((line_x, line_y), line, font=font, fill=(255, 230, 0, 255))

                final_rgb = img.convert("RGB")
                output_path.parent.mkdir(parents=True, exist_ok=True)
                final_rgb.save(output_path, "JPEG", quality=92, optimize=True)
                logger.info("Saved zero-cost viral thumbnail cover to %s", output_path.name)
                return output_path
        finally:
            if temp_frame.exists():
                try:
                    temp_frame.unlink()
                except Exception:
                    pass
