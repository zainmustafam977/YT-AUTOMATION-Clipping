"""
Rendering Service: FFmpeg compositor that crops to 9:16 portrait, burns in karaoke ASS subtitles,
normalizes audio to EBU R128 standards, ducks background audio, and validates the final MP4.
"""

import os
import json
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from shorts_engine.config.settings import settings
from shorts_engine.services.reframing.cropper import CropPlan
from shorts_engine.services.audio.mastering import AudioMasteringEngine
from shorts_engine.services.execution.watchdog import SubprocessWatchdog

logger = logging.getLogger(__name__)


def escape_ffmpeg_filter_path(path: Path) -> str:
    """
    Escapes a filesystem path for inclusion in FFmpeg filter graph parameters.
    Converts backslashes to forward slashes, escapes colons for Windows paths,
    and escapes single quotes.
    """
    path_str = str(path.resolve()).replace("\\", "/")
    # Escape colon (e.g. D:/ -> D\\:/)
    path_str = path_str.replace(":", "\\:")
    # Escape single quotes
    path_str = path_str.replace("'", "'\\\\''")
    return path_str


def get_default_fontfile() -> Optional[str]:
    """Finds a reliable system font path for drawtext on Windows/Linux."""
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
        Path("/System/Library/Fonts/Helvetica.ttc"),
    ]
    for c in candidates:
        if c.exists():
            return escape_ffmpeg_filter_path(c)
    return None



class RenderValidationResult:
    def __init__(
        self,
        is_valid: bool,
        duration_sec: float,
        width: int,
        height: int,
        has_audio: bool,
        file_size_bytes: int,
        error: Optional[str] = None,
    ):
        self.is_valid = is_valid
        self.duration_sec = duration_sec
        self.width = width
        self.height = height
        self.has_audio = has_audio
        self.file_size_bytes = file_size_bytes
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "duration_sec": round(self.duration_sec, 2),
            "width": self.width,
            "height": self.height,
            "has_audio": self.has_audio,
            "file_size_bytes": self.file_size_bytes,
            "error": self.error,
        }

    def __repr__(self) -> str:
        return (
            f"<RenderValidationResult valid={self.is_valid} "
            f"res={self.width}x{self.height} dur={self.duration_sec:.1f}s>"
        )


_CACHED_ENCODER = None


def _test_hardware_encoder(encoder_name: str) -> bool:
    """Performs a 1-frame probe test to verify hardware encoder opens without DLL or device errors."""
    try:
        proc = SubprocessWatchdog.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=0.04:size=64x64:rate=25", "-c:v", encoder_name, "-f", "null", "-"],
            timeout_sec=2.0,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


def get_best_video_encoder() -> Tuple[str, List[str]]:
    """
    Auto-detects the best available hardware video encoder (NVENC, QSV, AMF)
    or falls back cleanly to software libx264. Probes hardware encoders before selecting.
    Reads studio mastering quality from settings.
    """
    global _CACHED_ENCODER
    if _CACHED_ENCODER is not None:
        return _CACHED_ENCODER

    crf = str(getattr(settings, "VIDEO_ENCODING_CRF", 18))
    preset = str(getattr(settings, "VIDEO_ENCODING_PRESET", "medium"))

    try:
        proc = SubprocessWatchdog.run(["ffmpeg", "-encoders"], timeout_sec=5.0, check=False)
        encoders_out = proc.stdout.lower()
        if "h264_nvenc" in encoders_out and _test_hardware_encoder("h264_nvenc"):
            logger.info("Hardware acceleration validated: using NVIDIA h264_nvenc at CQ %s.", crf)
            _CACHED_ENCODER = ("h264_nvenc", ["-preset", "p6", "-cq", crf, "-rc", "vbr", "-spatial-aq", "1"])
            return _CACHED_ENCODER
        elif "h264_qsv" in encoders_out and _test_hardware_encoder("h264_qsv"):
            logger.info("Hardware acceleration validated: using Intel h264_qsv at quality %s.", crf)
            _CACHED_ENCODER = ("h264_qsv", ["-preset", "medium", "-global_quality", crf])
            return _CACHED_ENCODER
        elif "h264_amf" in encoders_out and _test_hardware_encoder("h264_amf"):
            logger.info("Hardware acceleration validated: using AMD h264_amf at QP %s.", crf)
            _CACHED_ENCODER = ("h264_amf", ["-quality", "quality", "-rc", "cqp", "-qp_p", crf])
            return _CACHED_ENCODER
    except Exception as e:
        logger.warning("Could not probe FFmpeg hardware encoders: %s", e)

    logger.info("Using software video encoder: libx264 at CRF %s (preset: %s).", crf, preset)
    _CACHED_ENCODER = ("libx264", ["-preset", preset, "-crf", crf, "-profile:v", "high", "-level", "4.2"])
    return _CACHED_ENCODER


