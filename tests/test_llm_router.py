"""
Unit and integration tests for Phase 3:
- Multi-Provider LLM Router with transparent failover (Gemini -> OpenAI on 429)
- ClipSelectionService integration with LLMRouter
- SemanticVerifier integration with LLMRouter
- Settings UI multi-provider parameters and key masking
"""

import json
from unittest.mock import MagicMock, patch
from pydantic import BaseModel, Field
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.config.settings import settings
from shorts_engine.db.security import mask_secret
from shorts_engine.services.llm.quota_tracker import mask_api_key
from shorts_engine.db.session import Base
from shorts_engine.db.models import User, UserRole
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.auth import get_current_user, require_operator, require_viewer, verify_csrf
from shorts_engine.services.llm.router import LLMRouter
from shorts_engine.services.clip_selection.selector import ClipSelectionService
from shorts_engine.services.transcription.formatter import FormattedTranscript, WordSegment
from shorts_engine.services.quality_gate.semantic_check import SemanticVerifier


class DummySchema(BaseModel):
    summary: str = Field(description="Summary")
    score: float = Field(description="Score")


# ── 1. LLMRouter Unit Tests ───────────────────────────────────────────────────

def test_router_direct_gemini_success():
    router = LLMRouter(gemini_api_key="test-gemini-key", provider="gemini")

    mock_resp = MagicMock()
    mock_resp.text = json.dumps({"summary": "Great video", "score": 9.2})

    mock_models = MagicMock()
    mock_models.generate_content.return_value = mock_resp
    mock_client = MagicMock()
    mock_client.models = mock_models

    with patch.object(LLMRouter, "gemini_client", mock_client):
        result, meta = router.generate_structured("Test prompt", DummySchema)
        assert result is not None
        assert result.summary == "Great video"
        assert result.score == 9.2
        assert meta["provider"] == "gemini"
        assert meta["failover"] is False


def test_router_failover_gemini_429_to_openai():
    router = LLMRouter(
        gemini_api_key="test-gemini-key",
        openai_api_key="sk-test-openai-key",
        groq_api_key="",
        openrouter_api_key="",
        provider="auto",
    )

    # Gemini throws 429 ResourceExhausted
    mock_models = MagicMock()
    mock_models.generate_content.side_effect = Exception("429 ResourceExhausted: Quota exceeded for quota metric 'GenerateContent'")
    mock_client = MagicMock()
    mock_client.models = mock_models

    # OpenAI mock response
    openai_json_payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"summary": "Failover content from OpenAI", "score": 8.7})
                }
            }
        ]
    }
    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 200
    mock_http_resp.json.return_value = openai_json_payload

    with patch.object(LLMRouter, "gemini_client", mock_client):
        with patch("httpx.Client.post", return_value=mock_http_resp) as mock_post:
            result, meta = router.generate_structured("Test prompt", DummySchema)
            assert result is not None
            assert result.summary == "Failover content from OpenAI"
            assert result.score == 8.7
            assert meta["provider"] == "openai"
            assert meta["failover"] is True
            assert mock_post.called


def test_router_openai_direct():
    router = LLMRouter(
        openai_api_key="sk-test-openai-key",
        provider="openai",
    )

    openai_json_payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"summary": "Direct OpenAI content", "score": 9.0})
                }
            }
        ]
    }
    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 200
    mock_http_resp.json.return_value = openai_json_payload

    with patch("httpx.Client.post", return_value=mock_http_resp):
        result, meta = router.generate_structured("Prompt", DummySchema)
        assert result is not None
        assert result.summary == "Direct OpenAI content"
        assert meta["provider"] == "openai"


def test_router_groq_direct():
    router = LLMRouter(
        groq_api_key="gsk-test-groq-key",
        provider="groq",
    )

    groq_payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"summary": "Ultra-fast Groq LPU response", "score": 9.6})
                }
            }
        ]
    }
    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 200
    mock_http_resp.json.return_value = groq_payload

    with patch("httpx.Client.post", return_value=mock_http_resp):
        result, meta = router.generate_structured("Prompt", DummySchema)
        assert result is not None
        assert result.summary == "Ultra-fast Groq LPU response"
        assert result.score == 9.6
        assert meta["provider"] == "groq"
        assert meta["model"] == settings.GROQ_MODEL_NAME


def test_router_openrouter_direct():
    router = LLMRouter(
        openrouter_api_key="sk-or-test-key",
        provider="openrouter",
    )

    openrouter_payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"summary": "Free tier OpenRouter response", "score": 8.4})
                }
            }
        ]
    }
    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 200
    mock_http_resp.json.return_value = openrouter_payload

    with patch("httpx.Client.post", return_value=mock_http_resp):
        result, meta = router.generate_structured("Prompt", DummySchema)
        assert result is not None
        assert result.summary == "Free tier OpenRouter response"
        assert meta["provider"] == "openrouter"
        assert meta["model"] == settings.OPENROUTER_MODEL_NAME


