import logging
import pytest
from unittest.mock import MagicMock
from shorts_engine.services.monitoring.analytics_sync import YouTubeAnalyticsSync
from shorts_engine.db.models import Clip, ClipStatus, PublishLog, ClipCandidate, SourceVideo

def test_analytics_sync_uses_published_clip_id():
    """Verify that analytics sync uses clip.publish_log.youtube_video_id when present."""
    db = MagicMock()
    clip = MagicMock(spec=Clip)
    clip.id = "clip-1"
    clip.status = ClipStatus.PUBLISHED
    clip.publish_log = MagicMock(spec=PublishLog)
    clip.publish_log.youtube_video_id = "real_clip_yt_id"
    clip.candidate = MagicMock(spec=ClipCandidate)
    clip.candidate.source_video = MagicMock(spec=SourceVideo)
    clip.candidate.source_video.youtube_video_id = "parent_video_id"

    db.query.return_value.filter.return_value.all.return_value = [clip]

    syncer = YouTubeAnalyticsSync(db)
    result = syncer.sync_all_published_clips()

    assert result["synced_count"] == 1
    assert clip.analytics is not None
    assert clip.analytics.get("is_simulated") is True
    db.commit.assert_called_once()

def test_analytics_sync_never_falls_back_to_source_video_id(caplog):
    """
    Verify that analytics sync never falls back to source_video.youtube_video_id.
    If clip.status == ClipStatus.PUBLISHED but publish_log has no ID, it must warn and skip.
    """
    db = MagicMock()
    clip = MagicMock(spec=Clip)
    clip.id = "clip-corrupted"
    clip.status = ClipStatus.PUBLISHED
    clip.publish_log = None
    clip.candidate = MagicMock(spec=ClipCandidate)
    clip.candidate.source_video = MagicMock(spec=SourceVideo)
    clip.candidate.source_video.youtube_video_id = "parent_video_id"
    clip.analytics = None

    db.query.return_value.filter.return_value.all.return_value = [clip]

    syncer = YouTubeAnalyticsSync(db)
    with caplog.at_level(logging.WARNING):
        result = syncer.sync_all_published_clips()

    assert result["synced_count"] == 0
    # clip.analytics must NOT have been updated
    assert clip.analytics is None
    # Must log a warning for published clip with missing ID
    assert any("Published clip clip-corrupted has no YouTube video ID" in msg for msg in caplog.messages)

def test_analytics_sync_skips_unpublished_clip_without_warning(caplog):
    """
    Verify that an unpublished clip with no publish_log ID is skipped quietly without warning.
    """
    db = MagicMock()
    clip = MagicMock(spec=Clip)
    clip.id = "clip-pending"
    clip.status = ClipStatus.WAITING_APPROVAL
    clip.publish_log = None
    clip.candidate = MagicMock(spec=ClipCandidate)
    clip.candidate.source_video = MagicMock(spec=SourceVideo)
    clip.candidate.source_video.youtube_video_id = "parent_video_id"
    clip.analytics = None

    db.query.return_value.filter.return_value.all.return_value = [clip]

    syncer = YouTubeAnalyticsSync(db)
    with caplog.at_level(logging.WARNING):
        result = syncer.sync_all_published_clips()

    assert result["synced_count"] == 0
    assert clip.analytics is None
    # Must NOT log any warnings
    assert not any("has no YouTube video ID" in msg for msg in caplog.messages)