class VideoCompositor:
    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or settings.OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def render_short(
        self,
        source_video_path: Path,
        start_ms: int,
        end_ms: int,
        crop_plan: CropPlan,
        subtitle_ass_path: Optional[Path] = None,
        output_filename: str = "output.mp4",
        normalize_audio: bool = True,
        crf: Optional[int] = None,
        preset: Optional[str] = None,
        scale_algo: Optional[str] = None,
        audio_bitrate: Optional[str] = None,
        target_fps: Optional[int] = None,
        hook_overlay_text: Optional[str] = None,
        broll_video_path: Optional[Path] = None,
        broll_start_sec: float = 1.0,
        broll_duration_sec: float = 2.5,
        music_track_path: Optional[Path] = None,
    ) -> Path:
        """
        Renders a finished 9:16 portrait Short video using FFmpeg.
        Supports per-video overrides for CRF, preset, scale algorithm, audio bitrate, FPS,
        optional visual hook overlay during the opening 3 seconds, optional B-roll stock footage cutaways,
        and studio-grade audio mastering with speech-activated sidechain ducking over background music.
        """
        output_path = self.output_dir / output_filename
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists() and output_path.stat().st_size > 0:
            val = self.validate_render(output_path)
            if val.is_valid:
                logger.info("Valid rendered file already exists at %s, skipping FFmpeg re-render.", output_path)
                return output_path

        if not Path(source_video_path).exists():
            raise FileNotFoundError(f"Master source video not found at: {source_video_path}")

        start_sec = max(0.0, start_ms / 1000.0)
        duration_sec = max(1.0, (end_ms - start_ms) / 1000.0)

        # Build audio mastering filter chain (Studio Mastering: HPF 80Hz + Noise Gate + EBU R128)
        mastering_engine = AudioMasteringEngine()
        if normalize_audio:
            hpf_hz = mastering_engine.hpf_cutoff_hz
            target_lufs = mastering_engine.target_lufs
            tp = mastering_engine.true_peak_dbfs
            audio_filter_arg = (
                f"highpass=f={hpf_hz}:poles=2,"
                "agate=range=-24dB:threshold=-36dB:attack=10:release=120,"
                f"loudnorm=I={target_lufs}:TP={tp}:LRA=7"
            )
        else:
            audio_filter_arg = "anull"

        hook_temp_file = None
        has_broll = broll_video_path is not None and Path(broll_video_path).exists()
        has_music = music_track_path is not None and Path(music_track_path).exists()

        if has_broll or has_music:
            # Multi-input filter_complex for B-roll overlays and/or background music ducking
            fc_parts = []
            fc_parts.append(f"[0:v]{crop_plan.to_ffmpeg_filter(scale_algo=scale_algo)}[base]")
            current_v = "[base]"

            if hook_overlay_text and hook_overlay_text.strip():
                try:
                    import tempfile
                    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix="_hook.txt") as tf:
                        tf.write(hook_overlay_text.strip())
                        hook_temp_file = Path(tf.name)

                    esc_txt_path = escape_ffmpeg_filter_path(hook_temp_file)
                    font_file = get_default_fontfile()
                    font_opt = f"fontfile='{font_file}':" if font_file else ""
                    drawtext_filter = (
                        f"drawtext={font_opt}textfile='{esc_txt_path}':expansion=none:"
                        f"enable='between(t,0,3)':fontcolor=yellow:fontsize=52:"
                        f"box=1:boxcolor=black@0.75:boxborderw=14:x=(w-text_w)/2:y=h*0.22"
                    )
                    fc_parts.append(f"{current_v}{drawtext_filter}[hooked]")
                    current_v = "[hooked]"
                except Exception as e:
                    logger.warning("Failed to configure hook overlay textfile: %s", e)

            # B-roll scaling and cropping to 1080:1920 canvas
            if has_broll:
                fc_parts.append("[1:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[broll_scaled]")
                broll_end = broll_start_sec + broll_duration_sec
                fc_parts.append(f"{current_v}[broll_scaled]overlay=0:0:enable='between(t,{broll_start_sec},{broll_end})'[overlaid]")
                current_v = "[overlaid]"

            if subtitle_ass_path and subtitle_ass_path.exists():
                escaped_sub = escape_ffmpeg_filter_path(subtitle_ass_path)
                fc_parts.append(f"{current_v}ass='{escaped_sub}'[v_out]")
            else:
                fc_parts.append(f"{current_v}null[v_out]")

            if has_music and normalize_audio:
                music_input_idx = 2 if has_broll else 1
                ducking_fc = mastering_engine.build_sidechain_ducking_filter_graph(
                    speech_input_label="0:a",
                    music_input_label=f"{music_input_idx}:a",
                    output_label="a_out",
                )
                fc_parts.append(ducking_fc)
            else:
                fc_parts.append(f"[0:a]{audio_filter_arg}[a_out]")

            filter_complex_arg = ";".join(fc_parts)
        else:
            # Build standard single-input video filter chain
            v_filters = []
            v_filters.append(crop_plan.to_ffmpeg_filter(scale_algo=scale_algo))

            if hook_overlay_text and hook_overlay_text.strip():
                try:
                    import tempfile
                    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix="_hook.txt") as tf:
                        tf.write(hook_overlay_text.strip())
                        hook_temp_file = Path(tf.name)

                    esc_txt_path = escape_ffmpeg_filter_path(hook_temp_file)
                    font_file = get_default_fontfile()
                    font_opt = f"fontfile='{font_file}':" if font_file else ""
                    drawtext_filter = (
                        f"drawtext={font_opt}textfile='{esc_txt_path}':expansion=none:"
                        f"enable='between(t,0,3)':fontcolor=yellow:fontsize=52:"
                        f"box=1:boxcolor=black@0.75:boxborderw=14:x=(w-text_w)/2:y=h*0.22"
                    )
                    v_filters.append(drawtext_filter)
                except Exception as e:
                    logger.warning("Failed to configure hook overlay textfile: %s", e)

            if subtitle_ass_path and subtitle_ass_path.exists():
                escaped_sub = escape_ffmpeg_filter_path(subtitle_ass_path)
                v_filters.append(f"ass='{escaped_sub}'")

            video_filter_arg = ",".join(v_filters)

        encoder_name, encoder_flags = get_best_video_encoder()
        eff_crf = str(crf if crf is not None else getattr(settings, "VIDEO_ENCODING_CRF", 18))
        eff_preset = str(preset if preset is not None else getattr(settings, "VIDEO_ENCODING_PRESET", "medium"))
        eff_audio_br = str(audio_bitrate if audio_bitrate is not None else getattr(settings, "AUDIO_BITRATE", "320k"))
        eff_fps = str(target_fps if target_fps is not None else getattr(settings, "VIDEO_TARGET_FPS", 30))
        audio_ar = str(getattr(settings, "AUDIO_SAMPLE_RATE", 48000))
        fallback_crf = str(getattr(settings, "VIDEO_ENCODING_CRF", 18))
        fallback_preset = str(getattr(settings, "VIDEO_ENCODING_PRESET", "medium"))

        active_flags = list(encoder_flags)
        if crf is not None or preset is not None:
            if encoder_name == "libx264":
                active_flags = ["-preset", eff_preset, "-crf", eff_crf, "-profile:v", "high", "-level", "4.2"]
            elif "nvenc" in encoder_name:
                active_flags = ["-preset", "p6" if eff_preset in ("slow", "veryslow") else "p4", "-cq", eff_crf, "-rc", "vbr", "-spatial-aq", "1"]
            elif "qsv" in encoder_name:
                active_flags = ["-preset", eff_preset, "-global_quality", eff_crf]
            elif "amf" in encoder_name:
                active_flags = ["-quality", "quality", "-rc", "cqp", "-qp_p", eff_crf]

        if has_broll or has_music:
            cmd = [
                "ffmpeg",
                "-y",
                "-ss", f"{start_sec:.3f}",
                "-i", str(source_video_path),
            ]
            if has_broll:
                cmd.extend(["-t", f"{duration_sec:.3f}", "-i", str(broll_video_path)])
            if has_music:
                cmd.extend(["-stream_loop", "-1", "-i", str(music_track_path)])

            cmd.extend([
                "-t", f"{duration_sec:.3f}",
                "-filter_complex", filter_complex_arg,
                "-map", "[v_out]",
                "-map", "[a_out]",
                "-r", eff_fps,
                "-c:v", encoder_name,
                *active_flags,
                "-c:a", "aac",
                "-b:a", eff_audio_br,
                "-ar", audio_ar,
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                str(output_path),
            ])
        else:
            cmd = [
                "ffmpeg",
                "-y",
                "-ss", f"{start_sec:.3f}",
                "-i", str(source_video_path),
                "-t", f"{duration_sec:.3f}",
                "-vf", video_filter_arg,
                "-af", audio_filter_arg,
                "-r", eff_fps,
                "-c:v", encoder_name,
                *active_flags,
                "-c:a", "aac",
                "-b:a", eff_audio_br,
                "-ar", audio_ar,
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                str(output_path),
            ]

        timeout_limit = max(180.0, duration_sec * 6.0)
        try:
            logger.info("Executing FFmpeg render for %s (encoder: %s, crf: %s, preset: %s, bitrate: %s, fps: %s, timeout: %0.1fs)...", output_filename, encoder_name, eff_crf, eff_preset, eff_audio_br, eff_fps, timeout_limit)
            result = SubprocessWatchdog.run(cmd, timeout_sec=timeout_limit, check=False, artifacts_to_cleanup=[output_path])
            if result.exit_code != 0:
                if encoder_name != "libx264":
                    global _CACHED_ENCODER
                    _CACHED_ENCODER = ("libx264", ["-preset", fallback_preset, "-crf", fallback_crf, "-profile:v", "high", "-level", "4.2"])
                    logger.warning("Hardware encoder %s failed. Cached libx264 for future renders. Retrying with CPU libx264...", encoder_name)
                    if has_broll or has_music:
                        cmd_fallback = [
                            "ffmpeg",
                            "-y",
                            "-ss", f"{start_sec:.3f}",
                            "-i", str(source_video_path),
                        ]
                        if has_broll:
                            cmd_fallback.extend(["-t", f"{duration_sec:.3f}", "-i", str(broll_video_path)])
                        if has_music:
                            cmd_fallback.extend(["-stream_loop", "-1", "-i", str(music_track_path)])

                        cmd_fallback.extend([
                            "-t", f"{duration_sec:.3f}",
                            "-filter_complex", filter_complex_arg,
                            "-map", "[v_out]",
                            "-map", "[a_out]",
                            "-r", eff_fps,
                            "-c:v", "libx264",
                            "-preset", fallback_preset,
                            "-crf", fallback_crf,
                            "-profile:v", "high",
                            "-level", "4.2",
                            "-c:a", "aac",
                            "-b:a", eff_audio_br,
                            "-ar", audio_ar,
                            "-pix_fmt", "yuv420p",
                            "-movflags", "+faststart",
                            str(output_path),
                        ])
                    else:
                        cmd_fallback = [
                            "ffmpeg",
                            "-y",
                            "-ss", f"{start_sec:.3f}",
                            "-i", str(source_video_path),
                            "-t", f"{duration_sec:.3f}",
                            "-vf", video_filter_arg,
                            "-af", audio_filter_arg,
                            "-r", eff_fps,
                            "-c:v", "libx264",
                            "-preset", fallback_preset,
                            "-crf", fallback_crf,
                            "-profile:v", "high",
                            "-level", "4.2",
                            "-c:a", "aac",
                            "-b:a", eff_audio_br,
                            "-ar", audio_ar,
                            "-pix_fmt", "yuv420p",
                            "-movflags", "+faststart",
                            str(output_path),
                        ]
                    result = SubprocessWatchdog.run(cmd_fallback, timeout_sec=timeout_limit, check=False, artifacts_to_cleanup=[output_path])

                if result.exit_code != 0:
                    logger.error("FFmpeg render failed:\n%s", result.stderr)
                    raise RuntimeError(f"FFmpeg render failed: {result.stderr[-400:]}")

            logger.info("Render completed successfully: %s", output_path)
            return output_path
        finally:
            if hook_temp_file and hook_temp_file.exists():
                try:
                    hook_temp_file.unlink()
                except Exception:
                    pass


    def validate_render(self, video_path: Path) -> RenderValidationResult:
        """
        Validates the rendered video file using ffprobe.
        """
        if not video_path.exists() or video_path.stat().st_size == 0:
            return RenderValidationResult(
                is_valid=False,
                duration_sec=0.0,
                width=0,
                height=0,
                has_audio=False,
                file_size_bytes=0,
                error="File does not exist or is 0 bytes",
            )

        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(video_path),
        ]
        try:
            result = SubprocessWatchdog.run(cmd, timeout_sec=15.0)
        except Exception as e:
            return RenderValidationResult(
                is_valid=False,
                duration_sec=0.0,
                width=0,
                height=0,
                has_audio=False,
                file_size_bytes=video_path.stat().st_size if video_path.exists() else 0,
                error=f"ffprobe execution failed or timed out: {e}",
            )
        if result.returncode != 0:
            return RenderValidationResult(
                is_valid=False,
                duration_sec=0.0,
                width=0,
                height=0,
                has_audio=False,
                file_size_bytes=video_path.stat().st_size,
                error=f"ffprobe failed: {result.stderr}",
            )

        try:
            info = json.loads(result.stdout)
            format_info = info.get("format", {})
            duration = float(format_info.get("duration", 0.0))
            
            width = 0
            height = 0
            has_audio = False

            for stream in info.get("streams", []):
                if stream.get("codec_type") == "video" and width == 0:
                    width = int(stream.get("width", 0))
                    height = int(stream.get("height", 0))
                elif stream.get("codec_type") == "audio":
                    has_audio = True

            # Verify 9:16 resolution and audio
            is_valid = (width == settings.TARGET_WIDTH and height == settings.TARGET_HEIGHT and has_audio and duration > 0.0)
            err = None if is_valid else f"Failed checks: width={width}, height={height}, has_audio={has_audio}"

            return RenderValidationResult(
                is_valid=is_valid,
                duration_sec=duration,
                width=width,
                height=height,
                has_audio=has_audio,
                file_size_bytes=video_path.stat().st_size,
                error=err,
            )
        except Exception as e:
            return RenderValidationResult(
                is_valid=False,
                duration_sec=0.0,
                width=0,
                height=0,
                has_audio=False,
                file_size_bytes=video_path.stat().st_size,
                error=f"JSON parsing error: {e}",
            )
