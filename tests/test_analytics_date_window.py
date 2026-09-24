from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
import pytest
from shorts_engine.services.ab_testing.analytics_ingestion import AnalyticsIngestionService


def test_analytics_window_is_dynamic():
    """Verify that AnalyticsIngestionService does not use static 2026 calendar strings and computes dynamic windows."""
    mock_analytics = MagicMock()
    mock_reports = MagicMock()
    mock_query = MagicMock()
    mock_query.execute.return_value = {"rows": [["test_id", 85.5, 42.0]]}
    mock_reports.query.return_value = mock_query
    mock_analytics.reports.return_value = mock_reports

    service = AnalyticsIngestionService(youtube_analytics_client=mock_analytics)

    # 1. Test with explicit published_at in the past
    past_publish = datetime(2025, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
    res = service._fetch_tier2("test_id", published_at=past_publish)

    assert res["available"] is True
    assert mock_reports.query.called
    kwargs = mock_reports.query.call_args[1]

    expected_today = datetime.now(timezone.utc).date().strftime("%Y-%m-%d")
    assert kwargs["startDate"] == "2025-05-15"
    assert kwargs["endDate"] == expected_today
    assert kwargs["startDate"] != "2026-01-01"

    # 2. Test fallback when no published_at is provided
    mock_reports.reset_mock()
    mock_reports.query.return_value = mock_query
    service._fetch_tier2("test_id_no_date")

    kwargs_fallback = mock_reports.query.call_args[1]
    expected_fallback_start = (datetime.now(timezone.utc) - timedelta(days=30)).date().strftime("%Y-%m-%d")
    assert kwargs_fallback["startDate"] == expected_fallback_start
    assert kwargs_fallback["endDate"] == expected_today
    assert kwargs_fallback["startDate"] != "2026-01-01"


def test_analytics_window_with_clip_object():
    """Verify that clip.publish_log.published_at is parsed correctly."""
    mock_analytics = MagicMock()
    mock_reports = MagicMock()
    mock_query = MagicMock()
    mock_query.execute.return_value = {"rows": []}
    mock_reports.query.return_value = mock_query
    mock_analytics.reports.return_value = mock_reports

    service = AnalyticsIngestionService(youtube_analytics_client=mock_analytics)

    # Create dummy clip structure
    class DummyPublishLog:
        published_at = datetime(2024, 11, 20, 8, 30, 0)  # naive datetime

    class DummyClip:
        publish_log = DummyPublishLog()

    service._fetch_tier2("clip_vid_id", clip=DummyClip())
    kwargs = mock_reports.query.call_args[1]

    assert kwargs["startDate"] == "2024-11-20"
    assert kwargs["endDate"] == datetime.now(timezone.utc).date().strftime("%Y-%m-%d")
