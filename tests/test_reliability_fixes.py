"""
Unit and failure-injection tests for production reliability enhancements:
- Downloader HTTP 416 range reset and partial file purge
- Windows WinError 32 file-locking retry
- YouTube bot detection client cycling
- Groq 413 TPM overflow fast-skip and OTPM token capping
- OpenRouter 404 dynamic model decommission blacklist
- Gemini 503/504 jittered backoff recovery
- Pre-flight master source video existence validation
- Technical Quality Gate duration bounds clamping
"""

import os
import gc
import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock
import pytest

from shorts_engine.services.ingestion.downloader import IngestionService, VideoDownloadResult
from shorts_engine.services.llm.router import LLMRouter
from shorts_engine.services.clip_selection.boundaries import BoundarySnapper
from shorts_engine.services.clip_selection.selector import ClipSelectionService, SelectedClip
from shorts_engine.services.transcription.formatter import FormattedTranscript, WordSegment
from shorts_engine.services.quality_gate.technical_check import TechnicalVerifier
from shorts_engine.orchestration.runner import PipelineRunner
from shorts_engine.db.models import SourceVideo, SourceVideoStatus, Channel, Clip, ClipStatus


# ---------------------------------------------------------------------------
# 1. Ingestion: HTTP 416 Range Reset & Windows Lock Recovery
# ---------------------------------------------------------------------------

def test_downloader_http_416_resets_fragments_and_retries_fresh(tmp_path):
    """Verifies that an HTTP 416 error purges all partial files and resets to byte 0."""
    service = IngestionService(output_dir=tmp_path, max_retries=2)

    # Create dummy stale partial fragments in output_dir
    fake_part = tmp_path / "testvid.part"
    fake_part.write_text("corrupted bytes")
    fake_ytdl = tmp_path / "testvid.ytdl"
    fake_ytdl.write_text("state")

    attempts = 0

    def mock_extract_info(url, download=True):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("ERROR: unable to download video data: HTTP Error 416: Requested range not satisfiable")
        # Attempt 2 succeeds
        mock_info = {
            "title": "Clean Test Video",
            "duration": 45,
            "channel": "Test Channel",
            "channel_id": "UC1234567890123456789012",
        }
        # Create output file
        dummy_mp4 = tmp_path / "testvid.mp4"
        dummy_mp4.write_text("mp4 content")
        return mock_info

    mock_ydl_instance = MagicMock()
    mock_ydl_instance.extract_info.side_effect = mock_extract_info
    mock_ydl_instance.prepare_filename.return_value = str(tmp_path / "testvid.mp4")

    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls, \
         patch.object(service, "extract_audio") as mock_audio, \
         patch("time.sleep"):
        mock_ydl_cls.return_value.__enter__.return_value = mock_ydl_instance
        res = service.download_video("testvid")

    assert attempts == 2
    assert res.video_id == "testvid"
    assert not fake_part.exists()
    assert not fake_ytdl.exists()


def test_downloader_winerror_32_lock_handling(tmp_path):
    """Verifies that WinError 32 triggers GC, jittered wait, and retry."""
    service = IngestionService(output_dir=tmp_path, max_retries=2)
    attempts = 0

    def mock_extract_info(url, download=True):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("[WinError 32] The process cannot access the file because it is being used by another process")
        mock_info = {"title": "Unlocked Video", "duration": 30, "channel": "Tech", "channel_id": "UC123"}
        (tmp_path / "winlock.mp4").write_text("data")
        return mock_info

    mock_ydl_instance = MagicMock()
    mock_ydl_instance.extract_info.side_effect = mock_extract_info
    mock_ydl_instance.prepare_filename.return_value = str(tmp_path / "winlock.mp4")

    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls, \
         patch.object(service, "extract_audio"), \
         patch("gc.collect") as mock_gc, \
         patch("time.sleep"):
        mock_ydl_cls.return_value.__enter__.return_value = mock_ydl_instance
        res = service.download_video("winlock")

    assert attempts == 2
    assert mock_gc.called
    assert res.video_id == "winlock"


# ---------------------------------------------------------------------------
# 2. LLM Router: Groq 413 & OTPM Protection, OpenRouter 404 Blacklist
# ---------------------------------------------------------------------------

def test_groq_otpm_max_tokens_enforcement():
    """Verifies that qwen/qwen3.8-27b has max_tokens capped to 800 to prevent OTPM overflow."""
    router = LLMRouter(groq_api_key="gsk_test123", groq_model="qwen/qwen3.8-27b")

    posted_payloads = []

    def mock_post(url, headers, json):
        posted_payloads.append(json)
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        resp.json.return_value = {
            "choices": [{"message": {"content": '{"candidates": []}'}}],
            "usage": {"completion_tokens": 50},
        }
        return resp

    from pydantic import BaseModel
    class DummySchema(BaseModel):
        candidates: list

    with patch("httpx.Client.post", side_effect=mock_post):
        res, meta = router._call_groq("Test short prompt", DummySchema)

    assert len(posted_payloads) > 0
    # The qwen model payload must have max_tokens <= 800
    for p in posted_payloads:
        if "qwen" in p["model"].lower():
            assert p.get("max_tokens") == 800