def test_router_tiered_failover_groq_to_gemini():
    router = LLMRouter(
        groq_api_key="gsk-test-groq-key",
        gemini_api_key="test-gemini-key",
        provider="auto",
    )

    # Groq fails with 429
    mock_groq_resp = MagicMock()
    mock_groq_resp.status_code = 429

    # Gemini succeeds
    mock_resp = MagicMock()
    mock_resp.text = json.dumps({"summary": "Gemini fallback response", "score": 9.1})
    mock_models = MagicMock()
    mock_models.generate_content.return_value = mock_resp
    mock_gemini = MagicMock()
    mock_gemini.models = mock_models

    with patch("httpx.Client.post", side_effect=Exception("HTTP 429 Rate Limit")):
        with patch.object(LLMRouter, "gemini_client", mock_gemini):
            result, meta = router.generate_structured("Prompt", DummySchema)
            assert result is not None
            assert result.summary == "Gemini fallback response"
            assert meta["provider"] == "gemini"
            assert meta["failover"] is True


def test_router_graceful_degradation_when_no_keys():
    router = LLMRouter(
        gemini_api_key="",
        openai_api_key="",
        groq_api_key="",
        openrouter_api_key="",
        provider="auto",
    )
    result, meta = router.generate_structured("Prompt", DummySchema)
    assert result is None
    assert "No LLM API keys configured" in meta["error"]


def test_ai_provenance_parser():
    from shorts_engine.services.clip_selection.selector import parse_ai_provenance

    # 1. Groq format
    res1 = parse_ai_provenance("[groq:openai/gpt-oss-120b] High tension hook and dramatic climax")
    assert res1["provider"] == "groq"
    assert res1["model"] == "openai/gpt-oss-120b"
    assert "⚡ Groq" in res1["badge"]
    assert res1["clean_reason"] == "High tension hook and dramatic climax"
    assert res1["is_heuristic"] is False

    # 2. Gemini format
    res2 = parse_ai_provenance("[gemini:gemini-3.8-flash] Engaging start")
    assert res2["provider"] == "gemini"
    assert res2["model"] == "gemini-3.8-flash"
    assert "🤖 Gemini" in res2["badge"]
    assert res2["is_heuristic"] is False

    # 3. OpenRouter format
    res3 = parse_ai_provenance("[openrouter:liquid/lfm-2.5-2.6b:free] Good dialogue")
    assert res3["provider"] == "openrouter"
    assert "🌐 OpenRouter" in res3["badge"]

    # 4. Heuristic fallback format
    res4 = parse_ai_provenance("[heuristic:algorithmic-fallback] Selected via sentence-density")
    assert res4["provider"] == "heuristic"
    assert res4["is_heuristic"] is True
    assert "⚙️ Algorithmic" in res4["badge"]

    # 5. Legacy untagged string
    res5 = parse_ai_provenance("Original legacy reason without tag")
    assert res5["clean_reason"] == "Original legacy reason without tag"


# ── 2. Service Integration Tests ──────────────────────────────────────────────

def test_clip_selection_service_with_router_failover():
    # Simulate Gemini failing with 429 and OpenAI returning candidate segments
    router = LLMRouter(
        gemini_api_key="gem-key",
        openai_api_key="sk-op-key",
        provider="auto",
    )

    mock_models = MagicMock()
    mock_models.generate_content.side_effect = Exception("HTTP 429: Daily request limit reached")
    mock_gemini = MagicMock()
    mock_gemini.models = mock_models

    candidates_payload = {
        "candidates": [
            {
                "start_sec": 1.0,
                "end_sec": 22.0,
                "score": 0.91,
                "reason": "Dramatic narrative hook and conclusion",
                "hook_text": "You will not believe this discovery!",
                "payoff_text": "And that is how the problem was solved.",
                "suggested_title": "Incredible Discovery Unveiled",
                "suggested_description": "Watch until the end.\n#Shorts",
                "suggested_tags": ["#shorts", "#discovery"],
            }
        ]
    }
    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 200
    mock_http_resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(candidates_payload)}}]
    }

    service = ClipSelectionService(router=router)

    # Build dummy 30-second transcript with pauses
    words = [WordSegment(f"word{i}", float(i), float(i) + 0.4) for i in range(30)]
    transcript = FormattedTranscript(words=words)

    with patch.object(LLMRouter, "gemini_client", mock_gemini):
        with patch("httpx.Client.post", return_value=mock_http_resp):
            clips = service.select_clips(transcript, min_clips=1, max_clips=3)
            assert len(clips) >= 1
            top = clips[0]
            assert top.hook_text == "You will not believe this discovery!"
            assert top.score == 0.91
            assert top.duration_sec >= settings.TARGET_DURATION_MIN_SEC


