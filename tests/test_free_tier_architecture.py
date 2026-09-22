"""
Unit tests validating the 100% Zero-Cost Resilient Architecture:
- Groq rate-limit header parsing (x-ratelimit-remaining-tokens, reset countdown, inference metrics)
- Proactive Groq token pre-flight budgeting in LLMRouter
- Gemini Interactions API integration with transparent generateContent fallback
- Groq Cloud Whisper acceleration (whisper-large-v3-turbo) & local fallback
- Zero-cost viral thumbnail generator with HDR pop & safe-zone typography
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from PIL import Image

from shorts_engine.config.settings import settings
from shorts_engine.services.llm.quota_tracker import usage_tracker
from shorts_engine.services.llm.router import LLMRouter
from shorts_engine.services.transcription.engine import TranscriptionService
from shorts_engine.services.rendering.thumbnail_generator import ThumbnailGenerator


def test_groq_rate_limit_header_parsing():
    """Verify that Groq rate-limit headers and inference metrics are parsed and tracked."""
    headers = {
        "x-ratelimit-remaining-tokens": "4850",
        "x-ratelimit-reset-tokens": "14.2s",
        "x-ratelimit-remaining-requests": "29",
    }
    metrics = {
        "tokens_per_sec": 482.5,
        "completion_time": 0.45,
    }

    test_key = "gsk_test_header_parsing_12345"
    with patch.object(settings, "GROQ_API_KEY", test_key):
        usage_tracker.record_request(
            provider="groq",
            key=test_key,
            status="SUCCESS",
            latency_ms=350,
            model="openai/gpt-oss-120b",
            headers=headers,
            inference_metrics=metrics,
        )

        remaining = usage_tracker.get_groq_remaining_tokens(test_key)
        assert remaining == 4850

        per_key = usage_tracker.get_per_key_metrics()
        groq_entry = next((k for k in per_key if k["key_masked"].endswith("2345")), None)
        assert groq_entry is not None
        assert groq_entry["remaining_tokens_minute"] == 4850
        assert groq_entry["reset_tokens_sec"] == 14.2
        assert groq_entry["last_tokens_per_sec"] == 482.5
        assert groq_entry["last_completion_time_sec"] == 0.45

        # Cleanup
        usage_tracker.remove_key("groq", groq_entry["key_hash"])


def test_groq_proactive_token_exhaustion_routes_to_gemini():
    """Verify that when Groq remaining tokens are exhausted, router immediately skips Groq."""
    test_key = "gsk_test_exhausted_token_key_99999"
    headers = {
        "x-ratelimit-remaining-tokens": "100",  # Only 100 tokens left
        "x-ratelimit-reset-tokens": "30s",
    }
    with patch.object(settings, "GROQ_API_KEY", test_key):
        usage_tracker.record_request(
            provider="groq",
            key=test_key,
            status="SUCCESS",
            latency_ms=200,
            headers=headers,
        )

        router = LLMRouter(
            groq_api_key=test_key,
            gemini_api_key="mock_gemini_key",
        )

        # 100 words = ~133 tokens, which exceeds the 100 remaining tokens on Groq
        prompt = " ".join(["word"] * 100)
        with patch.object(router, "_call_gemini", return_value=({"result": "ok"}, {"model": "gemini-3.8-flash"})) as mock_gemini, \
             patch("httpx.Client.post") as mock_groq_http:
            res, meta = router.generate_structured(prompt, dict)
            assert res["result"] == "ok"
            mock_groq_http.assert_not_called()
            mock_gemini.assert_called_once()

        # Cleanup
        per_key = usage_tracker.get_per_key_metrics()
        groq_entry = next((k for k in per_key if k["key_masked"].endswith("9999")), None)
        if groq_entry:
            usage_tracker.remove_key("groq", groq_entry["key_hash"])


def test_gemini_interactions_api_success():
    """Verify that LLMRouter uses client.interactions.create when available."""
    router = LLMRouter(gemini_api_key="mock_gemini_interactions_key")

    mock_interaction = MagicMock()
    mock_interaction.output_text = '{"summary": "viral hook from interactions api"}'

    mock_client = MagicMock()
    mock_client.interactions.create.return_value = mock_interaction

    with patch("google.genai.Client", return_value=mock_client):
        data, meta = router._call_gemini("Test prompt", dict, "gemini-3.8-flash")
        assert data["summary"] == "viral hook from interactions api"
        assert meta["api"] == "interactions"
        mock_client.interactions.create.assert_called_once()


def test_gemini_interactions_fallback_to_generate_content():
    """Verify that if client.interactions fails, router falls back to generate_content."""
    router = LLMRouter(gemini_api_key="mock_gemini_fallback_key")

    mock_response = MagicMock()
    mock_response.text = '{"summary": "fallback from generate_content"}'

    mock_client = MagicMock()
    # Interactions API raises an error
    mock_client.interactions.create.side_effect = Exception("Interactions API not supported on endpoint")
    mock_client.models.generate_content.return_value = mock_response

    with patch("google.genai.Client", return_value=mock_client):
        data, meta = router._call_gemini("Test prompt", dict, "gemini-3.8-flash")
        assert data["summary"] == "fallback from generate_content"
        assert meta["api"] == "generate_content"
        mock_client.interactions.create.assert_called_once()
        mock_client.models.generate_content.assert_called_once()


def test_groq_whisper_transcription(tmp_path):
    """Verify Groq Cloud Whisper audio transcription integration."""
    audio_file = tmp_path / "test_audio.wav"
    audio_file.write_bytes(b"RIFF dummy audio data")

    engine = TranscriptionService()

    mock_groq_response = {
        "text": "Never give up on your dreams. Stay focused and disciplined.",
        "language": "en",
        "duration": 4.5,
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 2.2,
                "text": " Never give up on your dreams.",
                "words": [
                    {"word": "Never", "start": 0.0, "end": 0.4},
                    {"word": "give", "start": 0.4, "end": 0.7},
                    {"word": "up", "start": 0.7, "end": 0.9},
                    {"word": "on", "start": 0.9, "end": 1.2},
                    {"word": "your", "start": 1.2, "end": 1.5},
                    {"word": "dreams.", "start": 1.5, "end": 2.2},
                ]
            },
            {
                "id": 1,
                "start": 2.3,
                "end": 4.5,
                "text": " Stay focused and disciplined.",
                "words": [
                    {"word": "Stay", "start": 2.3, "end": 2.8},
                    {"word": "focused", "start": 2.8, "end": 3.4},
                    {"word": "and", "start": 3.4, "end": 3.7},
                    {"word": "disciplined.", "start": 3.7, "end": 4.5},
                ]
            }
        ]
    }

    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 200
    mock_http_resp.json.return_value = mock_groq_response

    with patch("httpx.Client.post", return_value=mock_http_resp), \
         patch.object(settings, "GROQ_API_KEY", "gsk_mock_whisper_key"):
        transcript = engine._transcribe_with_groq(audio_file, language="en")
        assert transcript is not None
        assert "Never give up" in transcript.full_text
        assert len(transcript.words) == 10
        assert transcript.words[0].word == "Never"
        assert transcript.words[0].start == 0.0


def test_thumbnail_generator_creates_valid_image(tmp_path):
    """Verify that ThumbnailGenerator extracts frame and renders viral HDR thumbnail."""
    base_frame = Image.new("RGB", (1080, 1920), color=(40, 50, 60))
    video_path = tmp_path / "mock_video.mp4"
    video_path.write_bytes(b"dummy mp4 video bytes")

    generator = ThumbnailGenerator(output_dir=tmp_path)

    def mock_extract(v_path, out_frame, timestamp_sec=1.5):
        base_frame.save(out_frame)
        return True

    with patch.object(generator, "extract_frame_ffmpeg", side_effect=mock_extract):
        thumb_path = generator.generate_thumbnail(
            video_path=video_path,
            hook_text="THE SECRET TO VIRAL SUCCESS",
            timestamp_sec=1.5,
        )

        assert thumb_path is not None
        assert Path(thumb_path).exists()
        assert Path(thumb_path).suffix == ".jpg"

        # Verify image dimensions
        with Image.open(thumb_path) as img:
            assert img.size == (1080, 1920)
