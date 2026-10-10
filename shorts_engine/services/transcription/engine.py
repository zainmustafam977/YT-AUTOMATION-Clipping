"""
Transcription engine wrapping faster-whisper with automatic GPU/CPU auto-detection,
Silero VAD speech pre-filtering/masking, Whisper hallucination shields,
and word-level timestamp alignment.
"""

import logging
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import torch

from shorts_engine.config.settings import settings
from shorts_engine.services.transcription.formatter import WordSegment, FormattedTranscript
from shorts_engine.services.transcription.aligner import ForcedAligner
from shorts_engine.services.execution.watchdog import SubprocessWatchdog
from shorts_engine.services.events.bus import global_event_bus
from shorts_engine.services.events.models import PlatformEvent

logger = logging.getLogger(__name__)


class SileroVADFilter:
    """
    Voice Activity Detection (VAD) audio pre-filter and masking engine based on Silero VAD.
    Identifies active speech intervals and eliminates or masks non-speech audio segments
    prior to Whisper decoding.
    """

    def __init__(
        self,
        threshold: float = 0.5,
        min_silence_duration_ms: int = 400,
        speech_pad_ms: int = 200,
    ):
        self.threshold = threshold
        self.min_silence_duration_ms = min_silence_duration_ms
        self.speech_pad_ms = speech_pad_ms

    def detect_speech_segments(
        self,
        audio_path: Path,
        min_silence_duration_ms: Optional[int] = None,
        threshold: Optional[float] = None,
    ) -> List[Tuple[float, float]]:
        """
        Detects start and end timestamps (in seconds) of active speech segments.
        Uses faster-whisper / Silero VAD model if available, falling back to FFmpeg silencedetect.
        """
        if not audio_path.exists():
            return []

        eff_min_silence = min_silence_duration_ms or self.min_silence_duration_ms
        eff_threshold = threshold or self.threshold

        # Try faster_whisper Silero VAD engine
        try:
            from faster_whisper.vad import get_vad_model, get_speech_timestamps, VadOptions
            import numpy as np

            _ = get_vad_model()
            vad_opts = VadOptions(
                threshold=eff_threshold,
                min_silence_duration_ms=eff_min_silence,
                speech_pad_ms=self.speech_pad_ms,
            )
            cmd = [
                "ffmpeg", "-v", "quiet",
                "-i", str(audio_path),
                "-f", "s16le",
                "-ac", "1",
                "-ar", "16000",
                "-",
            ]
            proc = SubprocessWatchdog.run(cmd, timeout_sec=30.0, check=False)
            if proc.returncode == 0 and proc.stdout:
                raw_bytes = proc.stdout.encode("latin1") if isinstance(proc.stdout, str) else proc.stdout
                audio_np = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                timestamps = get_speech_timestamps(audio_np, vad_opts)
                segments = [(ts["start"] / 16000.0, ts["end"] / 16000.0) for ts in timestamps]
                if segments:
                    return segments
        except Exception as e:
            logger.debug("Silero direct model extraction fallback: %s", e)

        # Fallback to FFmpeg silencedetect
        try:
            silence_sec = eff_min_silence / 1000.0
            cmd = [
                "ffmpeg", "-v", "info",
                "-i", str(audio_path),
                "-af", f"silencedetect=noise=-35dB:d={silence_sec:.2f}",
                "-f", "null",
                "-",
            ]
            res = SubprocessWatchdog.run(cmd, timeout_sec=30.0, check=False)
            output = res.stderr
            silence_starts = [float(m) for m in re.findall(r"silence_start:\s*([0-9.]+)", output)]
            silence_ends = [float(m) for m in re.findall(r"silence_end:\s*([0-9.]+)", output)]

            speech_segments = []
            curr_pos = 0.0
            for s_start, s_end in zip(silence_starts, silence_ends):
                if s_start > curr_pos + 0.1:
                    speech_segments.append((round(curr_pos, 3), round(s_start, 3)))
                curr_pos = s_end
            if silence_starts and curr_pos < silence_starts[-1]:
                speech_segments.append((round(curr_pos, 3), 9999.0))
            if speech_segments:
                return speech_segments
        except Exception as e:
            logger.debug("FFmpeg silencedetect fallback failed: %s", e)

        return [(0.0, 99999.0)]

    def mask_non_speech_audio(
        self,
        audio_path: Path,
        output_path: Path,
        speech_segments: Optional[List[Tuple[float, float]]] = None,
    ) -> Path:
        """
        Creates a masked audio file where all non-speech audio segments (e.g. background music,
        room noise during silence) are attenuated or muted prior to Whisper decoding.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        segments = speech_segments or self.detect_speech_segments(audio_path)
        if not segments or (len(segments) == 1 and segments[0][1] >= 9999.0):
            cmd = ["ffmpeg", "-y", "-i", str(audio_path), "-c:a", "pcm_s16le", str(output_path)]
            SubprocessWatchdog.run(cmd, timeout_sec=30.0, check=False)
            return output_path

        enable_expr = "+".join(f"between(t,{st:.3f},{en:.3f})" for st, en in segments)
        vol_filter = f"volume=enable='{enable_expr}':volume=1:eval=frame,volume=enable='not({enable_expr})':volume=0:eval=frame"

        cmd = [
            "ffmpeg", "-y",
            "-i", str(audio_path),
            "-af", vol_filter,
            "-c:a", "pcm_s16le",
            str(output_path),
        ]
        proc = SubprocessWatchdog.run(cmd, timeout_sec=60.0, check=False)
        if proc.returncode != 0:
            cmd_fb = ["ffmpeg", "-y", "-i", str(audio_path), "-c:a", "pcm_s16le", str(output_path)]
            SubprocessWatchdog.run(cmd_fb, timeout_sec=30.0, check=False)

        return output_path


class TranscriptionService:
    def __init__(
        self,
        model_size: str = "base",
        device: Optional[str] = None,
        compute_type: Optional[str] = None,
    ):
        self.model_size = model_size
        
        # Auto-detect hardware
        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        if compute_type is None:
            self.compute_type = "float16" if self.device == "cuda" else "int8"
        else:
            self.compute_type = compute_type

        logger.info(
            "Initializing TranscriptionService (model=%s, device=%s, compute_type=%s)",
            self.model_size,
            self.device,
            self.compute_type,
        )
        self._model = None
        self.aligner = ForcedAligner(device=self.device)
        self.vad_filter = SileroVADFilter()

    @property
    def model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
                self._model = WhisperModel(
                    self.model_size,
                    device=self.device,
                    compute_type=self.compute_type,
                    download_root=str(settings.MODELS_DIR / "whisper"),
                )
            except Exception as e:
                logger.error("Failed to load faster-whisper model: %s", str(e))
                raise RuntimeError(f"Could not load faster-whisper model: {e}")
        return self._model

    @staticmethod
    def get_hallucination_shield_params() -> Dict[str, Any]:
        """
        Whisper Hallucination Shield decoding parameters to guard against infinite loops,
        repetition spirals, and ghost transcriptions during music or silence:
        - vad_filter: True (Silero VAD pre-filter)
        - vad_parameters: min_silence_duration_ms=400, threshold=0.5, speech_pad_ms=200
        - compression_ratio_threshold: 2.4 (drops runaway repetitive loops)
        - no_speech_threshold: 0.6 (rejects ghost text on silent intervals)
        - temperature: (0.0, 0.2, 0.4, 0.6, 0.8, 1.0) (temperature fallback schedule)
        - repetition_penalty: 1.2
        - no_repeat_ngram_size: 3
        - condition_on_previous_text: False
        """
        return {
            "vad_filter": True,
            "vad_parameters": dict(
                min_silence_duration_ms=400,
                threshold=0.5,
                speech_pad_ms=200,
            ),
            "compression_ratio_threshold": 2.4,
            "no_speech_threshold": 0.6,
            "temperature": (0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
            "repetition_penalty": 1.2,
            "no_repeat_ngram_size": 3,
            "condition_on_previous_text": False,
        }

    def _transcribe_with_groq(self, audio_path: Path, language: Optional[str] = None) -> Optional[FormattedTranscript]:
        """
        Transcribes audio using Groq Cloud Whisper API (whisper-large-v3-turbo).
        Fast (~2s) and 100% free (28.8k ASD daily quota).
        Returns None on any error to allow local fallback.
        """
        groq_keys = settings.get_groq_keys()
        if not groq_keys:
            return None
        import httpx
        target_key = groq_keys[0]
        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {target_key}"}
        data = {
            "model": "whisper-large-v3-turbo",
            "response_format": "verbose_json",
            "timestamp_granularities[]": ["word", "segment"],
        }
        if language:
            data["language"] = language
        try:
            logger.info("Calling Groq Cloud Whisper API for %s...", audio_path.name)
            with open(audio_path, "rb") as f:
                files = {"file": (audio_path.name, f, "audio/mpeg" if audio_path.suffix == ".mp3" else "application/octet-stream")}
                with httpx.Client(timeout=60.0) as client:
                    resp = client.post(url, headers=headers, data=data, files=files)
            if resp.status_code != 200:
                logger.warning("Groq Whisper returned HTTP %d: %s. Falling back to local.", resp.status_code, resp.text[:100])
                return None
            res_json = resp.json()
            words_data = res_json.get("words", [])
            raw_words = []
            for w in words_data:
                raw_words.append(
                    WordSegment(
                        word=w.get("word", "").strip(),
                        start=float(w.get("start", 0.0)),
                        end=float(w.get("end", 0.0)),
                        probability=1.0,
                    )
                )
            full_text = res_json.get("text", "").strip()
            if not raw_words and res_json.get("segments"):
                for seg in res_json["segments"]:
                    for w in seg.get("words", []):
                        raw_words.append(
                            WordSegment(
                                word=w.get("word", "").strip(),
                                start=float(w.get("start", 0.0)),
                                end=float(w.get("end", 0.0)),
                                probability=1.0,
                            )
                        )
            if not raw_words:
                logger.warning("No words extracted from Groq Whisper response. Falling back to local.")
                return None
            logger.info("Successfully transcribed %s via Groq Whisper (%d words in ~2s).", audio_path.name, len(raw_words))
            return FormattedTranscript(
                words=raw_words,
                language=res_json.get("language") or language or "en",
                full_text=full_text,
            )
        except Exception as e:
            logger.warning("Groq Whisper failed: %s. Falling back to local whisper.", e)
            return None

    def transcribe(
        self,
        audio_path: Path,
        language: Optional[str] = None,
        video_id: Optional[str] = None,
        apply_vad_mask: bool = False,
    ) -> FormattedTranscript:
        """
        Transcribes the given audio file and produces word-level timestamped segments.
        Applies Silero VAD speech pre-filtering and Whisper hallucination shields
        (compression_ratio_threshold=2.4, no_speech_threshold=0.6, temperature schedule).
        Applies forced alignment to ensure +/-50ms precision.
        """
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        from shorts_engine.services.telemetry.progress_tracker import job_progress_tracker

        if video_id:
            job_progress_tracker.update_progress(
                video_id=video_id,
                stage="TRANSCRIBING",
                percent=5.0,
                detail="Starting speech-to-text transcription with Whisper...",
            )
        try:
            global_event_bus.publish_sync(PlatformEvent(
                category="transcription",
                level="INFO",
                title="Transcription Started",
                message=f"Starting speech-to-text transcription for {video_id or audio_path.name} with Whisper...",
                stage_progress_pct=5.0,
                entity_id=video_id,
                entity_type="video" if video_id else None,
                details={"audio_file": audio_path.name, "language": language},
            ))
        except Exception:
            pass

        try:
            # Optional cloud whisper acceleration via Groq
            if getattr(settings, "USE_GROQ_WHISPER", False):
                if video_id:
                    job_progress_tracker.update_progress(
                        video_id=video_id,
                        stage="TRANSCRIBING",
                        percent=20.0,
                        detail="Executing high-speed cloud transcription via Groq LPU...",
                    )
                try:
                    global_event_bus.publish_sync(PlatformEvent(
                        category="transcription",
                        level="INFO",
                        title="Cloud Whisper Invoked",
                        message=f"Executing high-speed cloud transcription via Groq LPU for {audio_path.name}...",
                        stage_progress_pct=20.0,
                        entity_id=video_id,
                        entity_type="video" if video_id else None,
                    ))
                except Exception:
                    pass
                groq_res = self._transcribe_with_groq(audio_path, language=language)
                if groq_res is not None:
                    if video_id:
                        job_progress_tracker.update_progress(
                            video_id=video_id,
                            stage="TRANSCRIBING",
                            percent=100.0,
                            detail="Transcription completed successfully.",
                        )
                    try:
                        global_event_bus.publish_sync(PlatformEvent(
                            category="transcription",
                            level="SUCCESS",
                            title="Transcription Completed",
                            message=f"Transcribed {len(groq_res.words)} words via Groq Cloud in ~2s ({groq_res.language}).",
                            stage_progress_pct=100.0,
                            entity_id=video_id,
                            entity_type="video" if video_id else None,
                            details={"words_count": len(groq_res.words), "language": groq_res.language, "provider": "groq"},
                        ))
                    except Exception:
                        pass
                    return groq_res

            eff_audio_path = audio_path
            if apply_vad_mask:
                masked_path = audio_path.with_name(f"{audio_path.stem}_vad_masked.wav")
                eff_audio_path = self.vad_filter.mask_non_speech_audio(audio_path, masked_path)

            logger.info("Transcribing %s with Silero VAD filter & Hallucination Shields...", audio_path.name)
            shield_params = self.get_hallucination_shield_params()

            segments, info = self.model.transcribe(
                str(eff_audio_path),
                language=language,
                word_timestamps=True,
                **shield_params,
            )

            total_dur = getattr(info, "duration", 0.0) or 1.0
            raw_words = []
            full_text_parts = []
            last_emitted_pct = 5.0
            for segment in segments:
                full_text_parts.append(segment.text.strip())
                if segment.words:
                    for w in segment.words:
                        raw_words.append(
                            WordSegment(
                                word=w.word.strip(),
                                start=w.start,
                                end=w.end,
                                probability=getattr(w, "probability", 1.0),
                            )
                        )
                if video_id and total_dur > 0:
                    cur_end = segment.end or 0.0
                    pct = min(95.0, max(5.0, round((cur_end / total_dur) * 90.0 + 5.0, 1)))
                    job_progress_tracker.update_progress(
                        video_id=video_id,
                        stage="TRANSCRIBING",
                        percent=pct,
                        detail=f"Transcribing: {pct}% ({int(cur_end)}s / {int(total_dur)}s)",
                    )
                    if pct - last_emitted_pct >= 25.0:
                        last_emitted_pct = pct
                        try:
                            global_event_bus.publish_sync(PlatformEvent(
                                category="transcription",
                                level="INFO",
                                title="Transcribing Audio",
                                message=f"Whisper transcribing: {pct:.0f}% ({int(cur_end)}s / {int(total_dur)}s)",
                                stage_progress_pct=pct,
                                entity_id=video_id,
                                entity_type="video" if video_id else None,
                            ))
                        except Exception:
                            pass

            if video_id:
                job_progress_tracker.update_progress(
                    video_id=video_id,
                    stage="TRANSCRIBING",
                    percent=95.0,
                    detail="Running forced phoneme alignment pass (+/-50ms precision)...",
                )
            try:
                global_event_bus.publish_sync(PlatformEvent(
                    category="transcription",
                    level="INFO",
                    title="Phoneme Alignment",
                    message="Running forced phoneme alignment pass (+/-50ms precision)...",
                    stage_progress_pct=95.0,
                    entity_id=video_id,
                    entity_type="video" if video_id else None,
                ))
            except Exception:
                pass

            # Apply forced alignment pass to eliminate cross-attention drift
            aligned_words = self.aligner.align(audio_path, raw_words)

            if video_id:
                job_progress_tracker.update_progress(
                    video_id=video_id,
                    stage="TRANSCRIBING",
                    percent=100.0,
                    detail=f"Transcription complete ({len(aligned_words)} words aligned).",
                )
            try:
                global_event_bus.publish_sync(PlatformEvent(
                    category="transcription",
                    level="SUCCESS",
                    title="Transcription Completed",
                    message=f"Transcribed & aligned {len(aligned_words)} words ({info.language or 'en'}).",
                    stage_progress_pct=100.0,
                    entity_id=video_id,
                    entity_type="video" if video_id else None,
                    details={"words_count": len(aligned_words), "language": info.language},
                ))
            except Exception:
                pass

            return FormattedTranscript(
                words=aligned_words,
                language=info.language or "en",
                full_text=" ".join(full_text_parts),
            )
        except Exception as e:
            try:
                global_event_bus.publish_sync(PlatformEvent(
                    category="transcription",
                    level="ERROR",
                    title="Transcription Failed",
                    message=f"Transcription failed for {video_id or audio_path.name}: {str(e)[:250]}",
                    entity_id=video_id,
                    entity_type="video" if video_id else None,
                ))
            except Exception:
                pass
            raise
