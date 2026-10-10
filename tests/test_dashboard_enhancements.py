"""
Unit and integration tests for dashboard enhancements:
- Dynamic runtime settings persistence and hot-reload
- Exact field names parity (TARGET_DURATION_MIN_SEC, etc.)
- Worker runner dynamic reload
- Manual video ingestion trigger (/process-video)
- Live log viewer (/logs and /api/logs)
- Channel add/delete endpoints
- Review queue timestamp formatting and quality badges
- Expandable Dead-Letter failure diagnostics
- Dark theme contrast styling
"""

import json
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.config.settings import settings
from shorts_engine.config.logging_config import reload_runtime_settings, save_runtime_settings
from shorts_engine.db.session import Base
from shorts_engine.db.models import Channel, SourceVideo, SourceVideoStatus, ClipCandidate, Clip, ClipStatus, User, UserRole
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.routes import status as status_route
from shorts_engine.dashboard.routes import queue as queue_route
from shorts_engine.dashboard.routes import channels as channels_route
from shorts_engine.dashboard.auth import get_current_user, require_operator, require_viewer, require_admin, verify_csrf


@pytest.fixture(autouse=True)
def restore_settings(tmp_path):
    """Snapshot and restore singleton settings so changes don't leak across tests."""
    orig = {
        "TARGET_DURATION_MIN_SEC": settings.TARGET_DURATION_MIN_SEC,
        "TARGET_DURATION_MAX_SEC": settings.TARGET_DURATION_MAX_SEC,
        "MIN_SEGMENTS_PER_VIDEO": settings.MIN_SEGMENTS_PER_VIDEO,
        "MAX_SEGMENTS_PER_VIDEO": settings.MAX_SEGMENTS_PER_VIDEO,
        "CAPTION_MARGIN_VERTICAL": settings.CAPTION_MARGIN_VERTICAL,
        "ENABLE_SFX": settings.ENABLE_SFX,
        "MAX_RETRY_COUNT": settings.MAX_RETRY_COUNT,
        "QUOTA_WARNING_THRESHOLD": settings.QUOTA_WARNING_THRESHOLD,
        "LLM_MODEL_NAME": settings.LLM_MODEL_NAME,
    }
    mock_cfg = tmp_path / "runtime_settings.json"
    with patch("shorts_engine.config.logging_config.RUNTIME_CONFIG_PATH", mock_cfg):
        yield
    for k, v in orig.items():
        setattr(settings, k, v)


