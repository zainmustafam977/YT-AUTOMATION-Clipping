"""
Rendering Service: FFmpeg compositor that crops to 9:16 portrait, burns in karaoke ASS subtitles,
normalizes audio to EBU R128 standards, ducks background audio, and validates the final MP4.
"""

import json
import logging
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from shorts_engine.config.settings import settings
from shorts_engine.services.reframing.cropper import CropPlan
from shorts_engine.services.audio.mastering import AudioMasteringEngine
from shorts_engine.services.execution.watchdog import SubprocessWatchdog
from shorts_engine.services.rendering.hwaccel import HardwareEncoderProber
from shorts_engine.services.events.bus import global_event_bus
from shorts_engine.services.events.models import PlatformEvent

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
    """Finds a reliable system font path for drawtext on Windows/Linux (PLAN-P2-09 / F-35)."""
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
        settings.ROOT_DIR / "assets" / "fonts" / "DejaVuSans-Bold.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"),
        Path("/System/Library/Fonts/Helvetica.ttc"),
    ]
    for c in candidates:
        if c.exists():
            return escape_ffmpeg_filter_path(c)
    return None


def get_ass_filter_string(subtitle_ass_path: Path, fonts_dir: Optional[Path] = None) -> str:
    """
    Builds the FFmpeg ass subtitle filter parameter string with optional fontsdir configuration (Enhancement 6).
    """
    escaped_sub = escape_ffmpeg_filter_path(subtitle_ass_path)
    f_dir = fonts_dir or getattr(settings, "FONTS_DIR", settings.ROOT_DIR / "assets" / "fonts")
    if f_dir and Path(f_dir).exists():
        escaped_fonts = escape_ffmpeg_filter_path(Path(f_dir))
        return f"ass='{escaped_sub}':fontsdir='{escaped_fonts}'"
    return f"ass='{escaped_sub}'"



def compute_watermark_position(
    anchor: str,
    margin_x: int = 30,
    margin_y: int = 40,
    is_drawtext: bool = False,
) -> Tuple[str, str]:
    """
    Computes FFmpeg (x, y) coordinates for the 9-point grid watermark matrix.
    Supports:
      - 'top_left' / 'tl'
      - 'top_center' / 'tc'
      - 'top_right' / 'tr'
      - 'center_left' / 'cl'
      - 'center' / 'cc' / 'middle'
      - 'center_right' / 'cr'
      - 'bottom_left' / 'bl'
      - 'bottom_center' / 'bc'
      - 'bottom_right' / 'br'
    """
    a = (anchor or "top_right").lower().replace("-", "_").strip()
    mx = int(margin_x)
    my = int(margin_y)
    if is_drawtext:
        W, H, w, h = "w", "h", "text_w", "text_h"
    else:
        W, H, w, h = "W", "H", "w", "h"

    if a in ("top_left", "tl"):
        return str(mx), str(my)
    elif a in ("top_center", "tc", "top"):
        return f"({W}-{w})/2", str(my)
    elif a in ("top_right", "tr"):
        return f"{W}-{w}-{mx}", str(my)
    elif a in ("center_left", "cl", "left"):
        return str(mx), f"({H}-{h})/2"
    elif a in ("center", "cc", "middle"):
        return f"({W}-{w})/2", f"({H}-{h})/2"
    elif a in ("center_right", "cr", "right"):
        return f"{W}-{w}-{mx}", f"({H}-{h})/2"
    elif a in ("bottom_left", "bl"):
        return str(mx), f"{H}-{h}-{my}"
    elif a in ("bottom_center", "bc", "bottom"):
        return f"({W}-{w})/2", f"{H}-{h}-{my}"
    elif a in ("bottom_right", "br"):
        return f"{W}-{w}-{mx}", f"{H}-{h}-{my}"
    else:
        return f"{W}-{w}-{mx}", str(my)


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

    prober = HardwareEncoderProber.get_instance()
    _CACHED_ENCODER = prober.get_optimal_encoder()
    return _CACHED_ENCODER