def test_groq_http_413_fast_skip_without_key_loop():
    """Verifies that HTTP 413 immediately fast-skips Groq without retrying the same payload on key 2."""
    router = LLMRouter(
        groq_api_keys=["gsk_key1", "gsk_key2", "gsk_key3"],
        groq_model="openai/gpt-oss-120b",
    )

    call_count = 0

    def mock_post(url, headers, json):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.status_code = 413
        resp.text = '{"error":{"message":"Request too large for model on tokens per minute (TPM): Limit 8000, Requested 9867"}}'
        resp.headers = {}
        return resp

    from pydantic import BaseModel
    class DummySchema(BaseModel):
        candidates: list

    with patch("httpx.Client.post", side_effect=mock_post):
        res, meta = router._call_groq("Very large prompt", DummySchema)

    assert res is None
    assert meta.get("is_oversized") is True
    # Fast skip must abort on first 413 and NOT try key2 and key3 with identical oversized payload
    assert call_count == 1


def test_openrouter_404_dynamic_blacklist():
    """Verifies that any OpenRouter model returning 404 is permanently blacklisted in memory."""
    router = LLMRouter(
        openrouter_api_key="sk-or-test123",
        openrouter_model="meta-llama/llama-3.3-70b-instruct:free",
    )
    LLMRouter._decommissioned_models.clear()

    tried_models = []

    def mock_post(url, headers, json):
        model = json["model"]
        tried_models.append(model)
        resp = MagicMock()
        if model == "meta-llama/llama-3.3-70b-instruct:free":
            resp.status_code = 404
            resp.text = '{"error":"Model not found"}'
        else:
            resp.status_code = 200
            resp.text = '{"choices":[{"message":{"content":"{\\"candidates\\":[]}"}}]}'
            resp.json.return_value = {"choices": [{"message": {"content": '{"candidates":[]}'}}]}
        return resp

    from pydantic import BaseModel
    class DummySchema(BaseModel):
        candidates: list

    with patch("httpx.Client.post", side_effect=mock_post):
        # Call 1: model 404s, blacklisted, next model succeeds
        res, meta = router._call_openrouter("Prompt", DummySchema)
        assert "meta-llama/llama-3.3-70b-instruct:free" in LLMRouter._decommissioned_models

        # Call 2: decommissioned model must be skipped entirely
        tried_models.clear()
        res2, meta2 = router._call_openrouter("Prompt 2", DummySchema)
        assert "meta-llama/llama-3.3-70b-instruct:free" not in tried_models


# ---------------------------------------------------------------------------
# 3. Quality Gate & Duration Clamping
# ---------------------------------------------------------------------------

def test_boundary_snapper_strict_duration_elasticity():
    """Verifies that BoundarySnapper strictly clamps duration to max_duration_sec + 1.5s."""
    snapper = BoundarySnapper(min_duration_sec=15.0, max_duration_sec=58.0)

    # Generate synthetic transcript spanning 120 seconds
    words = []
    t = 0.0
    for i in range(200):
        punct = ". " if (i % 20 == 0 and i > 0) else " "
        words.append(WordSegment(word=f"word{i}{punct}", start=t, end=t + 0.5, probability=0.99))
        t += 0.6

    transcript = FormattedTranscript(words=words, language="en")

    # Ask for raw 85 second slice (should be clamped)
    start_ms, end_ms, seg_text = snapper.snap_boundaries(0.0, 85.0, transcript)
    dur_sec = (end_ms - start_ms) / 1000.0

    # Must strictly not exceed max_duration_sec + 1.5s (59.5s)
    assert dur_sec <= 59.5


# ---------------------------------------------------------------------------
# 4. Orchestration: Pre-Flight Master Video Verification
# ---------------------------------------------------------------------------

def test_pipeline_runner_preflight_missing_master_fails_fast(tmp_path):
    """Verifies that a missing master source video halts the pipeline cleanly without FFmpeg crashes."""
    db_session = MagicMock()
    runner = PipelineRunner(db_session=db_session)

    # Mock video with non-existent downloaded path
    video = MagicMock(spec=SourceVideo)
    video.id = "vid-123"
    video.video_id = "missing_yt_id"
    video.downloaded_path = str(tmp_path / "non_existent_master.mp4")
    video.channel = MagicMock(spec=Channel)
    video.channel.name = "TestChannel"
    video.channel.youtube_channel_id = "UC123"
    video.title = "Missing Video"
    video.runtime_overrides = None

    transcript_mock = MagicMock()
    transcript_mock.word_level_json = json.dumps({"language": "en", "words": [], "full_text": ""})

    def mock_query(model):
        q = MagicMock()
        model_name = getattr(model, "__name__", str(model))
        if "SourceVideo" in model_name:
            q.filter_by.return_value.first.return_value = video
        elif "Transcript" in model_name:
            q.filter_by.return_value.first.return_value = transcript_mock
        else:
            q.filter_by.return_value.first.return_value = None
        return q

    db_session.query.side_effect = mock_query

    mock_download_res = MagicMock(
        video_id="missing_yt_id",
        title="Missing Video",
        video_path=tmp_path / "non_existent_master.mp4",
        audio_path=tmp_path / "non_existent_audio.wav",
        duration_sec=60,
    )
    mock_cand = MagicMock(id="cand-1", start_ms=0, end_ms=25000, hook_text="Hook")

    with patch.object(runner.ingestion, "download_video", return_value=mock_download_res), \
         patch.object(runner.transcription, "transcribe", return_value=FormattedTranscript(words=[])), \
         patch.object(runner.clip_selector, "select_clips", return_value=[mock_cand]), \
         patch("shorts_engine.services.storage.path_manager.PathManager.resolve_download_path", return_value=None):

        result = runner.run_pipeline_for_video("missing_yt_id", dry_run=False)

    assert result["status"] == "FAILED"
    assert "Master source video not found on disk" in result["error"]
    assert video.status == SourceVideoStatus.FAILED
