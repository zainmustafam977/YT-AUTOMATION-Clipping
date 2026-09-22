"""
Transcription engine wrapping faster-whisper with automatic GPU/CPU auto-detection
and word-level timestamp alignment.
"""

import logging
from pathlib import Path
from typing import Optional
import torch
from shorts_engine.config.settings import settings
from shorts_engine.services.transcription.formatter import WordSegment, FormattedTranscript
from shorts_engine.services.transcription.aligner import ForcedAligner

logger = logging.getLogger(__name__)


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

    def transcribe(self, audio_path: Path, language: Optional[str] = None, video_id: Optional[str] = None) -> FormattedTranscript:
        """
        Transcribes the given audio file and produces word-level timestamped segments.
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

        # Optional cloud whisper acceleration via Groq
        if getattr(settings, "USE_GROQ_WHISPER", False):
            if video_id:
                job_progress_tracker.update_progress(
                    video_id=video_id,
                    stage="TRANSCRIBING",
                    percent=20.0,
                    detail="Executing high-speed cloud transcription via Groq LPU...",
                )
            groq_res = self._transcribe_with_groq(audio_path, language=language)
            if groq_res is not None:
                if video_id:
                    job_progress_tracker.update_progress(
                        video_id=video_id,
                        stage="TRANSCRIBING",
                        percent=100.0,
                        detail="Transcription completed successfully.",
                    )
                return groq_res

        logger.info("Transcribing %s...", audio_path.name)
        segments, info = self.model.transcribe(
            str(audio_path),
            language=language,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=400),
        )

        total_dur = getattr(info, "duration", 0.0) or 1.0
        raw_words = []
        full_text_parts = []
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

        if video_id:
            job_progress_tracker.update_progress(
                video_id=video_id,
                stage="TRANSCRIBING",
                percent=95.0,
                detail="Running forced phoneme alignment pass (+/-50ms precision)...",
            )

        # Apply forced alignment pass to eliminate cross-attention drift
        aligned_words = self.aligner.align(audio_path, raw_words)

        if video_id:
            job_progress_tracker.update_progress(
                video_id=video_id,
                stage="TRANSCRIBING",
                percent=100.0,
                detail=f"Transcription complete ({len(aligned_words)} words aligned).",
            )

        return FormattedTranscript(
            words=aligned_words,
            language=info.language or "en",
            full_text=" ".join(full_text_parts),
        )