@pytest.fixture
def test_env():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = create_dashboard_app()
    app.dependency_overrides[status_route.get_db] = override_get_db
    app.dependency_overrides[queue_route.get_db] = override_get_db
    app.dependency_overrides[channels_route.get_db] = override_get_db

    test_operator = User(
        id="test-op-001",
        username="test_operator",
        password_hash="hash",
        role=UserRole.OPERATOR,
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: test_operator
    app.dependency_overrides[require_viewer] = lambda: test_operator
    app.dependency_overrides[require_operator] = lambda: test_operator
    app.dependency_overrides[require_admin] = lambda: test_operator
    app.dependency_overrides[verify_csrf] = lambda: True

    client = TestClient(app, raise_server_exceptions=True)
    db = TestSession()
    yield client, db
    db.close()


# ── 1. Runtime Settings Persistence & Hot-Reload ──────────────────────────────

def test_runtime_settings_exact_field_names_and_persistence(tmp_path):
    """Verifies that runtime settings save to disk, reload dynamically, and use exact field names."""
    test_cfg_path = tmp_path / "runtime_settings.json"
    
    with patch("shorts_engine.config.logging_config.RUNTIME_CONFIG_PATH", test_cfg_path):
        payload = {
            "TARGET_DURATION_MIN_SEC": 20,
            "TARGET_DURATION_MAX_SEC": 45,
            "MIN_SEGMENTS_PER_VIDEO": 2,
            "MAX_SEGMENTS_PER_VIDEO": 5,
            "CAPTION_MARGIN_VERTICAL": 450,
            "ENABLE_SFX": False,
            "MAX_RETRY_COUNT": 4,
            "QUOTA_WARNING_THRESHOLD": 0.75,
            "LLM_MODEL_NAME": "gemini-3.6-flash",
        }
        
        saved = save_runtime_settings(payload)
        assert isinstance(saved, dict)
        assert saved["TARGET_DURATION_MIN_SEC"] == 20
        assert test_cfg_path.exists()

        # Reload and check settings object
        reloaded = reload_runtime_settings()
        assert reloaded.get("TARGET_DURATION_MIN_SEC") == 20
        assert settings.TARGET_DURATION_MIN_SEC == 20
        assert settings.TARGET_DURATION_MAX_SEC == 45
        assert settings.MIN_SEGMENTS_PER_VIDEO == 2
        assert settings.MAX_SEGMENTS_PER_VIDEO == 5
        assert settings.CAPTION_MARGIN_VERTICAL == 450
        assert settings.ENABLE_SFX is False
        assert settings.MAX_RETRY_COUNT == 4
        assert settings.QUOTA_WARNING_THRESHOLD == 0.75
        assert settings.LLM_MODEL_NAME == "gemini-3.6-flash"


def test_settings_page_get_and_post(test_env):
    """Verifies GET /settings renders form and POST /settings updates runtime settings."""
    client, _ = test_env
    
    # GET /settings
    get_resp = client.get("/settings")
    assert get_resp.status_code == 200
    html = get_resp.text
    assert "TARGET_DURATION_MIN_SEC" in html or "target_duration_min_sec" in html
    assert "TARGET_DURATION_MAX_SEC" in html or "target_duration_max_sec" in html
    assert "Enable Audio SFX Mixing" in html

    # POST valid settings
    post_resp = client.post(
        "/settings",
        data={
            "target_duration_min_sec": 18,
            "target_duration_max_sec": 55,
            "min_segments_per_video": 2,
            "max_segments_per_video": 4,
            "caption_margin_vertical": 400,
            "enable_sfx": "on",
            "max_retry_count": 3,
            "quota_warning_threshold": 0.85,
            "llm_model_name": "gemini-3.8-flash",
            "csrf_token": "mock-token",
        },
        headers={"Sec-Fetch-Dest": "document"},
        follow_redirects=False,
    )
    assert post_resp.status_code == 303
    assert "/settings" in post_resp.headers["location"]
    assert settings.TARGET_DURATION_MIN_SEC == 18
    assert settings.ENABLE_SFX is True


def test_settings_page_bounds_validation(test_env):
    """Verifies that invalid settings (e.g. min > max duration) are rejected with 422."""
    client, _ = test_env
    
    bad_resp = client.post(
        "/settings",
        data={
            "target_duration_min_sec": 60,
            "target_duration_max_sec": 30,  # Invalid: min > max
            "min_segments_per_video": 2,
            "max_segments_per_video": 4,
            "caption_margin_vertical": 400,
            "max_retry_count": 3,
            "quota_warning_threshold": 0.8,
            "llm_model_name": "gemini-3.8-flash",
            "csrf_token": "mock-token",
        },
    )
    assert bad_resp.status_code == 422
    assert "TARGET_DURATION_MIN_SEC" in bad_resp.json()["detail"]


# ── 2. Manual Video Processing Trigger ────────────────────────────────────────

def test_manual_video_processing_trigger(test_env):
    """Verifies POST /process-video triggers a background thread and redirects with flash message."""
    client, _ = test_env

    with patch("shorts_engine.dashboard.routes.status.threading.Thread") as mock_thread_cls:
        mock_thread_instance = mock_thread_cls.return_value
        resp = client.post(
            "/process-video",
            data={
                "video_input": "https://www.youtube.com/watch?v=fTVN19h4nMg",
                "dry_run": "on",
                "csrf_token": "mock-token",
            },
            headers={"Sec-Fetch-Dest": "document"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "fTVN19h4nMg" in resp.headers["location"]
        assert mock_thread_instance.start.called


# ── 3. Live Log Viewer & JSON Endpoint ────────────────────────────────────────

def test_live_logs_routes(test_env, tmp_path):
    """Verifies /logs and /api/logs endpoints read and return log file lines."""
    client, _ = test_env
    fake_log = tmp_path / "shorts_engine.log"
    fake_log.write_text("2026-09-14 10:00:00,000 [INFO] Engine initialized\n2026-09-14 10:01:00,000 [INFO] Action logged\n", encoding="utf-8")

    with patch("shorts_engine.dashboard.routes.logs.LOG_FILE_PATH", fake_log):
        # HTML route
        resp = client.get("/logs")
        assert resp.status_code == 200
        assert "Engine initialized" in resp.text
        assert "storage/logs/shorts_engine.log" in resp.text

        # API route
        api_resp = client.get("/api/logs?max_lines=50")
        assert api_resp.status_code == 200
        assert "Engine initialized" in api_resp.json()["logs"]


# ── 4. Channel Add and Delete ──────────────────────────────────────────────────

def test_channel_add_and_delete_endpoints(test_env):
    """Verifies POST /channels/add creates a channel and POST /channels/{id}/delete removes it."""
    client, db = test_env

    # 1. Add channel
    add_resp = client.post(
        "/channels/add",
        data={
            "youtube_channel_id": "UC_new_test_channel_id22",
            "name": "New Test Channel",
            "human_review": "on",
            "csrf_token": "mock-token",
        },
        headers={"Sec-Fetch-Dest": "document"},
        follow_redirects=False,
    )
    assert add_resp.status_code == 303

    ch = db.query(Channel).filter_by(youtube_channel_id="UC_new_test_channel_id22").first()
    assert ch is not None
    assert ch.name == "New Test Channel"
    assert ch.human_review_required is True

    # 2. Delete channel
    del_resp = client.post(
        f"/channels/{ch.id}/delete",
        data={"csrf_token": "mock-token"},
        headers={"Sec-Fetch-Dest": "document"},
        follow_redirects=False,
    )
    assert del_resp.status_code == 303
    assert db.query(Channel).filter_by(id=ch.id).first() is None


# ── 5. Review Queue Formatting & Quality Badges ───────────────────────────────

def test_queue_page_formatting_and_badges(test_env):
    """Verifies that /queue formats timestamps, hook quotes, and structured check badges."""
    client, db = test_env

    channel = Channel(youtube_channel_id="UC_qch", name="Queue Ch", watch_enabled=True, human_review_required=True)
    db.add(channel)
    db.flush()

    video = SourceVideo(channel_id=channel.id, youtube_video_id="q_vid_001", title="Queue Video", status=SourceVideoStatus.DONE)
    db.add(video)
    db.flush()

    cand = ClipCandidate(
        source_video_id=video.id,
        start_ms=2260,
        end_ms=42260,
        score=8.7,
        reason="Viral Hook",
        hook_text="This will blow your mind",
        selected=True,
    )
    db.add(cand)
    db.flush()

    q_report = {
        "technical_check": {"passed": True},
        "render_inspection": {"passed": True},
        "semantic_check": {"passed": True},
    }
    clip = Clip(
        clip_candidate_id=cand.id,
        status=ClipStatus.WAITING_APPROVAL,
        rendered_path="/storage/outputs/q_vid_001_short_2260_42260.mp4",
        quality_check_result=json.dumps(q_report),
    )
    db.add(clip)
    db.commit()

    resp = client.get("/queue")
    assert resp.status_code == 200
    html = resp.text
    # Check timestamp format MM:SS - MM:SS (XXs)
    assert "00:02 – 00:42 (40.0s)" in html
    # Check hook quote
    assert "This will blow your mind" in html
    # Check virality score
    assert "Score: 8.7" in html
    # Check rendered filename
    assert "q_vid_001_short_2260_42260.mp4" in html
    # Check quality check badges
    assert "Technical Pass" in html
    assert "Framing Pass" in html
    assert "Semantic Pass" in html


# ── 6. Dead-Letter Expandable Diagnostics ─────────────────────────────────────

def test_dead_letter_expandable_diagnostics(test_env):
    """Verifies that /dead-letter includes expandable <details> and <summary> tags with full traces."""
    client, db = test_env

    channel = Channel(youtube_channel_id="UC_dlch", name="DL Ch", watch_enabled=True, human_review_required=True)
    db.add(channel)
    db.flush()

    video = SourceVideo(
        channel_id=channel.id,
        youtube_video_id="failed_vid_001",
        title="Failed Video",
        status=SourceVideoStatus.FAILED,
        error_message="Traceback (most recent call last):\n  File 'pipeline.py', line 42, in run\nValueError: Downloader timeout",
    )
    db.add(video)
    db.flush()

    cand = ClipCandidate(source_video_id=video.id, start_ms=0, end_ms=30000, score=0.5, reason="Test", selected=True)
    db.add(cand)
    db.flush()

    clip = Clip(
        clip_candidate_id=cand.id,
        status=ClipStatus.FAILED,
        quality_check_result="RenderFailed: ffmpeg error - code 137 OOM killed",
    )
    db.add(clip)
    db.commit()

    resp = client.get("/dead-letter")
    assert resp.status_code == 200
    html = resp.text

    # Verify diagnostic traces and details tags
    assert "View Full Diagnostic Trace" in html
    assert "<details" in html
    assert "Downloader timeout" in html
    assert "ffmpeg error - code 137 OOM killed" in html


# ── 7. Dark Theme Contrast CSS ────────────────────────────────────────────────

def test_dark_theme_contrast_css():
    """Verifies base.html contains high-contrast color rules for dark theme."""
    base_html = Path("shorts_engine/dashboard/templates/base.html").read_text(encoding="utf-8")
    assert "--bs-secondary-color: #b0bec5" in base_html
    assert ".table-dark th" in base_html
    assert ".table-dark td" in base_html
    assert ".table-dark .text-muted" in base_html
