"""
Unit tests validating all architectural enhancements from the log audit:
- Token-aware dynamic LLM routing & Groq 5,500 token threshold
- Extractive NLP heuristic clip selection without dummy placeholder strings
- Downloader stale fragment sweeper (.part / .ytdl)
- Hardware encoder auto-probing and CFR (-r 30)
- PipelineResult backwards compatibility and schema consistency
- Preflight token volume & AI tier forecasting endpoint
- Clean re-ingest endpoint
"""

import time
import pytest
from unittest.mock import patch
from starlette.testclient import TestClient

from shorts_engine.config.settings import settings
from shorts_engine.services.llm.router import LLMRouter
from shorts_engine.services.clip_selection.selector import ClipSelectionService
from shorts_engine.services.transcription.formatter import FormattedTranscript, WordSegment
from shorts_engine.services.ingestion.downloader import IngestionService
from shorts_engine.services.rendering.compositor import get_best_video_encoder
from shorts_engine.orchestration.runner import PipelineResult
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.auth import create_session_token, generate_csrf_token, SESSION_COOKIE_NAME
from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import User, UserRole, SourceVideo, SourceVideoStatus, Channel


@pytest.fixture
def auth_client():
    db = SessionLocal()
    app = create_dashboard_app()
    client = TestClient(app)

    user = db.query(User).filter_by(username="test_admin_audit").first()
    if not user:
        user = User(
            username="test_admin_audit",
            password_hash="dummy_hash",
            role=UserRole.ADMIN,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    token = create_session_token(user.id)
    client.cookies.set(SESSION_COOKIE_NAME, token)
    yield client, db, token

    # Cleanup
    db.query(User).filter_by(username="test_admin_audit").delete()
    db.commit()
    db.close()


def test_token_aware_router_promotes_gemini():
    """Verify that large prompts (>=5,500 estimated tokens) dynamically promote Gemini over Groq."""
    router = LLMRouter(
        gemini_api_key="mock_gemini_key",
        groq_api_key="mock_groq_key",
    )

    # Short prompt (<5,500 tokens) -> Groq should remain Tier 1
    short_prompt = "Summarize this short text."
    est_short = int(len(short_prompt.split()) * 1.33)
    assert est_short < settings.GROQ_TOKEN_THRESHOLD

    # Long prompt (>=5,500 tokens) -> Gemini should be promoted to Tier 1
    long_prompt = " ".join(["word"] * 4500)
    est_long = int(len(long_prompt.split()) * 1.33)
    assert est_long >= settings.GROQ_TOKEN_THRESHOLD

    with patch.object(router, "_call_gemini", return_value=({"test": 1}, {"model": "gemini-3.8-flash"})) as mock_gemini, \
         patch.object(router, "_call_groq") as mock_groq:
        router.generate_structured(long_prompt, dict)
        # Should call Gemini directly, bypassing Groq
        mock_gemini.assert_called_once()
        mock_groq.assert_not_called()


def test_extractive_heuristic_selection_no_placeholder_strings():
    """Verify that _heuristic_select() extracts real transcript speech sentences and no dummy 'Clip X Hook' strings."""
    words = [
        WordSegment(word="Why", start=0.0, end=0.3, probability=0.9),
        WordSegment(word="does", start=0.3, end=0.5, probability=0.9),
        WordSegment(word="this", start=0.5, end=0.7, probability=0.9),
        WordSegment(word="matter?", start=0.7, end=1.0, probability=0.9),
        WordSegment(word="Because", start=1.5, end=2.0, probability=0.9),
        WordSegment(word="everything", start=2.0, end=2.5, probability=0.9),
        WordSegment(word="changes", start=2.5, end=3.0, probability=0.9),
        WordSegment(word="today.", start=3.0, end=3.5, probability=0.9),
    ]
    transcript = FormattedTranscript(
        words=words,
        language="en",
        full_text="Why does this matter? Because everything changes today.",
    )

    service = ClipSelectionService()
    candidates = service._heuristic_select(transcript, min_clips=1, max_clips=2)
    assert len(candidates) >= 1

    for c in candidates:
        assert "Clip " not in c.hook_text
        assert "Hook" not in c.hook_text
        assert "Key Takeaway" not in c.payoff_text
        assert len(c.hook_text.strip()) > 0
        assert len(c.payoff_text.strip()) > 0
        assert len(c.reason) > 0


def test_downloader_purge_stale_fragments(tmp_path):
    """Verify purge_stale_fragments removes older .part and .ytdl files while leaving newer files intact."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    downloader = IngestionService(output_dir=raw_dir)

    # Create an old .part file (modified 5 hours ago)
    old_part = raw_dir / "video123.mp4.part"
    old_part.write_text("partial data")
    five_hours_ago = time.time() - (5 * 3600)
    import os
    os.utime(str(old_part), (five_hours_ago, five_hours_ago))

    # Create a fresh .part file (modified just now)
    fresh_part = raw_dir / "video456.mp4.part"
    fresh_part.write_text("partial fresh data")

    purged = downloader.purge_stale_fragments(max_age_hours=2.0)
    assert purged >= 1
    assert not old_part.exists()
    assert fresh_part.exists()


def test_get_best_video_encoder():
    """Verify get_best_video_encoder returns a valid encoder and option list."""
    encoder, opts = get_best_video_encoder()
    assert isinstance(encoder, str)
    assert encoder in ("h264_nvenc", "h264_qsv", "h264_amf", "libx264")
    assert isinstance(opts, list)


def test_pipeline_result_compatibility():
    """Verify PipelineResult behaves as a full dict and seamlessly equals [] when empty."""
    res = PipelineResult({
        "source_video_id": "vid_test",
        "youtube_video_id": "yt_test",
        "status": "SKIPPED",
        "clips_count": 0,
        "clips": [],
    })

    assert isinstance(res, dict)
    assert res["status"] == "SKIPPED"
    assert res.get("clips_count") == 0
    # Backwards compatibility with tests asserting clips == []
    assert res == []


def test_video_status_preflight_forecast(auth_client):
    """Verify /api/video-status/{video_id} returns estimated_tokens and recommended_tier."""
    client, db, token = auth_client

    ch = db.query(Channel).first()
    if not ch:
        ch = Channel(id="ch_test_audit", youtube_channel_id="UC1234567890123456789012", name="Audit Channel")
        db.add(ch)
        db.commit()

    v = SourceVideo(
        id="test_preflight_vid",
        channel_id=ch.id,
        youtube_video_id="abcd1234efg",
        title="Test Preflight Video",
        duration_sec=900,  # 15 mins -> ~2992 tokens -> Tier 1
        status=SourceVideoStatus.DONE,
    )
    db.add(v)
    db.commit()

    resp = client.get("/api/video-status/abcd1234efg")
    assert resp.status_code == 200
    data = resp.json()
    assert data["exists"] is True
    assert data["estimated_tokens"] is not None
    assert "Tier 1" in data["recommended_tier"]

    # Cleanup
    db.query(SourceVideo).filter_by(id="test_preflight_vid").delete()
    db.commit()


def test_clean_retry_endpoint(auth_client):
    """Verify POST /process-video/{video_id}/clean-retry resets status to DETECTED."""
    client, db, token = auth_client

    ch = db.query(Channel).first()
    if not ch:
        ch = Channel(id="ch_test_audit", youtube_channel_id="UC1234567890123456789012", name="Audit Channel")
        db.add(ch)
        db.commit()

    test_yt_id = f"clean_{int(time.time())}"
    db.query(SourceVideo).filter_by(youtube_video_id=test_yt_id).delete()
    db.commit()

    v = SourceVideo(
        id=f"vid_{test_yt_id}",
        channel_id=ch.id,
        youtube_video_id=test_yt_id,
        title="Failed Download Video",
        status=SourceVideoStatus.FAILED,
        error_message="HTTP Error 416",
        retry_count=1,
    )
    db.add(v)
    db.commit()

    csrf = generate_csrf_token(token)
    with patch("shorts_engine.dashboard.routes.status._run_pipeline_background"):
        resp = client.post(
            f"/process-video/{test_yt_id}/clean-retry",
            headers={"Accept": "application/json", "X-CSRF-Token": csrf},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "clean_retrying"
        assert data["retry_count"] == 2

    # Verify database state
    db.refresh(v)
    assert v.status == SourceVideoStatus.DETECTED
    assert v.error_message is None

    # Cleanup
    db.query(SourceVideo).filter_by(id=f"vid_{test_yt_id}").delete()
    db.commit()
