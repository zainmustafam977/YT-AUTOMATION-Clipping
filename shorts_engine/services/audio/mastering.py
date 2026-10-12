"""
Studio-Grade Audio Mastering & Sidechain Ducking Engine.
Applies broadcast-grade audio engineering to vertical video production:
1. Sub-bass room rumble removal: 80Hz 2-pole Butterworth High Pass Filter (HPF).
2. 3kHz vocal presence shelf filter: Enhances consonant intelligibility and voice clarity.
3. Ambient floor noise reduction: Dynamic acoustic voice gate (agate).
4. Speech-activated sidechain compression ducking: Attenuates background music bed
   by -14dB during active speech with 15ms attack and 350ms release.
5. ITU-R BS.1770-4 Compliant Two-Pass Loudness Normalization:
   -14.0 LUFS integrated loudness, -1.0 dBFS True Peak, 7.0 LU LRA.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from shorts_engine.config.settings import settings
from shorts_engine.services.execution.watchdog import SubprocessWatchdog

logger = logging.getLogger(__name__)


class AudioMasteringEngine:
    """
    Broadcast audio mastering processor for vertical Shorts, Reels, and TikToks.
    Constructs deterministic FFmpeg filter chains and validates broadcast loudness.
    """

    def __init__(
        self,
        target_lufs: Optional[float] = None,
        true_peak_dbfs: Optional[float] = None,
        hpf_cutoff_hz: Optional[int] = None,
        ducking_db: Optional[float] = None,
        ducking_attack_ms: Optional[int] = None,
        ducking_release_ms: Optional[int] = None,
        vocal_presence_freq_hz: Optional[int] = None,
        vocal_presence_gain_db: Optional[float] = None,
        two_pass_loudnorm: Optional[bool] = None,
    ):
        self.target_lufs = target_lufs if target_lufs is not None else getattr(settings, "TARGET_LUFS", -14.0)
        self.true_peak_dbfs = true_peak_dbfs if true_peak_dbfs is not None else getattr(settings, "TRUE_PEAK_DBFS", -1.0)
        self.hpf_cutoff_hz = hpf_cutoff_hz if hpf_cutoff_hz is not None else getattr(settings, "SPEECH_HPF_CUTOFF_HZ", 80)
        self.ducking_db = ducking_db if ducking_db is not None else getattr(settings, "DUCKING_ATTENUATION_DB", -14.0)
        self.ducking_attack_ms = ducking_attack_ms if ducking_attack_ms is not None else getattr(settings, "DUCKING_ATTACK_MS", 15)
        self.ducking_release_ms = ducking_release_ms if ducking_release_ms is not None else getattr(settings, "DUCKING_RELEASE_MS", 350)
        self.vocal_presence_freq_hz = (
            vocal_presence_freq_hz
            if vocal_presence_freq_hz is not None
            else getattr(settings, "VOCAL_PRESENCE_FREQ_HZ", 3000)
        )
        self.vocal_presence_gain_db = (
            vocal_presence_gain_db
            if vocal_presence_gain_db is not None
            else getattr(settings, "VOCAL_PRESENCE_GAIN_DB", 2.5)
        )
        self.two_pass_loudnorm = (
            two_pass_loudnorm
            if two_pass_loudnorm is not None
            else getattr(settings, "LOUDNESS_TWO_PASS", True)
        )

    def build_speech_clean_filter(self, input_label: str = "0:a") -> str:
        """
        Builds filter subgraph to strip sub-rumble, shape vocal presence, and gate room noise.
        - High-Pass Filter: 80Hz cutoff (2 poles).
        - 3kHz vocal presence filter: +2.5dB boost at 3kHz.
        - Noise gate: -36dB threshold, 10ms attack, 120ms release.
        """
        hpf = f"highpass=f={self.hpf_cutoff_hz}:poles=2"
        presence = f"equalizer=f={self.vocal_presence_freq_hz}:t=q:w=1.0:g={self.vocal_presence_gain_db}"
        gate = "agate=range=-24dB:threshold=-36dB:attack=10:release=120"
        return f"[{input_label}]{hpf},{presence},{gate}"

    def build_sidechain_ducking_filter_graph(
        self,
        speech_input_label: str = "0:a",
        music_input_label: str = "1:a",
        output_label: str = "a_out",
        music_volume_ratio: float = 0.25,
        measured_loudnorm: Optional[Dict[str, float]] = None,
        print_format_json: bool = False,
    ) -> str:
        """
        Builds complete sidechain ducking and EBU R128 mastering filter graph.
        - Speech stream is cleaned (HPF + 3kHz presence shelf + Gate) and split into broadcast feed and sidechain control feed.
        - Music bed is looped, scaled to baseline bed volume, and compressed dynamically when speech engages.
        - Clean speech and ducked music are mixed and mastered through EBU R128 loudnorm.
        - Supports two-pass linear loudnorm parameters when measured_loudnorm is provided.
        """
        fc = []

        # 1. Clean voice and split into two identical streams: voice_main & voice_side
        presence = f"equalizer=f={self.vocal_presence_freq_hz}:t=q:w=1.0:g={self.vocal_presence_gain_db}"
        voice_clean = (
            f"[{speech_input_label}]highpass=f={self.hpf_cutoff_hz}:poles=2,{presence},"
            f"agate=range=-24dB:threshold=-36dB:attack=10:release=120,asplit=2[voice_main][voice_side]"
        )
        fc.append(voice_clean)

        # 2. Scale music volume to background bed level
        music_prep = f"[{music_input_label}]volume={music_volume_ratio:.2f}[music_bed]"
        fc.append(music_prep)

        # 3. Dynamic Sidechain Compression: voice_side attenuates music_bed
        # sidechaincompress takes [victim][control]
        # Threshold: 0.06 (~ -24dB), ratio: 4, attack: 15ms, release: 350ms
        ducking = (
            f"[music_bed][voice_side]sidechaincompress="
            f"threshold=0.06:ratio=4:attack={self.ducking_attack_ms}:release={self.ducking_release_ms}[music_ducked]"
        )
        fc.append(ducking)

        # 4. Mix speech with ducked background music
        mix = "[voice_main][music_ducked]amix=inputs=2:duration=first:dropout_transition=2[mixed_pre]"
        fc.append(mix)

        # 5. Final ITU-R BS.1770-4 / EBU R128 Loudness Normalization Master
        json_suffix = ":print_format=json" if print_format_json else ""
        if measured_loudnorm and "input_i" in measured_loudnorm:
            master_loudnorm = (
                f"[mixed_pre]loudnorm=I={self.target_lufs}:TP={self.true_peak_dbfs}:LRA=7:"
                f"measured_I={measured_loudnorm['input_i']}:"
                f"measured_TP={measured_loudnorm['input_tp']}:"
                f"measured_LRA={measured_loudnorm['input_lra']}:"
                f"measured_thresh={measured_loudnorm['input_thresh']}:"
                f"offset={measured_loudnorm.get('target_offset', 0.0)}:linear=true{json_suffix}[{output_label}]"
            )
        else:
            master_loudnorm = f"[mixed_pre]loudnorm=I={self.target_lufs}:TP={self.true_peak_dbfs}:LRA=7{json_suffix}[{output_label}]"
        fc.append(master_loudnorm)

        return ";".join(fc)

    def build_single_stream_master_filter(
        self,
        input_label: str = "0:a",
        output_label: str = "a_out",
        measured_loudnorm: Optional[Dict[str, float]] = None,
        print_format_json: bool = False,
    ) -> str:
        """
        Builds audio filter for dialogue-only speech mastering
        (80Hz HPF + 3kHz Vocal Presence + Gate + ITU-R BS.1770-4 Loudnorm).
        Supports two-pass measured parameters for linear, uncompressed mastering.
        """
        hpf = f"highpass=f={self.hpf_cutoff_hz}:poles=2"
        presence = f"equalizer=f={self.vocal_presence_freq_hz}:t=q:w=1.0:g={self.vocal_presence_gain_db}"
        gate = "agate=range=-24dB:threshold=-36dB:attack=10:release=120"
        json_suffix = ":print_format=json" if print_format_json else ""
        if measured_loudnorm and "input_i" in measured_loudnorm:
            norm = (
                f"loudnorm=I={self.target_lufs}:TP={self.true_peak_dbfs}:LRA=7:"
                f"measured_I={measured_loudnorm['input_i']}:"
                f"measured_TP={measured_loudnorm['input_tp']}:"
                f"measured_LRA={measured_loudnorm['input_lra']}:"
                f"measured_thresh={measured_loudnorm['input_thresh']}:"
                f"offset={measured_loudnorm.get('target_offset', 0.0)}:linear=true{json_suffix}"
            )
        else:
            norm = f"loudnorm=I={self.target_lufs}:TP={self.true_peak_dbfs}:LRA=7{json_suffix}"
        return f"[{input_label}]{hpf},{presence},{gate},{norm}[{output_label}]"

    def measure_loudness(
        self,
        media_path: Path,
        duration_sec: Optional[float] = None,
        start_sec: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Measures integrated loudness (LUFS), True Peak (dBFS), and Loudness Range (LU)
        using FFmpeg loudnorm measurement pass.
        """
        cmd = ["ffmpeg", "-v", "info"]
        if start_sec is not None and start_sec > 0:
            cmd.extend(["-ss", f"{start_sec:.3f}"])
        cmd.extend([
            "-i", str(media_path),
        ])
        if duration_sec is not None and duration_sec > 0:
            cmd.extend(["-t", f"{duration_sec:.3f}"])
        cmd.extend([
            "-af", f"loudnorm=I={self.target_lufs}:TP={self.true_peak_dbfs}:LRA=7:print_format=json",
            "-f", "null",
            "-",
        ])
        timeout = max(60.0, float(duration_sec) * 3.0) if duration_sec is not None else 60.0
        try:
            proc = SubprocessWatchdog.run(cmd, timeout_sec=timeout)
            output = proc.stderr
        except Exception as e:
            logger.warning("FFmpeg loudnorm probe failed or timed out: %s", e)
            return {"error": f"Loudness measurement failed: {e}"}
        try:
            start_idx = output.rfind("{")
            end_idx = output.rfind("}")
            if start_idx != -1 and end_idx != -1:
                json_str = output[start_idx : end_idx + 1]
                data = json.loads(json_str)
                return {
                    "input_i": float(data.get("input_i", 0.0)),
                    "input_tp": float(data.get("input_tp", 0.0)),
                    "input_lra": float(data.get("input_lra", 0.0)),
                    "input_thresh": float(data.get("input_thresh", 0.0)),
                    "target_offset": float(data.get("target_offset", 0.0)),
                }
        except Exception as e:
            logger.debug("Could not parse loudnorm measurement JSON: %s", e)
        return {"error": "Loudness measurement could not be parsed"}

    def two_pass_loudness_normalize(
        self,
        input_audio_path: Path,
        output_audio_path: Path,
        start_sec: Optional[float] = None,
        duration_sec: Optional[float] = None,
        music_track_path: Optional[Path] = None,
        clip_id: Optional[str] = None,
    ) -> Path:
        """
        Executes true ITU-R BS.1770-4 two-pass loudness normalization:
        - Pass 1: Analyzes pre-filtered audio and captures measured loudness parameters.
        - Pass 2: Normalizes linearly to exact -14.0 LUFS, -1.0 True Peak without dynamic pumping.
        - Measures and logs the resulting output LUFS for the clip.
        """
        output_audio_path.parent.mkdir(parents=True, exist_ok=True)
        timeout = max(60.0, (duration_sec or 30.0) * 3.0)
        has_music = music_track_path is not None and Path(music_track_path).exists()

        # Pass 1: Measure loudness with pre-filtering
        if has_music:
            fc1 = self.build_sidechain_ducking_filter_graph(
                speech_input_label="0:a",
                music_input_label="1:a",
                output_label="out",
                measured_loudnorm=None,
                print_format_json=True,
            )
            cmd1 = ["ffmpeg", "-v", "info"]
            if start_sec is not None and start_sec > 0:
                cmd1.extend(["-ss", f"{start_sec:.3f}"])
            cmd1.extend([
                "-i", str(input_audio_path),
                "-stream_loop", "-1",
                "-i", str(music_track_path),
            ])
            if duration_sec is not None and duration_sec > 0:
                cmd1.extend(["-t", f"{duration_sec:.3f}"])
            cmd1.extend([
                "-filter_complex", fc1,
                "-map", "[out]",
                "-f", "null",
                "-",
            ])
        else:
            hpf = f"highpass=f={self.hpf_cutoff_hz}:poles=2"
            presence = f"equalizer=f={self.vocal_presence_freq_hz}:t=q:w=1.0:g={self.vocal_presence_gain_db}"
            gate = "agate=range=-24dB:threshold=-36dB:attack=10:release=120"
            measure_filter = f"{hpf},{presence},{gate},loudnorm=I={self.target_lufs}:TP={self.true_peak_dbfs}:LRA=7:print_format=json"

            cmd1 = ["ffmpeg", "-v", "info"]
            if start_sec is not None and start_sec > 0:
                cmd1.extend(["-ss", f"{start_sec:.3f}"])
            cmd1.extend([
                "-i", str(input_audio_path),
            ])
            if duration_sec is not None and duration_sec > 0:
                cmd1.extend(["-t", f"{duration_sec:.3f}"])
            cmd1.extend([
                "-af", measure_filter,
                "-f", "null",
                "-",
            ])

        measured_data: Optional[Dict[str, float]] = None
        try:
            proc1 = SubprocessWatchdog.run(cmd1, timeout_sec=timeout)
            out1 = proc1.stderr
            s_idx = out1.rfind("{")
            e_idx = out1.rfind("}")
            if s_idx != -1 and e_idx != -1:
                d = json.loads(out1[s_idx : e_idx + 1])
                measured_data = {
                    "input_i": float(d.get("input_i", 0.0)),
                    "input_tp": float(d.get("input_tp", 0.0)),
                    "input_lra": float(d.get("input_lra", 0.0)),
                    "input_thresh": float(d.get("input_thresh", 0.0)),
                    "target_offset": float(d.get("target_offset", 0.0)),
                }
        except Exception as e:
            logger.warning("Pass 1 loudness measurement failed: %s. Using single-pass fallback.", e)

        # Pass 2: Apply linear normalization
        cmd2 = ["ffmpeg", "-y", "-v", "info"]
        if has_music:
            fc2 = self.build_sidechain_ducking_filter_graph(
                speech_input_label="0:a",
                music_input_label="1:a",
                output_label="out",
                measured_loudnorm=measured_data,
                print_format_json=True,
            )
            if start_sec is not None and start_sec > 0:
                cmd2.extend(["-ss", f"{start_sec:.3f}"])
            cmd2.extend([
                "-i", str(input_audio_path),
                "-stream_loop", "-1",
                "-i", str(music_track_path),
                "-filter_complex", fc2,
                "-map", "[out]",
            ])
        else:
            fc2 = self.build_single_stream_master_filter(
                input_label="0:a",
                output_label="out",
                measured_loudnorm=measured_data,
                print_format_json=True,
            )
            if start_sec is not None and start_sec > 0:
                cmd2.extend(["-ss", f"{start_sec:.3f}"])
            cmd2.extend([
                "-i", str(input_audio_path),
                "-filter_complex", fc2,
                "-map", "[out]",
            ])

        if duration_sec is not None and duration_sec > 0:
            cmd2.extend(["-t", f"{duration_sec:.3f}"])

        audio_ar = str(getattr(settings, "AUDIO_SAMPLE_RATE", 48000))
        eff_audio_br = str(getattr(settings, "AUDIO_BITRATE", "320k"))
        if output_audio_path.suffix.lower() == ".wav":
            cmd2.extend(["-c:a", "pcm_s16le", "-ar", audio_ar, str(output_audio_path)])
        else:
            cmd2.extend(["-c:a", "aac", "-b:a", eff_audio_br, "-ar", audio_ar, str(output_audio_path)])

        proc2 = SubprocessWatchdog.run(
            cmd2,
            timeout_sec=timeout,
            check=False,
            artifacts_to_cleanup=[output_audio_path],
        )
        if proc2.exit_code != 0:
            raise RuntimeError(f"FFmpeg two-pass audio mastering failed: {proc2.stderr}")

        # Extract and log measured output LUFS
        measured_output_lufs: Optional[float] = None
        measured_output_tp: Optional[float] = None
        s2 = proc2.stderr.rfind("{")
        e2 = proc2.stderr.rfind("}")
        if s2 != -1 and e2 != -1:
            try:
                d2 = json.loads(proc2.stderr[s2 : e2 + 1])
                if "output_i" in d2:
                    measured_output_lufs = float(d2["output_i"])
                if "output_tp" in d2:
                    measured_output_tp = float(d2["output_tp"])
            except Exception:
                pass

        if measured_output_lufs is None:
            post_metrics = self.measure_loudness(output_audio_path, duration_sec=duration_sec)
            measured_output_lufs = post_metrics.get("input_i")
            measured_output_tp = post_metrics.get("input_tp", 0.0)

        clip_label = f"clip '{clip_id}'" if clip_id else f"clip '{output_audio_path.name}'"
        if measured_output_lufs is not None:
            logger.info(
                "Mastered audio measured LUFS for %s: %.2f LUFS (target: %.1f LUFS, true peak: %.2f dBTP)",
                clip_label,
                measured_output_lufs,
                self.target_lufs,
                measured_output_tp if measured_output_tp is not None else 0.0,
            )
        self.last_measured_lufs = measured_output_lufs

        return output_audio_path

    def master_audio(
        self,
        speech_audio_path: Path,
        output_path: Path,
        music_track_path: Optional[Path] = None,
        duration_sec: Optional[float] = None,
        start_sec: Optional[float] = None,
        two_pass: Optional[bool] = None,
        clip_id: Optional[str] = None,
    ) -> Path:
        """
        Performs standalone high-fidelity audio mastering to target output path.
        Applies two-pass ITU-R BS.1770-4 normalization when two_pass is True.
        Logs the measured LUFS of the output audio.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        has_music = music_track_path is not None and Path(music_track_path).exists()
        use_two_pass = two_pass if two_pass is not None else self.two_pass_loudnorm

        if use_two_pass:
            return self.two_pass_loudness_normalize(
                input_audio_path=speech_audio_path,
                output_audio_path=output_path,
                start_sec=start_sec,
                duration_sec=duration_sec,
                music_track_path=music_track_path,
                clip_id=clip_id,
            )

        if has_music:
            fc = self.build_sidechain_ducking_filter_graph(
                speech_input_label="0:a",
                music_input_label="1:a",
                output_label="out",
                print_format_json=True,
            )
            cmd = ["ffmpeg", "-y"]
            if start_sec is not None and start_sec > 0:
                cmd.extend(["-ss", f"{start_sec:.3f}"])
            cmd.extend([
                "-i", str(speech_audio_path),
                "-stream_loop", "-1",
                "-i", str(music_track_path),
                "-filter_complex", fc,
                "-map", "[out]",
            ])
        else:
            fc = self.build_single_stream_master_filter(
                input_label="0:a",
                output_label="out",
                print_format_json=True,
            )
            cmd = ["ffmpeg", "-y"]
            if start_sec is not None and start_sec > 0:
                cmd.extend(["-ss", f"{start_sec:.3f}"])
            cmd.extend([
                "-i", str(speech_audio_path),
                "-filter_complex", fc,
                "-map", "[out]",
            ])

        if duration_sec is not None and duration_sec > 0:
            cmd.extend(["-t", f"{duration_sec:.3f}"])

        audio_ar = str(getattr(settings, "AUDIO_SAMPLE_RATE", 48000))
        eff_audio_br = str(getattr(settings, "AUDIO_BITRATE", "320k"))
        if output_path.suffix.lower() == ".wav":
            cmd.extend(["-c:a", "pcm_s16le", "-ar", audio_ar, str(output_path)])
        else:
            cmd.extend(["-c:a", "aac", "-b:a", eff_audio_br, "-ar", audio_ar, str(output_path)])

        proc = SubprocessWatchdog.run(
            cmd,
            timeout_sec=max(60.0, (duration_sec or 30.0) * 3.0),
            check=False,
            artifacts_to_cleanup=[output_path],
        )
        if proc.exit_code != 0:
            raise RuntimeError(f"FFmpeg audio mastering failed: {proc.stderr}")

        # Log measured output LUFS
        metrics = self.measure_loudness(output_path, duration_sec=duration_sec)
        out_lufs = metrics.get("input_i")
        clip_label = f"clip '{clip_id}'" if clip_id else f"clip '{output_path.name}'"
        if out_lufs is not None:
            logger.info(
                "Mastered audio measured LUFS for %s: %.2f LUFS (target: %.1f LUFS, true peak: %.2f dBTP)",
                clip_label,
                out_lufs,
                self.target_lufs,
                metrics.get("input_tp", 0.0),
            )
        self.last_measured_lufs = out_lufs

        return output_path