def probe_aspect_ratio(video_path: Path) -> Tuple[int, int, str]:
    """
    Probes video dimensions via ffprobe and categorizes aspect ratio:
    - '16:9': Widescreen landscape (width / height >= 1.4)
    - '9:16': Vertical portrait (width / height <= 0.7)
    - '1:1': Square (0.85 <= width / height <= 1.15)
    - '4:3': Standard fullscreen / SD (1.15 < width / height < 1.4)
    Returns (width, height, aspect_ratio_type).
    """
    if not video_path or not Path(video_path).exists() or Path(video_path).stat().st_size == 0:
        return settings.TARGET_WIDTH, settings.TARGET_HEIGHT, "16:9"

    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        str(video_path),
    ]
    try:
        res = SubprocessWatchdog.run(cmd, timeout_sec=10.0, check=False)
        if res.returncode == 0 and res.stdout:
            data = json.loads(res.stdout)
            for s in data.get("streams", []):
                if s.get("codec_type") == "video":
                    w = int(s.get("width", 0))
                    h = int(s.get("height", 0))
                    if w > 0 and h > 0:
                        ratio = w / h
                        if ratio >= 1.4:
                            return w, h, "16:9"
                        elif ratio <= 0.7:
                            return w, h, "9:16"
                        elif 0.85 <= ratio <= 1.15:
                            return w, h, "1:1"
                        elif 1.15 < ratio < 1.4:
                            return w, h, "4:3"
                        else:
                            return w, h, "16:9"
    except Exception as e:
        logger.debug("Failed to probe aspect ratio for %s: %s", video_path, e)

    return settings.TARGET_WIDTH, settings.TARGET_HEIGHT, "16:9"


