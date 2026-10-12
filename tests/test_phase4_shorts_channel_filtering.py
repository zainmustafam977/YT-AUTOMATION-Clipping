"""
Unit and integration tests for Phase 4:
- Pre-ingestion Shorts filtering (#Shorts in title/tags, <= 180s duration boundary)
- Channel ID format validation (UC prefix, @handle helpful rejection)
- WebSub callback public URL alert banner
- YouTube channel clickable links on /channels and / (Status)
"""

from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.config.settings import settings
from shorts_engine.db.session import Base
from shorts_engine.db.models import Channel, SourceVideo, SourceVideoStatus, User, UserRole
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.routes import channels as channels_route
from shorts_engine.dashboard.routes import status as status_route
from shorts_engine.dashboard.auth import get_current_user, require_operator, require_viewer, require_admin, verify_csrf
from shorts_engine.services.detection.filter import is_existing_short
from shorts_engine.services.ingestion.downloader import VideoDownloadResult
from shorts_engine.orchestration.runner import PipelineRunner


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
    app.dependency_overrides[channels_route.get_db] = override_get_db

    test_op = User(id="op-p4", username="operator_p4", password_hash="hash", role=UserRole.OPERATOR, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: test_op
    app.dependency_overrides[require_viewer] = lambda: test_op
    app.dependency_overrides[require_operator] = lambda: test_op
    app.dependency_overrides[require_admin] = lambda: test_op
    app.dependency_overrides[verify_csrf] = lambda: True

    client = TestClient(app)
    db = TestSession()
    yield client, db
    db.close()


# ── 1. Shorts Detection Filter Unit Tests ─────────────────────────────────────

def test_is_existing_short_primary_hashtag():
    # Primary signal: #Shorts in title
    is_short, reason = is_existing_short("Crazy AI Breakthrough #Shorts", duration_sec=300)
    assert is_short is True
    assert "#Shorts" in reason

    is_short2, reason2 = is_existing_short("Check out this clip #short", duration_sec=500)
    assert is_short2 is True
    assert "#Shorts" in reason2

    # Primary signal: #shorts in tags
    is_short3, reason3 = is_existing_short("Video Title", duration_sec=400, tags=["ai", "tech", "#shorts"])
    assert is_short3 is True
    assert "#Shorts" in reason3


def test_is_existing_short_secondary_duration_ceiling():
    # Secondary signal: duration <= 180s
    is_short, reason = is_existing_short("Quick Update", duration_sec=120)
    assert is_short is True
    assert "180s ceiling" in reason

    is_short_border, reason_border = is_existing_short("Three Minute Clip", duration_sec=180)
    assert is_short_border is True
    assert "180s ceiling" in reason_border

    # Long-form video (> 180s, no #shorts tag)
    is_not_short, reason_none = is_existing_short("Complete 1-Hour Podcast", duration_sec=3600)
    assert is_not_short is False
    assert reason_none == ""


# ── 2. PipelineRunner Pre-Ingestion Shorts Skipping ───────────────────────────

def test_pipeline_runner_skips_existing_short(test_env, tmp_path):
    _, db = test_env

    ch = Channel(id="ch-filter-1", youtube_channel_id="UC_filter_test", name="Filter Channel")
    db.add(ch)
    db.commit()

    runner = PipelineRunner(db)

    # Mock Ingestion returning a video with #Shorts in title
    dummy_video = tmp_path / "already_a_short.mp4"
    dummy_video.write_bytes(b"\x00" * 100)
    dummy_audio = tmp_path / "already_a_short.wav"
    dummy_audio.write_bytes(b"\x00" * 100)

    mock_download = VideoDownloadResult(
        video_id="short_vid01",
        title="Mind Blowing Fact #Shorts",
        duration_sec=45,
        video_path=dummy_video,
        audio_path=dummy_audio,
        metadata={"tags": ["#shorts", "facts"]},
    )

    with patch.object(runner.ingestion, "download_video", return_value=mock_download):
        clips = runner.run_pipeline_for_video("https://youtube.com/watch?v=short_vid01", channel=ch)
        assert clips == []

        # Check that video was saved and transitioned to DONE with informative note
        video = db.query(SourceVideo).filter_by(youtube_video_id="short_vid01").first()
        assert video is not None
        assert video.status == SourceVideoStatus.DONE
        assert "Skipped: already a YouTube Short" in video.error_message


# ── 3. Channel Format Validation Tests ────────────────────────────────────────

def test_channel_add_valid_uc_id(test_env):
    client, db = test_env

    # 24-character official UC channel ID
    resp = client.post(
        "/channels/add",
        data={
            "youtube_channel_id": "UC_x5XG1OV2P6uZZ5FSM9Ttw",
            "name": "Veritasium",
            "human_review": "on",
        },
    )
    assert resp.status_code in (200, 303)
    ch = db.query(Channel).filter_by(youtube_channel_id="UC_x5XG1OV2P6uZZ5FSM9Ttw").first()
    assert ch is not None
    assert ch.name == "Veritasium"


def test_channel_add_extracts_from_full_url(test_env):
    client, db = test_env

    resp = client.post(
        "/channels/add",
        data={
            "youtube_channel_id": "https://www.youtube.com/channel/UCb_test_channel_url_id2",
            "name": "URL Extracted Channel",
        },
    )
    assert resp.status_code in (200, 303)
    ch = db.query(Channel).filter_by(youtube_channel_id="UCb_test_channel_url_id2").first()
    assert ch is not None


def test_channel_add_rejects_handle_with_helpful_guidance(test_env):
    client, _ = test_env

    # Reject @handle with 422 and helpful message
    resp = client.post(
        "/channels/add",
        data={
            "youtube_channel_id": "@veritasium",
            "name": "Veritasium Handle",
        },
    )
    assert resp.status_code == 422
    assert "Handles (like @ChannelName) cannot be queried directly" in resp.json()["detail"]


def test_channel_add_rejects_invalid_non_uc_format(test_env):
    client, _ = test_env

    # 1. Reject non-UC prefix
    resp = client.post(
        "/channels/add",
        data={
            "youtube_channel_id": "my_random_custom_name",
            "name": "Invalid Channel",
        },
    )
    assert resp.status_code == 422
    assert "must begin with 'UC'" in resp.json()["detail"]

    # 2. Reject UC prefix with incorrect length (e.g. 12 characters instead of 24)
    short_resp = client.post(
        "/channels/add",
        data={
            "youtube_channel_id": "UC1234567890",
            "name": "Short Channel ID",
        },
    )
    assert short_resp.status_code == 422
    assert "exactly 22 characters" in short_resp.json()["detail"]


# ── 4. WebSub Banner and Channel Links ────────────────────────────────────────

def test_channels_page_websub_alert_and_channel_links(test_env):
    client, db = test_env

    ch = Channel(id="ch-link-1", youtube_channel_id="UC_link_test_123", name="Linked Channel")
    db.add(ch)
    db.commit()

    # When callback URL is unset / localhost, show alert banner
    with patch.object(settings, "WEBSUB_CALLBACK_URL", None):
        resp = client.get("/channels")
        assert resp.status_code == 200
        html = resp.text
        assert "WebSub Hub Notice" in html
        assert "Periodic RSS polling fallback is active" in html
        # Clickable link to YouTube
        assert "https://www.youtube.com/channel/UC_link_test_123" in html


def test_status_page_renders_clickable_channel_links(test_env):
    client, db = test_env

    ch = Channel(id="ch-status-1", youtube_channel_id="UC_status_channel", name="Status Channel")
    db.add(ch)
    db.commit()

    video = SourceVideo(
        id="sv-status-vid-1",
        channel_id=ch.id,
        youtube_video_id="test_vid_p4",
        title="Sample Video",
        status=SourceVideoStatus.DONE,
        duration_sec=300.0,
    )
    db.add(video)
    db.commit()

    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "https://www.youtube.com/channel/UC_status_channel" in html
    assert "Status Channel" in html