def test_semantic_verifier_with_router_failover():
    router = LLMRouter(
        gemini_api_key="gem-key",
        openai_api_key="sk-op-key",
        provider="auto",
    )

    mock_models = MagicMock()
    mock_models.generate_content.side_effect = Exception("429 QuotaExceeded")
    mock_gemini = MagicMock()
    mock_gemini.models = mock_models

    sem_payload = {
        "is_autonomous": True,
        "has_unresolved_pronouns": False,
        "delivers_payoff": True,
        "pass_decision": True,
        "critique_reason": "Clear standalone segment with complete thought",
    }
    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 200
    mock_http_resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(sem_payload)}}]
    }

    verifier = SemanticVerifier(router=router)

    with patch.object(LLMRouter, "gemini_client", mock_gemini):
        with patch("httpx.Client.post", return_value=mock_http_resp):
            passed, details = verifier.verify_segment(
                segment_text="This is a fully complete standalone explanation of how rockets work.",
                hook_text="How do rockets fly?",
                payoff_text="That is the core principle of propulsion.",
            )
            assert passed is True
            assert details["pass_decision"] is True
            assert details["is_autonomous"] is True


# ── 3. Dashboard Settings Multi-Provider UI Tests ─────────────────────────────

def test_settings_page_multi_provider_persistence(tmp_path):
    orig_provider = settings.LLM_PROVIDER
    orig_openai_key = settings.OPENAI_API_KEY
    orig_openai_model = settings.OPENAI_MODEL_NAME
    orig_min_dur = settings.TARGET_DURATION_MIN_SEC
    orig_max_dur = settings.TARGET_DURATION_MAX_SEC
    orig_fallback = settings.LLM_FALLBACK_ON_RATE_LIMIT

    mock_cfg = tmp_path / "runtime_settings.json"
    try:
        with patch("shorts_engine.config.logging_config.RUNTIME_CONFIG_PATH", mock_cfg):
            app = create_dashboard_app()
            test_op = User(id="op-test", username="admin_op", password_hash="hash", role=UserRole.OPERATOR, is_active=True)
            app.dependency_overrides[get_current_user] = lambda: test_op
            app.dependency_overrides[require_viewer] = lambda: test_op
            app.dependency_overrides[require_operator] = lambda: test_op
            app.dependency_overrides[verify_csrf] = lambda: True

            client = TestClient(app)

            # 1. Update settings with OpenAI key and provider
            payload = {
                "target_duration_min_sec": str(settings.TARGET_DURATION_MIN_SEC),
                "target_duration_max_sec": "50",
                "min_segments_per_video": "2",
                "max_segments_per_video": "4",
                "caption_margin_vertical": "420",
                "max_retry_count": "3",
                "quota_warning_threshold": "0.85",
                "llm_model_name": "gemini-3.8-flash",
                "llm_provider": "auto",
                "openai_model_name": "gpt-4o",
                "openai_api_key": "sk-super-secret-backup-key-1234",
                "llm_fallback_on_rate_limit": "on",
            }
            resp = client.post("/settings", data=payload)
            assert resp.status_code in (200, 303)

            assert settings.LLM_PROVIDER == "auto"
            assert settings.OPENAI_MODEL_NAME == "gpt-4o"
            assert settings.LLM_FALLBACK_ON_RATE_LIMIT is True

            # 2. View settings and check key masking
            resp_view = client.get("/settings")
            assert resp_view.status_code == 200
            html = resp_view.text
            assert "Multi-Provider LLM & Rate-Limit Failover" in html

            # Assert the exact masked rendering, not a bare "1234".
            #
            # This previously asserted `"1234" in html`, which passed for the
            # wrong reason: the settings page also rendered the shipped default
            # credential AdminPass12345!, and that string contains "1234". The
            # test therefore never checked the API key was masked - removing
            # the leaked credential made it fail, which is how the false
            # positive was found. Compare against the real masked form.
            # The page renders item.masked, which is mask_api_key
            # (first 8 + ... + last 4), not mask_secret (first 4 + ... + last 4).
            expected_masked = mask_api_key("sk-super-secret-backup-key-1234")
            assert expected_masked in html, (
                f"expected the masked key {expected_masked!r} on the settings page"
            )
            # The looser masking must also be absent of the full value.
            assert mask_secret("sk-super-secret-backup-key-1234") != "sk-super-secret-backup-key-1234"
            assert "sk-super-secret-backup-key-1234" not in html  # never rendered in full
            assert "sk-super-secret-backup-key" not in html  # Secret prefix masked
    finally:
        # Restore settings
        settings.LLM_PROVIDER = orig_provider
        settings.OPENAI_API_KEY = orig_openai_key
        settings.OPENAI_MODEL_NAME = orig_openai_model
        settings.TARGET_DURATION_MIN_SEC = orig_min_dur
        settings.TARGET_DURATION_MAX_SEC = orig_max_dur
        settings.LLM_FALLBACK_ON_RATE_LIMIT = orig_fallback


