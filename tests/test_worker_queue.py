"""
Tests for decoupled worker queue locking, CUDA cache flushing, and job execution.
"""

from unittest.mock import patch, MagicMock
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.db.models import Channel, SourceVideo, SourceVideoStatus, ClipCandidate, Clip, ClipStatus
from shorts_engine.services.worker.runner import WorkerRunner, claim_next_clip, clear_cuda_cache


def _setup_test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session


def _seed_pending_clip(db, video_path: str = "/tmp/fake_video.mp4"):
    channel = Channel(youtube_channel_id="UC_worker", name="Worker Ch", watch_enabled=True)
    db.add(channel)
    db.flush()
    video = SourceVideo(
        channel_id=channel.id,
        youtube_video_id="worker_vid_01",
        title="Worker Test Video",
        status=SourceVideoStatus.DONE,
        downloaded_path=video_path,
    )
    db.add(video)
    db.flush()
    cand = ClipCandidate(
        source_video_id=video.id,
        start_ms=0,
        end_ms=25000,
        score=0.9,
        reason="Good segment",
        selected=True,
    )
    db.add(cand)
    db.flush()
    clip = Clip(
        clip_candidate_id=cand.id,
        status=ClipStatus.RENDERING,
        rendered_path=None,
    )
    db.add(clip)
    db.commit()
    return clip


def test_clear_cuda_cache_runs_without_error():
    """Verifies that clear_cuda_cache() executes safely even without GPU or CUDA."""
    clear_cuda_cache()  # Should not raise exception


def test_atomic_claim_next_clip_single_winner():
    """Verifies that multiple concurrent claim attempts result in exactly one winner."""
    Session = _setup_test_db()
    db1 = Session()
    db2 = Session()

    clip = _seed_pending_clip(db1)

    # Worker 1 claims
    claimed_by_w1 = claim_next_clip(db1, worker_id="worker-alpha")
    assert claimed_by_w1 is not None
    assert claimed_by_w1.id == clip.id
    assert claimed_by_w1.rendered_path == "CLAIMED:worker-alpha"

    # Worker 2 attempts to claim the same queue -> returns None
    claimed_by_w2 = claim_next_clip(db2, worker_id="worker-beta")
    assert claimed_by_w2 is None

    db1.close()
    db2.close()


def test_worker_run_once_processes_job(tmp_path):
    """Verifies WorkerRunner.run_once() successfully processes a pending job and returns 1."""
    Session = _setup_test_db()
    db = Session()
    fake_video = tmp_path / "fake_video.mp4"
    fake_video.write_bytes(b"mock_mp4_bytes")
    clip = _seed_pending_clip(db, video_path=str(fake_video))
    clip_id = clip.id
    db.close()

    runner = WorkerRunner(session_factory=Session, worker_id="test-worker-1")

    # Mock the internal heavy rendering and quality check calls
    with patch.object(runner.reframer, "compute_crop_plan", return_value={"points": []}), \
         patch.object(runner.ass_generator, "generate_ass", return_value=Path("/tmp/fake.ass")), \
         patch.object(runner.sfx_mapper, "map_sfx_cues", return_value=[]), \
         patch.object(runner.compositor, "render_short", return_value=Path("/tmp/rendered.mp4")), \
         patch.object(runner.quality_gate, "evaluate_clip", return_value=(ClipStatus.WAITING_APPROVAL, {})):

        processed_count = runner.run_once()
        assert processed_count == 1

        # Queue should now be empty
        assert runner.run_once() == 0

        # Verify final state in DB
        db_check = Session()
        updated_clip = db_check.query(Clip).filter_by(id=clip_id).first()
        assert updated_clip.status == ClipStatus.WAITING_APPROVAL
        assert Path(updated_clip.rendered_path) == Path("/tmp/rendered.mp4")
        db_check.close()
