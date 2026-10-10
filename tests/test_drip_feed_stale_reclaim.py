from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.models import (
    Base, Channel, SourceVideo, ClipCandidate, Clip, ClipStatus
)
from shorts_engine.db.security import encrypt_secret
from shorts_engine.services.publishing.drip_feed import (
    check_and_publish_due_clips, build_clip_metadata
)
from shorts_engine.services.publishing.quota import QuotaTracker, UPLOAD_COST_UNITS


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

    channel = Channel(
        id="chan_pub",
        youtube_channel_id="UC_pub_channel",
        name="Pub Channel",
        watch_enabled=True,
        human_review_required=False,
    )
    session.add(channel)

    source_vid = SourceVideo(
        id="sv_pub",
        channel_id=channel.id,
        youtube_video_id="yt_source_1",
        title="Source Video",
        duration_sec=300,
        status="DONE",
    )
    session.add(source_vid)

    candidate = ClipCandidate(
        id="cand_pub",
        source_video_id=source_vid.id,
        start_ms=1000,
        end_ms=30000,
        score=0.9,
        reason="Viral segment",
        hook_text="Check this amazing discovery!",
        selected=True,
    )
    session.add(candidate)
    session.commit()

    yield session
    session.close()


def test_build_clip_metadata_embeds_clip_token(db_session):
    """build_clip_metadata must embed [clip:<clip_id>] into description for out-of-band matching."""
    clip = Clip(
        id="clip_token_test",
        clip_candidate_id="cand_pub",
        rendered_path="dummy.mp4",
        status=ClipStatus.APPROVED,
    )
    db_session.add(clip)
    db_session.commit()

    metadata = build_clip_metadata(clip)
    assert f"[clip:{clip.id}]" in metadata.description