def test_settings_validation_error_redirects(tmp_path):
    mock_cfg = tmp_path / "runtime_settings.json"
    with patch("shorts_engine.config.logging_config.RUNTIME_CONFIG_PATH", mock_cfg):
        app = create_dashboard_app()
        test_op = User(id="op-test", username="admin_op", password_hash="hash", role=UserRole.OPERATOR, is_active=True)
        app.dependency_overrides[get_current_user] = lambda: test_op
        app.dependency_overrides[require_viewer] = lambda: test_op
        app.dependency_overrides[require_operator] = lambda: test_op
        app.dependency_overrides[verify_csrf] = lambda: True

        client = TestClient(app)

        # Invalid bounds: min_duration > max_duration
        bad_payload = {
            "target_duration_min_sec": "70",
            "target_duration_max_sec": "30",
            "min_segments_per_video": "2",
            "max_segments_per_video": "4",
            "caption_margin_vertical": "420",
            "max_retry_count": "3",
            "quota_warning_threshold": "0.85",
            "llm_model_name": "gemini-3.8-flash",
        }

        # 1. Browser form submission -> 303 Redirect to /settings with type=danger
        resp_browser = client.post(
            "/settings",
            data=bad_payload,
            headers={"Sec-Fetch-Dest": "document"},
            follow_redirects=False,
        )
        assert resp_browser.status_code == 303
        assert "/settings" in resp_browser.headers["location"]
        assert "type=danger" in resp_browser.headers["location"]

        # 2. API client submission -> 422 JSON error
        resp_api = client.post(
            "/settings",
            data=bad_payload,
            headers={"Accept": "application/json"},
            follow_redirects=False,
        )
        assert resp_api.status_code == 422
        assert "TARGET_DURATION_MIN_SEC" in resp_api.json()["detail"]


def test_dead_letter_delete_and_clear_endpoints():
    from shorts_engine.db.models import Clip, ClipStatus, ClipCandidate, SourceVideo, SourceVideoStatus, Channel
    from shorts_engine.dashboard.routes.queue import get_db

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    app = create_dashboard_app()
    test_op = User(id="op-test", username="admin_op", password_hash="hash", role=UserRole.OPERATOR, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: test_op
    app.dependency_overrides[require_viewer] = lambda: test_op
    app.dependency_overrides[require_operator] = lambda: test_op
    app.dependency_overrides[verify_csrf] = lambda: True
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)

    # 1. Seed failed video and clips
    ch = Channel(id="ch_dl_1", youtube_channel_id="UC_dl_1", name="DL Channel")
    sv1 = SourceVideo(id="sv_dl_1", channel_id="ch_dl_1", youtube_video_id="yt_dl_01", title="DL Video 1", status=SourceVideoStatus.FAILED)
    sv2 = SourceVideo(id="sv_dl_2", channel_id="ch_dl_1", youtube_video_id="yt_dl_02", title="DL Video 2", status=SourceVideoStatus.FAILED)
    cand1 = ClipCandidate(id="cand_dl_1", source_video_id="sv_dl_1", start_ms=0, end_ms=20000, score=8.0, reason="test")
    cand2 = ClipCandidate(id="cand_dl_2", source_video_id="sv_dl_2", start_ms=0, end_ms=20000, score=8.0, reason="test")
    clip1 = Clip(id="clip_dl_1", clip_candidate_id="cand_dl_1", status=ClipStatus.FAILED)
    clip2 = Clip(id="clip_dl_2", clip_candidate_id="cand_dl_2", status=ClipStatus.FAILED)

    db.add_all([ch, sv1, sv2, cand1, cand2, clip1, clip2])
    db.commit()

    # 2. Delete single clip
    resp_clip_del = client.post(f"/dead-letter/clip/{clip1.id}/delete")
    assert resp_clip_del.status_code in (200, 303)
    assert db.query(Clip).filter_by(id=clip1.id).first() is None

    # 3. Delete single video
    resp_vid_del = client.post(f"/dead-letter/video/{sv1.id}/delete")
    assert resp_vid_del.status_code in (200, 303)
    assert db.query(SourceVideo).filter_by(id=sv1.id).first() is None

    # 4. Clear all remaining failed
    resp_clear = client.post("/dead-letter/clear-all")
    assert resp_clear.status_code in (200, 303)
    assert db.query(Clip).filter_by(status=ClipStatus.FAILED).count() == 0
    assert db.query(SourceVideo).filter_by(status=SourceVideoStatus.FAILED).count() == 0
