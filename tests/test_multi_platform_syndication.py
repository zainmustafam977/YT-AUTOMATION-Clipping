"""
Tests for Component 2: Multi-Platform Resilient Syndication Engine
(YouTube Shorts, TikTok Content Posting API v2, Instagram Reels Graph API)
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.db.models import Clip, ClipStatus, ClipCandidate, SourceVideo, Channel, PlatformPublishLog
from shorts_engine.services.publishing.base import BasePlatformPublisher, PlatformPublishResult
from shorts_engine.services.publishing.tiktok import TikTokPublisher
from shorts_engine.services.publishing.instagram import InstagramReelsPublisher
from shorts_engine.services.publishing.manager import MultiPlatformSyndicator, YouTubePlatformAdapter


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


def test_platform_publish_result_serialization():
    """Verifies standard envelope formatting across platforms."""
    res = PlatformPublishResult(
        platform="tiktok",
        success=True,
        post_id="tt_12345",
        permalink="https://tiktok.com/@video/12345",
        metadata={"chunks": 2},
        dry_run=True,
    )
    d = res.to_dict()
    assert d["platform"] == "tiktok"
    assert d["success"] is True
    assert d["post_id"] == "tt_12345"
    assert d["dry_run"] is True
    assert d["metadata"]["chunks"] == 2


def test_tiktok_publisher_health_check_missing_token():
    """Verifies graceful health failure when TikTok token is missing."""
    pub = TikTokPublisher(access_token="")
    healthy, msg = pub.health_check()
    assert healthy is False
    assert "not configured" in msg


def test_tiktok_publisher_dry_run_simulation(tmp_path):
    """Verifies safe dry-run upload simulation for TikTok."""
    video = tmp_path / "test_vertical.mp4"
    video.write_bytes(b"dummy_video_stream" * 100)

    pub = TikTokPublisher(access_token="fake_token")
    res = pub.publish(
        video_path=video,
        title="Mindblowing Secret",
        description="Must watch!",
        tags=["viral", "shorts"],
        dry_run=True,
    )

    assert res.success is True
    assert res.dry_run is True
    assert res.platform == "tiktok"
    assert res.post_id.startswith("tt_sim_")
    assert "tiktok.com" in res.permalink
    assert res.metadata["simulated"] is True


def test_tiktok_publisher_live_chunked_upload_workflow(tmp_path):
    """Verifies TikTok live upload: creator info, init chunked upload, PUT chunks, and status polling."""
    video = tmp_path / "test_vertical.mp4"
    video.write_bytes(b"chunk_data_" * 1000)

    pub = TikTokPublisher(access_token="valid_test_token")

    mock_creator_resp = MagicMock()
    mock_creator_resp.status_code = 200
    mock_creator_resp.json.return_value = {
        "error": {"code": "ok"},
        "data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]},
    }

    mock_init_resp = MagicMock()
    mock_init_resp.status_code = 200
    mock_init_resp.json.return_value = {
        "error": {"code": "ok"},
        "data": {
            "publish_id": "v_pub_789",
            "upload_url": "https://upload.tiktok.com/resumable/test",
        },
    }

    mock_chunk_put = MagicMock()
    mock_chunk_put.status_code = 200

    mock_status_resp = MagicMock()
    mock_status_resp.status_code = 200
    mock_status_resp.json.return_value = {
        "error": {"code": "ok"},
        "data": {
            "status": "PUBLISH_COMPLETE",
            "publicaly_available_post_id": ["7123456789012345678"],
        },
    }

    with patch("httpx.Client.post") as mock_post, patch("httpx.Client.put") as mock_put:
        def post_router(url, **kwargs):
            if "creator_info" in url:
                return mock_creator_resp
            elif "video/init" in url:
                return mock_init_resp
            elif "status/fetch" in url:
                return mock_status_resp
            raise ValueError(f"Unexpected URL: {url}")

        mock_post.side_effect = post_router
        mock_put.return_value = mock_chunk_put

        res = pub.publish(
            video_path=video,
            title="Live Test Video",
            description="Live upload",
            tags=["live"],
            dry_run=False,
        )

        assert res.success is True
        assert res.platform == "tiktok"
        assert res.post_id == "7123456789012345678"
        assert "7123456789012345678" in res.permalink
        assert res.metadata["publish_id"] == "v_pub_789"


def test_instagram_publisher_health_check_missing_creds():
    """Verifies graceful health failure when Instagram tokens are missing."""
    pub = InstagramReelsPublisher(access_token="", ig_user_id="")
    healthy, msg = pub.health_check()
    assert healthy is False
    assert "missing" in msg


def test_instagram_publisher_dry_run_simulation(tmp_path):
    """Verifies safe dry-run upload simulation for Instagram Reels."""
    video = tmp_path / "test_reel.mp4"
    video.write_bytes(b"dummy_reel_stream" * 100)

    pub = InstagramReelsPublisher(access_token="fake_token", ig_user_id="fake_ig_id")
    res = pub.publish(
        video_path=video,
        title="Epic Reel",
        description="Double tap if you agree!",
        tags=["reels", "trending"],
        dry_run=True,
    )

    assert res.success is True
    assert res.dry_run is True
    assert res.platform == "instagram"
    assert res.post_id.startswith("ig_sim_")
    assert "instagram.com/reel" in res.permalink
    assert res.metadata["simulated"] is True


def test_instagram_publisher_live_container_workflow(tmp_path):
    """Verifies Instagram Reels state machine: session init, binary upload, polling FINISHED, and media publish."""
    video = tmp_path / "test_reel.mp4"
    video.write_bytes(b"dummy_reel_stream" * 100)

    pub = InstagramReelsPublisher(access_token="valid_meta_token", ig_user_id="17841400000000000")

    mock_init_resp = MagicMock()
    mock_init_resp.status_code = 200
    mock_init_resp.json.return_value = {
        "id": "container_999",
        "uri": "https://rupload.facebook.com/ig-reels/test_upload",
    }

    mock_binary_resp = MagicMock()
    mock_binary_resp.status_code = 200

    mock_poll_resp = MagicMock()
    mock_poll_resp.status_code = 200
    mock_poll_resp.json.return_value = {"status_code": "FINISHED", "id": "container_999"}

    mock_pub_resp = MagicMock()
    mock_pub_resp.status_code = 200
    mock_pub_resp.json.return_value = {"id": "media_reels_555"}

    mock_perm_resp = MagicMock()
    mock_perm_resp.status_code = 200
    mock_perm_resp.json.return_value = {"permalink": "https://www.instagram.com/reel/C_xyz123/"}

    with patch("httpx.Client.post") as mock_post, patch("httpx.Client.get") as mock_get:
        def post_router(url, **kwargs):
            if "media_publish" in url:
                return mock_pub_resp
            elif "/media" in url:
                return mock_init_resp
            elif "rupload.facebook.com" in url:
                return mock_binary_resp
            raise ValueError(f"Unexpected POST URL: {url}")

        def get_router(url, **kwargs):
            if "container_999" in url:
                return mock_poll_resp
            elif "media_reels_555" in url:
                return mock_perm_resp
            raise ValueError(f"Unexpected GET URL: {url}")

        mock_post.side_effect = post_router
        mock_get.side_effect = get_router

        res = pub.publish(
            video_path=video,
            title="Live Instagram Test",
            description="Reels description",
            tags=["meta"],
            dry_run=False,
        )

        assert res.success is True
        assert res.platform == "instagram"
        assert res.post_id == "555" or "media_reels_555" in res.post_id
        assert "instagram.com/reel" in res.permalink
        assert res.metadata["container_id"] == "container_999"


def test_multi_platform_syndicator_parallel_dispatch(db_session, tmp_path):
    """Verifies MultiPlatformSyndicator executes parallel dispatch and logs to PlatformPublishLog."""
    # Setup database fixture
    ch = Channel(name="Syndicate Channel", youtube_channel_id="UC_syndicate")
    db_session.add(ch)
    db_session.commit()

    src = SourceVideo(
        youtube_video_id="vid_syndicate_1",
        title="Syndication Source Video",
        channel_id=ch.id,
    )
    db_session.add(src)
    db_session.commit()

    cand = ClipCandidate(
        source_video_id=src.id,
        start_ms=0,
        end_ms=20000,
        hook_text="Watch this crazy event unfold!",
        reason="High virality",
        score=9.2,
    )
    db_session.add(cand)
    db_session.commit()

    video_file = tmp_path / "rendered_syndicate.mp4"
    video_file.write_bytes(b"rendered_short_stream" * 100)

    clip = Clip(
        clip_candidate_id=cand.id,
        rendered_path=str(video_file),
        status=ClipStatus.APPROVED,
    )
    db_session.add(clip)
    db_session.commit()

    syndicator = MultiPlatformSyndicator(db_session)
    results = syndicator.syndicate_clip(
        clip_id=clip.id,
        platforms=["youtube", "tiktok", "instagram"],
        dry_run=True,
    )

    # Verify all 3 platforms returned results
    assert "youtube" in results
    assert "tiktok" in results
    assert "instagram" in results

    assert results["youtube"].success is True
    assert results["tiktok"].success is True
    assert results["instagram"].success is True

    # Verify atomic update to Clip status
    db_session.refresh(clip)
    assert clip.status == ClipStatus.PUBLISHED

    # Verify database audit logging in platform_publish_logs
    logs = db_session.query(PlatformPublishLog).filter_by(clip_id=clip.id).all()
    assert len(logs) == 3
    platforms_logged = {l.platform for l in logs}
    assert platforms_logged == {"youtube", "tiktok", "instagram"}
    for log in logs:
        assert log.status == "DRY_RUN"
        assert log.platform_post_id is not None
        assert log.permalink is not None
