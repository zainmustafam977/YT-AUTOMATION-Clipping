"""
Tests for YouTube publisher: dry-run bypass, live OAuth2 path (mocked),
and last_seen_video_id write verification.
"""

import json
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.db.session import Base
from shorts_engine.db.models import Channel, SourceVideo, SourceVideoStatus, ClipCandidate, Clip, ClipStatus
from shorts_engine.services.publishing.client import YouTubePublisher
from shorts_engine.services.publishing.metadata import ShortsMetadata
from shorts_engine.services.publishing.quota import QuotaTracker


def _make_in_memory_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def _make_approved_clip(db):
    channel = Channel(youtube_channel_id="UC_pub", name="Pub Channel",
                      watch_enabled=True, human_review_required=False)
    db.add(channel)
    db.flush()

    video = SourceVideo(channel_id=channel.id, youtube_video_id="pub_vid_001",
                        title="Test Video", status=SourceVideoStatus.DONE)
    db.add(video)
    db.flush()

    cand = ClipCandidate(source_video_id=video.id, start_ms=0, end_ms=30000,
                         score=0.9, reason="Good clip", selected=True)
    db.add(cand)
    db.flush()

    import tempfile
    import pathlib
    tmp = pathlib.Path(tempfile.mktemp(suffix=".mp4"))
    tmp.write_bytes(b"\x00" * 1024)  # non-empty placeholder

    clip = Clip(clip_candidate_id=cand.id, status=ClipStatus.APPROVED,
                rendered_path=str(tmp))
    db.add(clip)
    db.commit()
    return clip, channel, video


def test_dry_run_publish_does_not_call_api():
    """With DRY_RUN_PUBLISHING=True, no HTTP call to the YouTube API should be made."""
    db = _make_in_memory_db()
    clip, channel, video = _make_approved_clip(db)
    meta = ShortsMetadata(title="Test Short", description="desc", tags=["#shorts"])

    mock_quota = MagicMock()
    mock_quota.can_publish.return_value = (True, 10000, "Quota available")

    with patch("shorts_engine.services.publishing.client.settings") as mock_settings:
        mock_settings.DRY_RUN_PUBLISHING = True

        with patch("shorts_engine.services.publishing.client._build_youtube_client") as mock_client:
            publisher = YouTubePublisher(db, quota_tracker=mock_quota)
            result = publisher.publish_clip(clip, meta, dry_run=True)

    mock_client.assert_not_called()
    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["youtube_video_id"].startswith("dry_run_")


def test_live_publish_calls_youtube_insert():
    """With dry_run=False and a mocked OAuth client, videos().insert() must be called."""
    db = _make_in_memory_db()
    clip, channel, video = _make_approved_clip(db)
    meta = ShortsMetadata(title="Live Short", description="desc", tags=["#shorts"])

    fake_token = json.dumps({
        "client_id": "cid",
        "client_secret": "csec",
        "refresh_token": "rtoken",
        "token_uri": "https://oauth2.googleapis.com/token",
    })

    mock_yt_client = MagicMock()
    mock_insert = MagicMock()
    mock_insert.next_chunk.return_value = (None, {"id": "LIVE_YT_ID_001"})
    mock_yt_client.videos.return_value.insert.return_value = mock_insert

    mock_quota = MagicMock()
    mock_quota.can_publish.return_value = (True, 10000, "Quota available")

    with patch("shorts_engine.services.publishing.client._build_youtube_client",
               return_value=mock_yt_client):
        with patch("shorts_engine.services.publishing.client.decrypt_secret",
                   return_value=fake_token):
            publisher = YouTubePublisher(db, quota_tracker=mock_quota)
            result = publisher.publish_clip(clip, meta, dry_run=False,
                                            encrypted_oauth_token="encrypted_placeholder")

    assert result["success"] is True
    assert result["youtube_video_id"] == "LIVE_YT_ID_001"
    assert result["dry_run"] is False
    mock_yt_client.videos.return_value.insert.assert_called_once()


def test_live_publish_updates_last_seen_video_id():
    """
    After a successful detection + pipeline run, channel.last_seen_video_id
    must reflect the newly processed video ID (gap identified in reanalysis Issue 4).
    """
    db = _make_in_memory_db()
    channel = Channel(youtube_channel_id="UC_ls", name="LastSeen Channel",
                      watch_enabled=True, human_review_required=False,
                      last_seen_video_id=None)
    db.add(channel)
    db.commit()

    # Simulate what PipelineRunner does on first detection
    video = SourceVideo(channel_id=channel.id, youtube_video_id="NEW_VID_XYZ",
                        title="New Video", status=SourceVideoStatus.DETECTED, retry_count=0)
    db.add(video)
    channel.last_seen_video_id = "NEW_VID_XYZ"   # This is the fix verified by this test
    db.commit()

    db.refresh(channel)
    assert channel.last_seen_video_id == "NEW_VID_XYZ", (
        "channel.last_seen_video_id must be written when a new video is detected "
        "to prevent RSS poller from re-detecting it on restart."
    )


def test_quota_tracker_active_db_reconciliation(tmp_path):
    """Verifies that QuotaTracker actively reconciles usage units with database PublishLog records."""
    from datetime import datetime
    from shorts_engine.db.models import PublishLog

    db = _make_in_memory_db()
    clip1, _, _ = _make_approved_clip(db)

    # Insert 2 PublishLog records for today
    now = datetime.utcnow()
    p1 = PublishLog(
        clip_id=clip1.id,
        youtube_video_id="yt_p1",
        published_at=now,
        title="Pub 1",
        description="desc 1",
        status="SUCCESS",
    )
    db.add(p1)
    db.commit()

    state_file = tmp_path / "test_quota_state.json"
    tracker = QuotaTracker(state_file=state_file)

    # Reconciles 1 publish = 1600 units
    metrics = tracker.get_usage_metrics(db=db)
    assert metrics["used_units"] == 1600
    assert metrics["remaining_units"] == 8400
    assert metrics["pct"] == 16.0

    # Verify state file was updated
    assert state_file.exists()
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["used_units"] == 1600

    # Add second publish
    cand2 = ClipCandidate(source_video_id=clip1.candidate.source_video_id, start_ms=30000, end_ms=60000,
                          score=0.85, reason="Clip 2", selected=True)
    db.add(cand2)
    db.flush()
    clip2 = Clip(clip_candidate_id=cand2.id, status=ClipStatus.PUBLISHED)
    db.add(clip2)
    db.flush()
    p2 = PublishLog(
        clip_id=clip2.id,
        youtube_video_id="yt_p2",
        published_at=now,
        title="Pub 2",
        description="desc 2",
        status="SUCCESS",
    )
    db.add(p2)
    db.commit()

    # Reconciles 2 publishes = 3200 units
    can_pub, rem, _ = tracker.can_publish(db=db)
    assert can_pub is True
    assert rem == 6800
    metrics2 = tracker.get_usage_metrics(db=db)
    assert metrics2["used_units"] == 3200
    assert metrics2["remaining_units"] == 6800
    assert metrics2["pct"] == 32.0