def test_publishing_claim_and_idempotency_guard(db_session, tmp_path):
    """
    1. Ambiguity rule: Clips in PUBLISHING within 15 min remain in PUBLISHING; clips past 15 min are moved to FAILED_NEEDS_REVIEW.
    2. Uploads playlist check: If [clip:<clip_id>] is found in recent uploads, reuse ID and mark PUBLISHED.
    3. Unlisted upload decrements quota by 1,600 units.
    """
    # 1. Setup quota tracker with temp file
    quota_file = tmp_path / "quota.json"
    tracker = QuotaTracker(state_file=quota_file)
    initial_used = tracker.get_used_units()

    # 2. Create dummy rendered file
    dummy_video = tmp_path / "test_render.mp4"
    dummy_video.write_bytes(b"dummy mp4 video bytes")

    # 3. Create a clip in PUBLISHING within ambiguity window (5 mins < 15 mins)
    # and a clip past 15 minutes (20 mins > 15 mins)
    stale_clip = Clip(
        id="clip_stale_ambiguous",
        clip_candidate_id="cand_pub",
        rendered_path=str(dummy_video),
        status=ClipStatus.PUBLISHING,
        scheduled_publish_at=datetime.utcnow() - timedelta(minutes=10),
        updated_at=datetime.utcnow() - timedelta(minutes=5),
    )
    cand_past = ClipCandidate(
        id="cand_pub_past",
        source_video_id="sv_pub",
        start_ms=1000,
        end_ms=15000,
        score=0.85,
        reason="Past window clip",
        hook_text="Past window hook",
        selected=True,
    )
    db_session.add(cand_past)

    stale_clip_past = Clip(
        id="clip_stale_past_window",
        clip_candidate_id="cand_pub_past",
        rendered_path=str(dummy_video),
        status=ClipStatus.PUBLISHING,
        scheduled_publish_at=datetime.utcnow() - timedelta(minutes=30),
        updated_at=datetime.utcnow() - timedelta(minutes=20),
    )
    db_session.add(stale_clip)
    db_session.add(stale_clip_past)
    db_session.commit()

    # Run check_and_publish_due_clips:
    # clip within window must remain PUBLISHING, clip past 15 mins must become FAILED_NEEDS_REVIEW
    check_and_publish_due_clips(db_session)
    db_session.refresh(stale_clip)
    db_session.refresh(stale_clip_past)
    assert stale_clip.status == ClipStatus.PUBLISHING
    assert stale_clip_past.status == ClipStatus.FAILED_NEEDS_REVIEW

    # 4. Create an approved due clip
    candidate2 = ClipCandidate(
        id="cand_pub_2",
        source_video_id="sv_pub",
        start_ms=35000,
        end_ms=60000,
        score=0.95,
        reason="Another segment",
        hook_text="Unlisted upload test",
        selected=True,
    )
    db_session.add(candidate2)
    db_session.commit()

    due_clip = Clip(
        id="clip_due_upload",
        clip_candidate_id=candidate2.id,
        rendered_path=str(dummy_video),
        status=ClipStatus.APPROVED,
        scheduled_publish_at=datetime.utcnow() - timedelta(minutes=1),
    )
    db_session.add(due_clip)
    db_session.commit()

    # Mock YouTube client and upload
    mock_youtube = MagicMock()
    # Mock uploads playlist response containing our clip token
    mock_playlist_items = MagicMock()
    mock_playlist_items.list.return_value.execute.return_value = {
        "items": [
            {
                "snippet": {
                    "description": f"Some description with token [clip:{due_clip.id}]",
                    "resourceId": {"videoId": "yt_recovered_123"},
                }
            }
        ]
    }
    mock_youtube.playlistItems.return_value = mock_playlist_items

    with patch("shorts_engine.services.publishing.client._build_youtube_client", return_value=mock_youtube):
        # We test with publisher checking playlist
        from shorts_engine.services.publishing.client import YouTubePublisher
        pub = YouTubePublisher(db_session=db_session, quota_tracker=tracker)
        token_payload = encrypt_secret("test_token")
        # Should detect that it was already uploaded to YouTube
        res = pub.publish_clip(due_clip, build_clip_metadata(due_clip), dry_run=False, encrypted_oauth_token=token_payload)

        assert res.get("youtube_video_id") == "yt_recovered_123"
        assert due_clip.status == ClipStatus.PUBLISHED

    # 5. Now test a normal upload to verify 1,600 quota decrement
    candidate3 = ClipCandidate(
        id="cand_pub_3",
        source_video_id="sv_pub",
        start_ms=70000,
        end_ms=90000,
        score=0.92,
        reason="Fresh segment",
        hook_text="Fresh upload",
        selected=True,
    )
    db_session.add(candidate3)
    fresh_clip = Clip(
        id="clip_fresh_upload",
        clip_candidate_id=candidate3.id,
        rendered_path=str(dummy_video),
        status=ClipStatus.APPROVED,
        scheduled_publish_at=datetime.utcnow() - timedelta(minutes=1),
    )
    db_session.add(fresh_clip)
    db_session.commit()

    # Mock playlist items returns empty (not yet uploaded)
    mock_playlist_empty = MagicMock()
    mock_playlist_empty.list.return_value.execute.return_value = {"items": []}
    mock_youtube.playlistItems.return_value = mock_playlist_empty

    with patch("shorts_engine.services.publishing.client._build_youtube_client", return_value=mock_youtube):
        with patch("shorts_engine.services.publishing.client._upload_video", return_value="yt_fresh_789"):
            pub = YouTubePublisher(db_session=db_session, quota_tracker=tracker)
            res2 = pub.publish_clip(fresh_clip, build_clip_metadata(fresh_clip), dry_run=False, encrypted_oauth_token=token_payload)
            assert res2.get("youtube_video_id") == "yt_fresh_789"
            assert fresh_clip.status == ClipStatus.PUBLISHED

    # Quota must be decremented / consumed by UPLOAD_COST_UNITS (1,600)
    assert tracker.get_used_units() == initial_used + UPLOAD_COST_UNITS


def test_dashboard_queue_needs_review_filter(db_session):
    """Dashboard /queue?tab=needs_review queries clips in FAILED_NEEDS_REVIEW state."""
    from starlette.requests import Request
    from shorts_engine.dashboard.routes.queue import queue_list
    from shorts_engine.db.models import User, UserRole

    # Create dummy user
    user = User(id="u1", username="admin", role=UserRole.ADMIN, password_hash="x", is_active=True)

    # Add a clip in FAILED_NEEDS_REVIEW
    clip = Clip(
        id="clip_ambig_queue",
        clip_candidate_id="cand_pub",
        rendered_path="test.mp4",
        status=ClipStatus.FAILED_NEEDS_REVIEW,
    )
    db_session.add(clip)
    db_session.commit()

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/queue",
        "query_string": b"tab=needs_review",
        "headers": [],
        "session": {"user": {"username": "admin", "role": "admin"}},
    }
    req = Request(scope)

    resp = queue_list(request=req, tab="needs_review", db=db_session, current_user=user)
    assert resp.status_code == 200
    assert "Needs Review" in resp.body.decode("utf-8")
    assert "clip_ambig_queue" in resp.body.decode("utf-8")

