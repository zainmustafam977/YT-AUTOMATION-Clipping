"""
Tests for Component 1: Sub-Frame Precision Interactive Clip Trimmer & Re-Timer
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from shorts_engine.db.session import Base
from shorts_engine.services.transcription.formatter import WordSegment
from shorts_engine.services.rendering.trimmer import snap_to_word_boundary, ClipTrimmerService
from shorts_engine.db.models import Clip, ClipCandidate, SourceVideo, Channel, User, UserRole
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.routes import queue as queue_route
from shorts_engine.dashboard.auth import require_operator, require_viewer, verify_csrf, hash_password, create_session_token, generate_csrf_token


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    app = create_dashboard_app()
    app.dependency_overrides[queue_route.get_db] = lambda: db_session
    fake_user = User(username="test_op_trim", role=UserRole.OPERATOR, is_active=True)
    app.dependency_overrides[require_operator] = lambda: fake_user
    app.dependency_overrides[require_viewer] = lambda: fake_user
    app.dependency_overrides[verify_csrf] = lambda: True
    return TestClient(app)


def test_snap_to_word_boundary_speech_pause():
    """Verifies that start/end boundaries snap to natural word boundaries instead of cutting words."""
    words = [
        WordSegment(word="Never", start=1.0, end=1.4),
        WordSegment(word="make", start=1.6, end=2.0),
        WordSegment(word="this", start=2.2, end=2.5),
        WordSegment(word="huge", start=3.0, end=3.5),
        WordSegment(word="mistake", start=3.7, end=4.2),
        WordSegment(word="again", start=4.5, end=5.0),
        WordSegment(word="today", start=15.0, end=15.6),
    ]

    # Target 1.8 is in the middle of 'make' (1.6 - 2.0) -> snaps to 1.6
    # Target 15.2 is in the middle of 'today' (15.0 - 15.6) -> snaps to 15.6
    snapped_start, snapped_end = snap_to_word_boundary(words, target_start_sec=1.8, target_end_sec=15.2)

    assert snapped_start == 1.6
    assert snapped_end == 15.6


def test_snap_to_word_boundary_fallback_on_empty():
    """Verifies graceful fallback to requested boundaries when no transcript words exist."""
    snapped_start, snapped_end = snap_to_word_boundary([], target_start_sec=5.0, target_end_sec=25.0)
    assert snapped_start == 5.0
    assert snapped_end == 25.0


def test_clip_trimmer_service_duration_validation(db_session, tmp_path):
    """Verifies that intervals under 10 seconds are rejected with a clear ValueError."""
    dummy_source = tmp_path / "dummy.mp4"
    dummy_source.write_bytes(b"dummy")

    trimmer = ClipTrimmerService(db_session)
    mock_clip = MagicMock()
    mock_clip.candidate = MagicMock()
    mock_clip.candidate.source_video = MagicMock()
    mock_clip.candidate.source_video.downloaded_path = str(dummy_source)

    with patch.object(db_session, "query") as mock_query:
        mock_query.return_value.filter_by.return_value.first.return_value = mock_clip
        with pytest.raises(ValueError, match="too short"):
            trimmer.trim_clip(
                clip_id="test_clip_1",
                start_sec=10.0,
                end_sec=15.0,  # 5s duration < 10s min
                snap_words=False,
            )


def test_clip_trimmer_service_successful_execution(db_session, tmp_path, monkeypatch):
    """Verifies successful trimming, crop re-computation, subtitle regeneration, and DB atomic updates."""
    # Create real DB records in isolated SQLite session
    ch = Channel(name="Trim Test Ch", youtube_channel_id="UC_trim_test_1")
    db_session.add(ch)
    db_session.commit()

    source_file = tmp_path / "source_video.mp4"
    source_file.write_bytes(b"dummy video data stream " * 100)

    sv = SourceVideo(
        youtube_video_id="trim_src_1",
        title="Source Video For Trimming",
        channel_id=ch.id,
        downloaded_path=str(source_file),
    )
    db_session.add(sv)
    db_session.commit()

    cand = ClipCandidate(
        source_video_id=sv.id,
        start_ms=1000,
        end_ms=40000,
        score=85.0,
        reason="Initial candidate",
        hook_text="Check out this tip",
    )
    db_session.add(cand)
    db_session.commit()

    rendered_file = tmp_path / "initial_render.mp4"
    rendered_file.write_bytes(b"rendered mp4 data" * 50)

    clip = Clip(
        clip_candidate_id=cand.id,
        rendered_path=str(rendered_file),
    )
    db_session.add(clip)
    db_session.commit()

    trimmer = ClipTrimmerService(db_session)

    # Mock external media engines (compositor, reframer, subtitle generator)
    new_rendered = tmp_path / "trimmed_render.mp4"
    new_rendered.write_bytes(b"new trimmed video content" * 60)

    monkeypatch.setattr(trimmer.reframer, "compute_crop_plan", lambda *a, **kw: MagicMock())
    monkeypatch.setattr(trimmer.ass_generator, "generate_ass", lambda *a, **kw: Path(tmp_path / "sub.ass"))
    monkeypatch.setattr(trimmer.compositor, "render_short", lambda *a, **kw: new_rendered)
    monkeypatch.setattr(trimmer.compositor, "validate_render", lambda p: MagicMock(is_valid=True, error=None))

    res = trimmer.trim_clip(
        clip_id=clip.id,
        start_sec=2.0,
        end_sec=22.0,
        snap_words=False,
    )

    assert res["success"] is True
    assert res["start_sec"] == 2.0
    assert res["end_sec"] == 22.0
    assert res["duration_sec"] == 20.0
    assert res["rendered_path"] == str(new_rendered)

    # Verify atomic update on candidate and clip
    db_session.refresh(cand)
    db_session.refresh(clip)
    assert cand.start_ms == 2000
    assert cand.end_ms == 22000
    assert clip.rendered_path == str(new_rendered)
    assert "trimmed_metadata" in json.loads(clip.quality_check_result)


def test_trim_clip_route_endpoint(client, db_session, tmp_path, monkeypatch):
    """Verifies that POST /queue/{clip_id}/trim executes with valid CSRF and operator auth."""
    # Ensure operator user exists
    user = db_session.query(User).filter_by(username="test_op_trim").first()
    if not user:
        user = User(
            username="test_op_trim",
            password_hash=hash_password("OperatorPass123!"),
            role=UserRole.OPERATOR,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()

    token = create_session_token(user.id)
    csrf_token = generate_csrf_token(token)
    client.cookies.set("session_id", token)

    mock_trim_res = {
        "success": True,
        "clip_id": "c123",
        "start_sec": 5.0,
        "end_sec": 30.0,
        "duration_sec": 25.0,
        "rendered_path": "/path/to/trimmed.mp4",
        "is_valid": True,
    }

    with patch("shorts_engine.services.rendering.trimmer.ClipTrimmerService.trim_clip", return_value=mock_trim_res):
        resp = client.post(
            "/queue/c123/trim",
            data={
                "start_sec": "5.0",
                "end_sec": "30.0",
                "snap_words": "true",
                "csrf_token": csrf_token,
            },
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        trigger = json.loads(resp.headers.get("HX-Trigger", "{}"))
        assert "showToast" in trigger
        assert "clipTrimmed" in trigger