class VideoCompositor:
    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or settings.OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def build_aspect_ratio_filter(
        self,
        source_path: Path,
        crop_plan: Optional[CropPlan] = None,
        aspect_ratio_mode: Optional[str] = "auto",
        scale_algo: Optional[str] = None,
    ) -> Tuple[str, bool]:
        """
        Builds the video filter string for multi-aspect ratio rendering.
        Returns:
            (filter_str, is_complex_split)
        Pipelines:
          - 16:9 widescreen: Face/active-speaker tracked vertical crop (or centered crop if no face).
          - 9:16 vertical: Direct passthrough with padding/scaling as needed.
          - 1:1 square or 4:3 standard: Smart Gaussian-blurred background mirror.
        """
        algo = scale_algo or getattr(settings, "VIDEO_SCALE_ALGORITHM", "lanczos")
        mode = (aspect_ratio_mode or "auto").lower()

        if mode == "auto":
            _, _, detected_mode = probe_aspect_ratio(source_path)
            mode = detected_mode

        if mode in ("1:1", "4:3", "blurred_mirror", "mirror"):
            # Smart Gaussian-blurred background mirror
            filter_str = (
                "split [main][bg]; "
                f"[bg] scale=1080:1920:force_original_aspect_ratio=increase:flags={algo},crop=1080:1920,gblur=sigma=25 [blurred]; "
                f"[main] scale=1080:-2:flags={algo} [fg]; "
                "[blurred][fg] overlay=(W-w)/2:(H-h)/2,setsar=1"
            )
            return filter_str, True

        elif mode in ("9:16", "vertical", "passthrough"):
            # Direct vertical passthrough with scaling and center padding
            filter_str = (
                f"scale=1080:1920:force_original_aspect_ratio=decrease:flags={algo},"
                "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1"
            )
            return filter_str, False

        else:
            # 16:9 widescreen or default: active crop plan
            if crop_plan is not None:
                return crop_plan.to_ffmpeg_filter(scale_algo=algo), False
            else:
                return (
                    f"crop=min(iw\\,ih*9/16):ih:(iw-ow)/2:0,scale=1080:1920:flags={algo},setsar=1",
                    False,
                )

    def _build_filter_complex(
        self,
        base_filter: str,
        broll_idx: Optional[int] = None,
        broll_start_sec: float = 1.0,
        broll_duration_sec: float = 2.5,
        watermark_img_idx: Optional[int] = None,
        watermark_scale: float = 0.18,
        watermark_fade: str = "none",
        watermark_fade_sec: float = 1.0,
        watermark_opacity: float = 0.85,
        duration_sec: float = 30.0,
        watermark_anchor: str = "top_right",
        watermark_margin_x: int = 30,
        watermark_margin_y: int = 40,
        dt_wm: Optional[str] = None,
        has_subs: bool = False,
        subtitle_ass_path: Optional[Path] = None,
        dt_hook: Optional[str] = None,
        dt_cta: Optional[str] = None,
        cover_img_idx: Optional[int] = None,
        music_idx: Optional[int] = None,
        normalize_audio: bool = True,
        audio_filter_arg: str = "anull",
        mastering_engine: Optional[AudioMasteringEngine] = None,
        fonts_dir: Optional[Path] = None,
    ) -> str:
        """
        Builds the unified filter_complex parameter string for zero generational loss.
        Includes fontsdir for the ASS subtitle filter (Enhancement 6) and cover frame overlay (Enhancement 7).
        """
        fc_parts = []
        fc_parts.append(f"[0:v]{base_filter}[base]")
        current_v = "[base]"

        if cover_img_idx is not None:
            fc_parts.append(
                f"[{cover_img_idx}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[cov_scaled]"
            )
            fc_parts.append(f"{current_v}[cov_scaled]overlay=0:0:enable='between(t,0,0.033)'[v_covered]")
            current_v = "[v_covered]"

        if broll_idx is not None:
            fc_parts.append(f"[{broll_idx}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[broll_scaled]")
            broll_end = broll_start_sec + broll_duration_sec
            fc_parts.append(f"{current_v}[broll_scaled]overlay=0:0:enable='between(t,{broll_start_sec:g},{broll_end:g})'[overlaid]")
            current_v = "[overlaid]"

        if watermark_img_idx is not None:
            raw_w = max(40, round(1080 * watermark_scale))
            wm_w = raw_w if raw_w % 2 == 0 else raw_w + 1
            wm_flt = [f"scale={wm_w}:-2", "format=rgba"]
            fade_dur = min(watermark_fade_sec, duration_sec / 2.0)
            if watermark_fade == "fade_in":
                wm_flt.append(f"fade=t=in:st=0:d={fade_dur:.2f}:alpha=1")
            elif watermark_fade == "fade_out":
                st = max(0.0, duration_sec - fade_dur)
                wm_flt.append(f"fade=t=out:st={st:.2f}:d={fade_dur:.2f}:alpha=1")
            elif watermark_fade == "fade_in_out":
                st = max(0.0, duration_sec - fade_dur)
                wm_flt.append(f"fade=t=in:st=0:d={fade_dur:.2f}:alpha=1,fade=t=out:st={st:.2f}:d={fade_dur:.2f}:alpha=1")
            wm_flt.append(f"colorchannelmixer=aa={watermark_opacity}")
            wm_filter_str = ",".join(wm_flt)
            fc_parts.append(f"[{watermark_img_idx}:v]{wm_filter_str}[wm_scaled]")
            x_pos, y_pos = compute_watermark_position(watermark_anchor, watermark_margin_x, watermark_margin_y, is_drawtext=False)
            fc_parts.append(f"{current_v}[wm_scaled]overlay={x_pos}:{y_pos}:format=auto[v_wm_img]")
            current_v = "[v_wm_img]"

        if dt_wm:
            fc_parts.append(f"{current_v}{dt_wm}[v_wm_txt]")
            current_v = "[v_wm_txt]"

        if has_subs and subtitle_ass_path:
            ass_filter = get_ass_filter_string(subtitle_ass_path, fonts_dir=fonts_dir)
            fc_parts.append(f"{current_v}{ass_filter}[v_sub]")
            current_v = "[v_sub]"

        if dt_hook:
            fc_parts.append(f"{current_v}{dt_hook}[v_hook]")
            current_v = "[v_hook]"

        if dt_cta:
            fc_parts.append(f"{current_v}{dt_cta}[v_cta]")
            current_v = "[v_cta]"

        fc_parts.append(f"{current_v}null[v_out]")

        if music_idx is not None and normalize_audio and mastering_engine:
            ducking_fc = mastering_engine.build_sidechain_ducking_filter_graph(
                speech_input_label="0:a",
                music_input_label=f"{music_idx}:a",
                output_label="a_out",
            )
            fc_parts.append(ducking_fc)
        else:
            fc_parts.append(f"[0:a]{audio_filter_arg}[a_out]")

        return ";".join(fc_parts)

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
        force_re_render: bool = False,
        watermark_image_path: Optional[Path] = None,
        watermark_text: Optional[str] = None,
        watermark_anchor: str = "top_right",
        watermark_opacity: float = 0.85,
        watermark_scale: float = 0.18,
        watermark_margin_x: int = 30,
        watermark_margin_y: int = 40,
        watermark_fade: str = "none",
        watermark_fade_sec: float = 1.0,
        cta_overlay_text: Optional[str] = None,
        cta_duration_sec: float = 3.5,
        hook_duration_sec: float = 3.0,
        aspect_ratio_mode: Optional[str] = "auto",
        cover_frame_sec: Optional[float] = None,
        cover_frame_image_path: Optional[Path] = None,
        fonts_dir: Optional[Path] = None,
    ) -> Path:
        """
        Renders a finished 9:16 portrait Short video using FFmpeg.
        Supports per-video overrides for CRF, preset, scale algorithm, audio bitrate, FPS,
        multi-aspect ratio adaptation (16:9 crop, 9:16 passthrough, 1:1/4:3 blurred mirror),
        optional visual hook overlay, optional footer CTA overlay, dual-engine watermarking
        (image + text handle with 9-point grid), optional B-roll stock footage cutaways,
        sub-frame AV lip-sync (-avoid_negative_ts make_zero -async 1),
        and studio-grade audio mastering with speech-activated sidechain ducking over background music.
        Compiled into a single FFmpeg pass with zero generational loss.
        """
        output_path = self.output_dir / output_filename
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not force_re_render and output_path.exists() and output_path.stat().st_size > 0:
            val = self.validate_render(output_path)
            if val.is_valid:
                logger.info("Valid rendered file already exists at %s, skipping FFmpeg re-render.", output_path)
                return output_path
        elif force_re_render and output_path.exists():
            try:
                output_path.unlink()
                logger.info("Force re-render requested: unlinked existing artifact at %s", output_path)
            except Exception as e:
                logger.warning("Could not unlink existing render file: %s", e)

        if not Path(source_video_path).exists():
            raise FileNotFoundError(f"Master source video not found at: {source_video_path}")

        start_sec = max(0.0, start_ms / 1000.0)
        duration_sec = max(1.0, (end_ms - start_ms) / 1000.0)

        # Build audio mastering filter chain (Studio Mastering: HPF 80Hz + 3kHz Presence + Noise Gate + EBU R128)
        mastering_engine = AudioMasteringEngine()
        if normalize_audio:
            hpf_hz = mastering_engine.hpf_cutoff_hz
            presence_hz = getattr(mastering_engine, "vocal_presence_freq_hz", 3000)
            presence_gain = getattr(mastering_engine, "vocal_presence_gain_db", 2.5)
            target_lufs = mastering_engine.target_lufs
            tp = mastering_engine.true_peak_dbfs
            audio_filter_arg = (
                f"highpass=f={hpf_hz}:poles=2,"
                f"equalizer=f={presence_hz}:t=q:w=1.0:g={presence_gain},"
                "agate=range=-24dB:threshold=-36dB:attack=10:release=120,"
                f"loudnorm=I={target_lufs}:TP={tp}:LRA=7"
            )
        else:
            audio_filter_arg = "anull"

        temp_files: List[Path] = []
        has_broll = broll_video_path is not None and Path(broll_video_path).exists()
        has_music = music_track_path is not None and Path(music_track_path).exists()
        has_watermark_image = watermark_image_path is not None and Path(watermark_image_path).exists()
        if watermark_image_path is not None and not has_watermark_image:
            logger.warning("Watermark image path does not exist on disk, skipping overlay: %s", watermark_image_path)
        has_watermark_text = bool(watermark_text and watermark_text.strip())
        has_subs = subtitle_ass_path is not None and Path(subtitle_ass_path).exists()
        has_hook = bool(hook_overlay_text and hook_overlay_text.strip())
        has_cta = bool(cta_overlay_text and cta_overlay_text.strip())

        font_file = get_default_fontfile()
        font_opt = f"fontfile='{font_file}':" if font_file else ""

        # Prepare Hook Overlay drawtext filter
        dt_hook = None
        if has_hook:
            try:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix="_hook.txt") as tf:
                    tf.write(hook_overlay_text.strip())
                    hk_path = Path(tf.name)
                    temp_files.append(hk_path)
                esc_hk = escape_ffmpeg_filter_path(hk_path)
                dt_hook = (
                    f"drawtext={font_opt}textfile='{esc_hk}':expansion=none:"
                    f"enable='between(t,0,{hook_duration_sec:g})':fontcolor=yellow:fontsize=52:"
                    f"box=1:boxcolor=black@0.75:boxborderw=14:x=(w-text_w)/2:y=h*0.12"
                )
            except Exception as e:
                logger.warning("Failed to configure hook overlay textfile: %s", e)

        # Prepare Footer CTA drawtext filter
        dt_cta = None
        if has_cta:
            try:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix="_cta.txt") as tf:
                    tf.write(cta_overlay_text.strip())
                    cta_path = Path(tf.name)
                    temp_files.append(cta_path)
                esc_cta = escape_ffmpeg_filter_path(cta_path)
                cta_st = max(0.0, duration_sec - cta_duration_sec)
                dt_cta = (
                    f"drawtext={font_opt}textfile='{esc_cta}':expansion=none:"
                    f"enable='between(t,{cta_st:g},{duration_sec:g})':fontcolor=white:fontsize=42:"
                    f"box=1:boxcolor=red@0.85:boxborderw=12:x=(w-text_w)/2:y=h-240"
                )
            except Exception as e:
                logger.warning("Failed to configure CTA overlay textfile: %s", e)

        # Prepare Text Watermark Handle drawtext filter
        dt_wm = None
        if has_watermark_text:
            try:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix="_wm.txt") as tf:
                    tf.write(watermark_text.strip())
                    wm_path = Path(tf.name)
                    temp_files.append(wm_path)
                esc_wm = escape_ffmpeg_filter_path(wm_path)
                wm_x, wm_y = compute_watermark_position(watermark_anchor, watermark_margin_x, watermark_margin_y, is_drawtext=True)
                if watermark_fade == "fade_in":
                    alpha_expr = f"min(1,t/{watermark_fade_sec:.2f})*{watermark_opacity}"
                elif watermark_fade == "fade_out":
                    alpha_expr = f"max(0,({duration_sec:.2f}-t)/{watermark_fade_sec:.2f})*{watermark_opacity}"
                elif watermark_fade == "fade_in_out":
                    alpha_expr = f"min(1,min(t/{watermark_fade_sec:.2f},max(0,({duration_sec:.2f}-t)/{watermark_fade_sec:.2f})))*{watermark_opacity}"
                else:
                    alpha_expr = f"{watermark_opacity}"
                dt_wm = (
                    f"drawtext={font_opt}textfile='{esc_wm}':expansion=none:fontsize=36:fontcolor=white:"
                    f"box=1:boxcolor=black@0.6:boxborderw=8:x={wm_x}:y={wm_y}:alpha='{alpha_expr}'"
                )
            except Exception as e:
                logger.warning("Failed to configure text watermark: %s", e)

        # Determine aspect ratio filter
        base_filter, is_complex_split = self.build_aspect_ratio_filter(
            source_path=source_video_path,
            crop_plan=crop_plan,
            aspect_ratio_mode=aspect_ratio_mode,
            scale_algo=scale_algo,
        )

        # Handle custom cover frame / thumbnail injection (Enhancement 7)
        if cover_frame_sec is not None and not cover_frame_image_path:
            try:
                with tempfile.NamedTemporaryFile("w", delete=False, suffix="_cover.jpg") as tf:
                    cov_tmp = Path(tf.name)
                    temp_files.append(cov_tmp)
                cov_ts = start_sec + float(cover_frame_sec)
                cmd_cov = [
                    "ffmpeg", "-y",
                    "-ss", f"{cov_ts:.3f}",
                    "-i", str(source_video_path),
                    "-frames:v", "1",
                    "-q:v", "2",
                    str(cov_tmp),
                ]
                res_cov = SubprocessWatchdog.run(cmd_cov, timeout_sec=15.0, check=False)
                if res_cov.returncode == 0 and cov_tmp.exists():
                    cover_frame_image_path = cov_tmp
            except Exception as e:
                logger.warning("Failed to extract cover frame at %s: %s", cover_frame_sec, e)

        has_cover = cover_frame_image_path is not None and Path(cover_frame_image_path).exists()
        is_multi_input = has_broll or has_music or has_watermark_image or is_complex_split or has_cover
        input_args = ["-ss", f"{start_sec:.3f}", "-i", str(source_video_path)]
        next_idx = 1
        cover_img_idx = None
        if has_cover:
            input_args.extend(["-i", str(cover_frame_image_path)])
            cover_img_idx = next_idx
            next_idx += 1
        broll_idx = None
        if has_broll:
            input_args.extend(["-t", f"{duration_sec:.3f}", "-i", str(broll_video_path)])
            broll_idx = next_idx
            next_idx += 1
        watermark_img_idx = None
        if has_watermark_image:
            input_args.extend(["-i", str(watermark_image_path)])
            watermark_img_idx = next_idx
            next_idx += 1
        music_idx = None
        if has_music:
            input_args.extend(["-stream_loop", "-1", "-i", str(music_track_path)])
            music_idx = next_idx
            next_idx += 1

        if is_multi_input:
            filter_complex_arg = self._build_filter_complex(
                base_filter=base_filter,
                broll_idx=broll_idx,
                broll_start_sec=broll_start_sec,
                broll_duration_sec=broll_duration_sec,
                watermark_img_idx=watermark_img_idx,
                watermark_scale=watermark_scale,
                watermark_fade=watermark_fade,
                watermark_fade_sec=watermark_fade_sec,
                watermark_opacity=watermark_opacity,
                duration_sec=duration_sec,
                watermark_anchor=watermark_anchor,
                watermark_margin_x=watermark_margin_x,
                watermark_margin_y=watermark_margin_y,
                dt_wm=dt_wm,
                has_subs=has_subs,
                subtitle_ass_path=subtitle_ass_path,
                dt_hook=dt_hook,
                dt_cta=dt_cta,
                cover_img_idx=cover_img_idx,
                music_idx=music_idx,
                normalize_audio=normalize_audio,
                audio_filter_arg=audio_filter_arg,
                mastering_engine=mastering_engine,
                fonts_dir=fonts_dir,
            )
        else:
            # Single-input filter chain
            v_filters = [base_filter]
            if dt_wm:
                v_filters.append(dt_wm)
            if has_subs and subtitle_ass_path:
                v_filters.append(get_ass_filter_string(subtitle_ass_path, fonts_dir=fonts_dir))
            if dt_hook:
                v_filters.append(dt_hook)
            if dt_cta:
                v_filters.append(dt_cta)
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

        if is_multi_input:
            cmd = [
                "ffmpeg",
                "-y",
                *input_args,
                "-t", f"{duration_sec:.3f}",
                "-filter_complex", filter_complex_arg,
                "-map", "[v_out]",
                "-map", "[a_out]",
                "-avoid_negative_ts", "make_zero",
                "-async", "1",
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
        else:
            cmd = [
                "ffmpeg",
                "-y",
                "-ss", f"{start_sec:.3f}",
                "-i", str(source_video_path),
                "-t", f"{duration_sec:.3f}",
                "-vf", video_filter_arg,
                "-af", audio_filter_arg,
                "-avoid_negative_ts", "make_zero",
                "-async", "1",
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
            global_event_bus.publish_sync(PlatformEvent(
                category="rendering",
                level="INFO",
                title="Compositor Rendering Started",
                message=f"Rendering short '{output_filename}' ({duration_sec:.1f}s) via FFmpeg ({encoder_name})...",
                stage_progress_pct=20.0,
                details={"filename": output_filename, "encoder": encoder_name, "duration_sec": duration_sec},
            ))
        except Exception:
            pass
        try:
            logger.info("Executing FFmpeg render for %s (encoder: %s, crf: %s, preset: %s, bitrate: %s, fps: %s, timeout: %0.1fs)...", output_filename, encoder_name, eff_crf, eff_preset, eff_audio_br, eff_fps, timeout_limit)
            result = SubprocessWatchdog.run(cmd, timeout_sec=timeout_limit, check=False, artifacts_to_cleanup=[output_path])
            if result.exit_code != 0:
                if encoder_name != "libx264":
                    global _CACHED_ENCODER
                    _CACHED_ENCODER = ("libx264", ["-preset", fallback_preset, "-crf", fallback_crf, "-profile:v", "high", "-level", "4.2"])
                    logger.warning("Hardware encoder %s failed. Cached libx264 for future renders. Retrying with CPU libx264...", encoder_name)
                    try:
                        global_event_bus.publish_sync(PlatformEvent(
                            category="rendering",
                            level="WARNING",
                            title="Hardware Encoder Fallback",
                            message=f"Encoder {encoder_name} failed. Retrying with CPU libx264 for {output_filename}.",
                        ))
                    except Exception:
                        pass
                    if is_multi_input:
                        cmd_fallback = [
                            "ffmpeg",
                            "-y",
                            *input_args,
                            "-t", f"{duration_sec:.3f}",
                            "-filter_complex", filter_complex_arg,
                            "-map", "[v_out]",
                            "-map", "[a_out]",
                            "-avoid_negative_ts", "make_zero",
                            "-async", "1",
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
                    else:
                        cmd_fallback = [
                            "ffmpeg",
                            "-y",
                            "-ss", f"{start_sec:.3f}",
                            "-i", str(source_video_path),
                            "-t", f"{duration_sec:.3f}",
                            "-vf", video_filter_arg,
                            "-af", audio_filter_arg,
                            "-avoid_negative_ts", "make_zero",
                            "-async", "1",
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
                    try:
                        global_event_bus.publish_sync(PlatformEvent(
                            category="rendering",
                            level="ERROR",
                            title="Compositor Rendering Failed",
                            message=f"FFmpeg render failed for {output_filename}: {result.stderr[-200:].strip()}",
                        ))
                    except Exception:
                        pass
                    raise RuntimeError(f"FFmpeg render failed: {result.stderr[-400:]}")

            logger.info("Render completed successfully: %s", output_path)
            try:
                global_event_bus.publish_sync(PlatformEvent(
                    category="rendering",
                    level="SUCCESS",
                    title="Compositor Rendering Completed",
                    message=f"FFmpeg composite render finished: {output_path.name}",
                    stage_progress_pct=100.0,
                    details={"output_path": str(output_path)},
                ))
            except Exception:
                pass
            return output_path
        finally:
            for tf in temp_files:
                if tf and tf.exists():
                    try:
                        tf.unlink()
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
            result = SubprocessWatchdog.run(cmd, timeout_sec=30.0)
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

    def render_clip(self, *args, **kwargs) -> Path:
        """
        Alias for render_short supporting custom cover frame and thumbnail injection (Enhancement 7).
        """
        return self.render_short(*args, **kwargs)

    def inject_cover_frame(
        self,
        video_path: Path,
        cover_image_path: Path,
        output_path: Optional[Path] = None,
    ) -> Path:
        """
        Injects a cover frame as frame 0 (first 0.033s) into an existing rendered MP4 video (Enhancement 7).
        """
        video_p = Path(video_path)
        target_p = Path(output_path) if output_path else video_p.parent / f"{video_p.stem}_cover.mp4"
        temp_out = target_p.parent / f"{target_p.stem}_tmp_cov.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_p),
            "-i", str(cover_image_path),
            "-filter_complex",
            "[1:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[cov];"
            "[0:v][cov]overlay=0:0:enable='between(t,0,0.033)'[outv]",
            "-map", "[outv]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-c:a", "copy",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(temp_out),
        ]
        res = SubprocessWatchdog.run(cmd, timeout_sec=60.0, check=False)
        if res.returncode != 0 or not temp_out.exists():
            if temp_out.exists():
                temp_out.unlink(missing_ok=True)
            raise RuntimeError(f"Failed to inject cover frame into video: {getattr(res, 'stderr', '')}")
        from shorts_engine.utils.fs import atomic_replace
        atomic_replace(temp_out, target_p)
        return target_p

