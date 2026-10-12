"""
Tests for Phase 1: Concurrency Protection & Pipeline Queue Hardening.
- Active video mutex lock registry prevents duplicate pipeline runs
- Dashboard /process-video rejects in-progress duplicates with amber warning
- Atomic clip claiming (claim_clip_by_id) guarantees single winner
- Compositor skips re-render when valid output file exists
- Test logging is isolated to shorts_engine_test.log
"""

from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.config.logging_config import is_test_environment, get_log_file_path, TEST_LOG_FILE_PATH
from shorts_engine.db.session import Base
from shorts_engine.db.models import Channel, SourceVideo, SourceVideoStatus, ClipCandidate, Clip, ClipStatus, User, UserRole
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.routes import status as status_route
from shorts_engine.dashboard.auth import require_operator, verify_csrf
from shorts_engine.orchestration.runner import PipelineRunner, get_video_lock
from shorts_engine.services.worker.runner import claim_clip_by_id
from shorts_engine.services.rendering.compositor import VideoCompositor, RenderValidationResult


@pytest.fixture
def memory_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


def test_test_log_isolation():
    """Verifies that running under pytest redirects logging away from shorts_engine.log."""
    assert is_test_environment() is True
    assert get_log_file_path() == TEST_LOG_FILE_PATH
    assert get_log_file_path().name == "shorts_engine_test.log"


def test_active_video_lock_prevents_duplicate_concurrent_run(memory_db):
    """Verifies that an active lock on a video ID blocks a concurrent second pipeline run."""
    video_id = "concur_vid1"
    v_lock = get_video_lock(video_id)
    
    # Simulate first process holding the lock
    assert v_lock.acquire(blocking=False) is True
    try:
        runner = PipelineRunner(memory_db)
        result = runner.run_pipeline_for_video(video_id, dry_run=True)
        assert result["status"] == "ALREADY_RUNNING"
        assert result["clips_count"] == 0
        assert "already actively processing" in result["message"]
    finally:
        v_lock.release()


def test_claim_clip_by_id_single_winner(memory_db):
    """Verifies that claim_clip_by_id atomically claims for one worker and rejects another."""
    chan = Channel(youtube_channel_id="UC_DUMMY_CHAN", name="Dummy")
    memory_db.add(chan)
    memory_db.commit()

    vid = SourceVideo(channel_id=chan.id, youtube_video_id="dummy-vid", title="Dummy Video")
    memory_db.add(vid)
    memory_db.commit()

    cand = ClipCandidate(
        source_video_id=vid.id,
        start_ms=1000,
        end_ms=25000,
        score=8.5,
        reason="Great viral hook",
    )
    memory_db.add(cand)
    memory_db.commit()

    clip = Clip(
        clip_candidate_id=cand.id,
        status=ClipStatus.RENDERING,
        rendered_path=None,
    )
    memory_db.add(clip)
    memory_db.commit()

    # Worker A claims
    claimed_a = claim_clip_by_id(memory_db, clip.id, worker_id="worker_A")
    assert claimed_a is not None
    assert claimed_a.rendered_path == "CLAIMED:worker_A"

    # Worker B attempts to claim the same clip
    claimed_b = claim_clip_by_id(memory_db, clip.id, worker_id="worker_B")
    assert claimed_b is None


def test_status_endpoint_rejects_in_progress_duplicate(memory_db):
    """Verifies that /process-video rejects a video that is currently in-progress."""
    app = create_dashboard_app()
    app.dependency_overrides[status_route.get_db] = lambda: memory_db
    
    operator = User(
        id="test-op-lock",
        username="test_op_concurrency",
        password_hash="hash",
        role=UserRole.OPERATOR,
        is_active=True,
    )
    app.dependency_overrides[require_operator] = lambda: operator
    app.dependency_overrides[verify_csrf] = lambda: True

    channel = Channel(
        youtube_channel_id="UC_TEST_CHAN",
        name="Test Chan",
        watch_enabled=True,
    )
    memory_db.add(channel)
    memory_db.commit()

    video = SourceVideo(
        channel_id=channel.id,
        youtube_video_id="WBhyV0qMsVU",
        title="Active Video",
        status=SourceVideoStatus.TRANSCRIBING,
    )
    memory_db.add(video)
    memory_db.commit()

    client = TestClient(app)
    resp = client.post(
        "/process-video",
        data={"video_input": "WBhyV0qMsVU"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "already_running"
    assert "already in progress" in data["message"]


def test_compositor_skips_render_when_valid_output_exists(tmp_path):
    """Verifies that VideoCompositor skips invoking FFmpeg if valid output file already exists."""
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    existing_file = output_dir / "test_existing.mp4"
    existing_file.write_bytes(b"dummy_mp4_bytes_12345")

    compositor = VideoCompositor(output_dir=output_dir)

    mock_validation = RenderValidationResult(
        is_valid=True,
        duration_sec=30.0,
        width=1080,
        height=1920,
        has_audio=True,
        file_size_bytes=existing_file.stat().st_size,
    )

    crop_plan = MagicMock()
    crop_plan.to_ffmpeg_filter.return_value = "scale=1080:1920"

    with patch.object(compositor, "validate_render", return_value=mock_validation) as mock_val:
        with patch("subprocess.run") as mock_run:
            res_path = compositor.render_short(
                source_video_path=tmp_path / "source.mp4",
                start_ms=0,
                end_ms=30000,
                crop_plan=crop_plan,
                subtitle_ass_path=None,
                output_filename="test_existing.mp4",
            )
            assert res_path == existing_file
            mock_val.assert_called_once_with(existing_file)
            mock_run.assert_not_called()
