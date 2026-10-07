"""
Adaptive Hardware Acceleration Engine:
Probes and configures GPU-accelerated video encoding pipelines
(NVIDIA NVENC, Intel QuickSync QSV, AMD AMF) with seamless CPU libx264 fallback.
"""

import logging
from typing import Dict, List, Optional, Tuple

from shorts_engine.config.settings import settings
from shorts_engine.services.execution.watchdog import SubprocessWatchdog

logger = logging.getLogger(__name__)


class HardwareEncoderProber:
    """
    Probes system FFmpeg installation for hardware encoding capabilities.
    Maintains cached optimal encoder discovery to eliminate redundant process overhead.
    """

    _instance: Optional["HardwareEncoderProber"] = None

    def __init__(self):
        self._cached_optimal: Optional[Tuple[str, List[str]]] = None
        self._probe_cache: Dict[str, bool] = {}

    @classmethod
    def get_instance(cls) -> "HardwareEncoderProber":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Resets the singleton instance (useful for testing)."""
        cls._instance = None

    def reset_cache(self) -> None:
        """Clears cached encoder detection and test results."""
        self._cached_optimal = None
        self._probe_cache.clear()

    def test_encoder(self, encoder_name: str, timeout_sec: float = 2.0) -> bool:
        """
        Executes a 1-frame probe test using SubprocessWatchdog to verify
        that the encoder opens cleanly without device or driver initialization failure.
        """
        if encoder_name in self._probe_cache:
            return self._probe_cache[encoder_name]

        cmd = [
            "ffmpeg",
            "-v", "error",
            "-f", "lavfi",
            "-i", "testsrc=duration=0.04:size=64x64:rate=25",
            "-c:v", encoder_name,
            "-f", "null",
            "-",
        ]
        try:
            proc = SubprocessWatchdog.run(cmd, timeout_sec=timeout_sec, check=False)
            success = proc.returncode == 0
            self._probe_cache[encoder_name] = success
            return success
        except Exception as e:
            logger.debug("Hardware probe for %s failed with exception: %s", encoder_name, e)
            self._probe_cache[encoder_name] = False
            return False

    def get_available_ffmpeg_encoders(self) -> str:
        """Queries FFmpeg for compiled-in encoder support."""
        try:
            proc = SubprocessWatchdog.run(["ffmpeg", "-encoders"], timeout_sec=5.0, check=False)
            return proc.stdout.lower()
        except Exception as e:
            logger.warning("Could not list FFmpeg encoders: %s", e)
            return ""

    def get_encoder_args(
        self,
        encoder_name: str,
        crf: Optional[int] = None,
        preset: Optional[str] = None,
        bitrate: Optional[str] = None,
    ) -> List[str]:
        """
        Returns optimized encoder arguments conforming to broadcast specifications.
        - NVIDIA CUDA/NVENC: h264_nvenc with -preset p5, -cq 19, -b:v 12M
        - Intel QuickSync: h264_qsv with -preset medium, -global_quality 20, -b:v 12M
        - AMD AMF: h264_amf with -quality balanced, -rc cqp, -qp_p 20, -b:v 12M
        - CPU Software Fallback: libx264 with -preset fast, -crf 19, -threads 0
        """
        eff_bitrate = bitrate or "12M"

        if encoder_name == "h264_nvenc":
            eff_preset = preset or "p5"
            eff_cq = str(crf if crf is not None else 19)
            return ["-preset", eff_preset, "-cq", eff_cq, "-b:v", eff_bitrate]

        elif encoder_name == "h264_qsv":
            eff_preset = preset or "medium"
            eff_gq = str(crf if crf is not None else 20)
            return ["-preset", eff_preset, "-global_quality", eff_gq, "-b:v", eff_bitrate]

        elif encoder_name == "h264_amf":
            eff_quality = preset or "balanced"
            eff_qp = str(crf if crf is not None else 20)
            return ["-quality", eff_quality, "-rc", "cqp", "-qp_p", eff_qp, "-b:v", eff_bitrate]

        else:
            # libx264 software fallback
            eff_preset = preset or "fast"
            if eff_preset in ("p4", "p5", "p6", "p7"):
                eff_preset = "fast"
            eff_crf = str(crf if crf is not None else 19)
            return [
                "-preset", eff_preset,
                "-crf", eff_crf,
                "-threads", "0",
                "-profile:v", "high",
                "-level", "4.2",
            ]

    def get_hwaccel_args(self, encoder_name: str) -> List[str]:
        """Returns input hardware acceleration flags for decoders/filters."""
        if encoder_name == "h264_nvenc":
            return ["-hwaccel", "cuda"]
        elif encoder_name == "h264_qsv":
            return ["-hwaccel", "qsv"]
        elif encoder_name == "h264_amf":
            return ["-hwaccel", "d3d11va"]
        return []

    def mark_encoder_failed(self, encoder_name: str) -> None:
        """
        Marks an encoder as failed (e.g. after a runtime crash or OOM during render)
        and invalidates cached optimal encoder so subsequent calls fall back.
        """
        self._probe_cache[encoder_name] = False
        self._cached_optimal = None
        logger.warning("Hardware encoder '%s' marked as failed. Falling back.", encoder_name)

    def get_optimal_encoder(self, force_refresh: bool = False) -> Tuple[str, List[str]]:
        """
        Probes candidate hardware encoders in priority order:
        1. NVIDIA CUDA/NVENC (h264_nvenc)
        2. Intel QuickSync QSV (h264_qsv)
        3. AMD AMF (h264_amf)
        4. CPU Software Fallback (libx264)
        """
        if not force_refresh and self._cached_optimal is not None:
            return self._cached_optimal

        # Check for explicit override in settings
        override = getattr(settings, "HARDWARE_ENCODER_OVERRIDE", None)
        if override:
            args = self.get_encoder_args(override)
            self._cached_optimal = (override, args)
            return self._cached_optimal

        # Check if hardware acceleration is globally disabled
        hw_enabled = getattr(settings, "HARDWARE_ACCELERATION_ENABLED", True)
        if not hw_enabled:
            fallback_args = self.get_encoder_args("libx264")
            self._cached_optimal = ("libx264", fallback_args)
            return self._cached_optimal

        encoders_text = self.get_available_ffmpeg_encoders()

        candidates = [
            ("h264_nvenc", "NVIDIA CUDA/NVENC"),
            ("h264_qsv", "Intel QuickSync"),
            ("h264_amf", "AMD AMF"),
        ]

        for enc, desc in candidates:
            if enc in encoders_text and self.test_encoder(enc):
                logger.info("Hardware acceleration validated: using %s (%s).", desc, enc)
                args = self.get_encoder_args(enc)
                self._cached_optimal = (enc, args)
                return self._cached_optimal

        logger.info("No active hardware encoder detected. Using multi-threaded CPU libx264.")
        fallback_args = self.get_encoder_args("libx264")
        self._cached_optimal = ("libx264", fallback_args)
        return self._cached_optimal


# Global singleton accessors
_prober = HardwareEncoderProber.get_instance()


def get_optimal_encoder(force_refresh: bool = False) -> Tuple[str, List[str]]:
    """Convenience functional interface to optimal encoder detection."""
    return HardwareEncoderProber.get_instance().get_optimal_encoder(force_refresh=force_refresh)


def get_encoder_args(
    encoder_name: str,
    crf: Optional[int] = None,
    preset: Optional[str] = None,
    bitrate: Optional[str] = None,
) -> List[str]:
    """Convenience functional interface to encoder flags generation."""
    return HardwareEncoderProber.get_instance().get_encoder_args(
        encoder_name=encoder_name,
        crf=crf,
        preset=preset,
        bitrate=bitrate,
    )
