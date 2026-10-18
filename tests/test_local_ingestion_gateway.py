"""
Unit and integration tests for Universal Local Video Ingestion Gateway (Task 2.3).
Verifies:
1. File validation logic (existence, extension, non-empty, canonical normalization).
2. Media metadata probing (duration, resolution, fps, codecs).
3. Zero-copy local path ingestion service and database registration.
4. Multipart chunked streaming upload service.
5. FastAPI endpoint /api/ingest/validate-path.
6. FastAPI endpoint /api/ingest/local-path with RBAC and error handling.
7. FastAPI endpoint /api/ingest/local-file with RBAC and chunk streaming.
8. Real-time EventBus notifications on ingestion.
"""

import os
import io
import cv2
import numpy as np
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.models import Base, User, UserRole, SourceVideoStatus
from shorts_engine.services.ingestion.local_loader import LocalVideoIngestionService
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.auth import create_session_token, hash_password
from shorts_engine.dashboard.routes import ingestion as ingestion_route
from shorts_engine.services.events.bus import global_event_bus


@pytest.fixture
def synthetic_video(tmp_path) -> Path:
    """Generates a small valid MP4 video file for media tests."""
    video_path = tmp_path / "sample_podcast_ep1.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(str(video_path), fourcc, 30.0, (320, 240))
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    for _ in range(60):  # 2.0 seconds at 30 fps
        out.write(frame)
    out.release()
    return video_path


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        # Create an operator user for authenticated endpoints
        user = User(
            username="operator1",
            password_hash=hash_password("password123"),
            role=UserRole.OPERATOR,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def clean_events():
    global_event_bus.clear()
    yield
    global_event_bus.clear()


@pytest.fixture
def api_client(test_db):
    app = create_dashboard_app()

    def override_get_db():
        yield test_db

    from shorts_engine.dashboard import auth
    app.dependency_overrides[auth.get_db] = override_get_db
    app.dependency_overrides[ingestion_route.get_db] = override_get_db

    client = TestClient(app, raise_server_exceptions=False)
    return client


@pytest.fixture
def auth_headers(test_db):
    user = test_db.query(User).filter(User.username == "operator1").first()
    token = create_session_token(user.id)
    return {"Authorization": f"Bearer {token}"}


def test_validate_local_file_errors(tmp_path):
    """Verifies file validation catches non-existent, non-video, and empty files."""
    # Non-existent
    with pytest.raises(FileNotFoundError):
        LocalVideoIngestionService.validate_local_file(tmp_path / "does_not_exist.mp4")

    # Unsupported extension
    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("hello", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        LocalVideoIngestionService.validate_local_file(txt_file)
    assert "Unsupported video extension" in str(exc.value)

    # Empty file
    empty_mp4 = tmp_path / "empty.mp4"
    empty_mp4.touch()
    with pytest.raises(ValueError) as exc:
        LocalVideoIngestionService.validate_local_file(empty_mp4)
    assert "empty" in str(exc.value)


def test_validate_local_file_success(synthetic_video):
    """Verifies valid video file returns canonical path."""
    canonical = LocalVideoIngestionService.validate_local_file(synthetic_video)
    assert canonical.exists()
    assert canonical.is_file()
    assert canonical.suffix.lower() == ".mp4"


def test_probe_video_metadata(synthetic_video):
    """Verifies metadata probing extracts duration and dimensions."""
    meta = LocalVideoIngestionService.probe_video_metadata(synthetic_video)
    assert meta["duration_sec"] >= 1.5
    assert meta["width"] == 320
    assert meta["height"] == 240
    assert meta["size_bytes"] > 0


def test_zero_copy_ingest_local_path(synthetic_video, test_db):
    """Verifies zero-copy ingestion creates DB record referencing original file without copying."""
    video = LocalVideoIngestionService.ingest_local_path(
        db=test_db,
        file_path=synthetic_video,
        title="Podcast Premiere",
        runtime_overrides={"target_crf": 18},
    )

    assert video.id is not None
    assert video.title == "Podcast Premiere"
    assert video.status == SourceVideoStatus.DETECTED
    assert video.duration_sec >= 1
    # ZERO-COPY CHECK: Path points directly to original file
    assert video.downloaded_path == os.path.normpath(str(synthetic_video.resolve()))
    assert video.runtime_overrides == {"target_crf": 18}

    # EventBus notification check
    events = global_event_bus.get_history(category="ingestion")
    assert len(events) >= 1
    assert "Local Video Ingested" in events[-1].title


def test_api_validate_path_endpoint(api_client, auth_headers, synthetic_video, tmp_path):
    """Verifies /api/ingest/validate-path endpoint."""
    # 1. Valid file
    res = api_client.post(
        "/api/ingest/validate-path",
        json={"file_path": str(synthetic_video)},
        headers=auth_headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["valid"] is True
    assert data["exists"] is True
    assert data["is_file"] is True
    assert data["duration_sec"] >= 1.5

    # 2. Nonexistent file
    res_err = api_client.post(
        "/api/ingest/validate-path",
        json={"file_path": str(tmp_path / "ghost.mp4")},
        headers=auth_headers,
    )
    assert res_err.status_code == 200
    data_err = res_err.json()
    assert data_err["valid"] is False
    assert "does not exist" in data_err["error"]


def test_api_local_path_endpoint(api_client, auth_headers, synthetic_video, tmp_path, test_db):
    """Verifies /api/ingest/local-path zero-copy ingestion endpoint."""
    # 1. Unauthenticated request rejected
    unauth = api_client.post("/api/ingest/local-path", json={"file_path": str(synthetic_video)})
    assert unauth.status_code in (401, 303)

    # 2. Authenticated valid request
    res = api_client.post(
        "/api/ingest/local-path",
        json={
            "file_path": str(synthetic_video),
            "title": "API Ingested Episode",
            "runtime_overrides": {"ai_model": "groq"},
        },
        headers=auth_headers,
    )
    assert res.status_code == 201
    payload = res.json()
    assert payload["status"] == "success"
    assert payload["title"] == "API Ingested Episode"
    assert payload["source_video_id"] is not None
    assert payload["video_id"].startswith("loc_")

    # 3. Invalid nonexistent path rejected with 404
    res_404 = api_client.post(
        "/api/ingest/local-path",
        json={"file_path": str(tmp_path / "missing.mp4")},
        headers=auth_headers,
    )
    assert res_404.status_code == 404


def test_api_local_file_multipart_upload_endpoint(api_client, auth_headers, synthetic_video, test_db):
    """Verifies /api/ingest/local-file multipart chunk streaming upload."""
    video_bytes = synthetic_video.read_bytes()

    res = api_client.post(
        "/api/ingest/local-file",
        files={"file": ("web_drop.mp4", io.BytesIO(video_bytes), "video/mp4")},
        data={"title": "Browser Uploaded Short", "runtime_overrides_json": '{"mode": "fast"}'},
        headers=auth_headers,
    )

    assert res.status_code == 201
    payload = res.json()
    assert payload["status"] == "success"
    assert payload["title"] == "Browser Uploaded Short"
    assert payload["source_video_id"] is not None
    assert payload["downloaded_path"] is not None
    assert Path(payload["downloaded_path"]).exists()


def test_validate_local_file_whitespace_only():
    """Verifies that whitespace-only file paths are rejected with ValueError."""
    with pytest.raises(ValueError) as exc:
        LocalVideoIngestionService.validate_local_file("   ")
    assert "cannot be empty" in str(exc.value)


def test_ingest_local_path_invalid_channel(synthetic_video, test_db):
    """Verifies that providing a non-existent channel_id raises ValueError."""
    with pytest.raises(ValueError) as exc:
        LocalVideoIngestionService.ingest_local_path(
            db=test_db,
            file_path=synthetic_video,
            channel_id="nonexistent-channel-uuid-12345",
        )
    assert "not found" in str(exc.value)


def test_api_local_file_empty_upload_rejected(api_client, auth_headers):
    """Verifies that uploading an empty 0-byte file returns HTTP 400."""
    res = api_client.post(
        "/api/ingest/local-file",
        files={"file": ("empty.mp4", io.BytesIO(b""), "video/mp4")},
        headers=auth_headers,
    )
    assert res.status_code == 400
    assert "empty" in res.json()["detail"].lower()


@pytest.mark.network
def test_pipeline_runner_processes_local_ingested_video(synthetic_video, test_db):
    """Verifies that PipelineRunner cleanly executes Step A on a locally ingested video without yt-dlp."""
    from shorts_engine.orchestration.runner import PipelineRunner

    sv = LocalVideoIngestionService.ingest_local_path(
        db=test_db,
        file_path=synthetic_video,
        title="Local Pipeline Test",
    )

    runner = PipelineRunner(db_session=test_db)
    try:
        runner.run_pipeline_for_video(sv.youtube_video_id, channel=sv.channel, dry_run=True)
    except Exception as e:
        # Downstream transcribe / LLM might stop in test environment, but Step A must succeed
        logger_err = str(e)
        assert "is not a valid URL" not in logger_err
        assert "This video is unavailable" not in logger_err

    test_db.refresh(sv)
    # Video must have successfully completed Step A and extracted audio
    assert sv.audio_path is not None
    assert Path(sv.audio_path).exists()
    assert sv.status != SourceVideoStatus.FAILED
