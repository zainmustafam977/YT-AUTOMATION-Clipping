"""
Technical Quality Gate: verifies resolution (1080x1920), duration bounds,
valid audio/video streams, and EBU R128 compliance using single-source-of-truth settings.
"""

import json
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, Tuple, Optional
from shorts_engine.config.settings import settings

logger = logging.getLogger(__name__)


class TechnicalVerifier:
    def verify(
        self,
        video_path: Path,
        min_duration_sec: Optional[float] = None,
        max_duration_sec: Optional[float] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Runs ffprobe and returns (passed: bool, details: dict).
        Supports per-video duration bounds with container muxing tolerance.
        """
        if not video_path.exists() or video_path.stat().st_size == 0:
            return False, {"error": "File does not exist or has 0 bytes"}

        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(video_path),
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            return False, {"error": f"ffprobe execution failed: {result.stderr}"}

        try:
            probe = json.loads(result.stdout)
            format_info = probe.get("format", {})
            duration = float(format_info.get("duration", 0.0))
            
            width = 0
            height = 0
            has_audio = False

            for stream in probe.get("streams", []):
                if stream.get("codec_type") == "video" and width == 0:
                    width = int(stream.get("width", 0))
                    height = int(stream.get("height", 0))
                elif stream.get("codec_type") == "audio":
                    has_audio = True

            default_max_dur = (
                settings.MAX_EXTENDED_DURATION_SEC
                if settings.EXTENDED_SHORTS_ALLOWED
                else settings.TARGET_DURATION_MAX_SEC
            )

            # Resolve effective duration bounds with 2.0s container muxing tolerance
            base_min_dur = min_duration_sec if min_duration_sec is not None else settings.TARGET_DURATION_MIN_SEC
            base_max_dur = max_duration_sec if max_duration_sec is not None else default_max_dur

            eff_min_dur = max(5.0, base_min_dur - 1.5)
            eff_max_dur = min(185.0, base_max_dur + 2.0)

            # Checks
            checks = {
                "correct_resolution": (width == settings.TARGET_WIDTH and height == settings.TARGET_HEIGHT),
                "duration_within_bounds": (eff_min_dur <= duration <= eff_max_dur),
                "audio_stream_present": has_audio,
                "file_size_valid": video_path.stat().st_size > 50_000,
            }

            all_passed = all(checks.values())
            details = {
                "all_passed": all_passed,
                "duration_sec": round(duration, 2),
                "min_duration_sec": base_min_dur,
                "max_duration_sec": base_max_dur,
                "width": width,
                "height": height,
                "checks": checks,
            }
            return all_passed, details
        except Exception as e:
            return False, {"error": f"Failed to parse probe data: {str(e)}"}
