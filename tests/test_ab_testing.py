"""
Tests for Title A/B variant generation (4 archetypes, 100-char limits)
and Two-Tier Analytics Ingestion with graceful scope degradation.
"""

from unittest.mock import MagicMock
from shorts_engine.services.ab_testing.title_generator import (
    TitleGenerator,
    TitleVariantSet,
)
from shorts_engine.services.ab_testing.analytics_ingestion import (
    AnalyticsIngestionService,
    VideoPerformanceMetrics,
)


def test_title_generator_generates_all_four_archetypes_under_100_chars():
    """Verifies that TitleGenerator produces all 4 archetypes within 100 characters."""
    gen = TitleGenerator()
    variants = gen.generate_variants(
        topic="Python Performance Optimization in Production Systems",
        hook_text="Your code is running 10x slower than it should",
        key_point="Use vectorization instead of loops",
    )

    assert isinstance(variants, TitleVariantSet)
    assert len(variants.curiosity_gap) <= 100
    assert len(variants.question_hook) <= 100
    assert len(variants.bold_statement) <= 100
    assert len(variants.numbered_listicle) <= 100

    assert len(variants.curiosity_gap) > 0
    assert len(variants.question_hook) > 0
    assert len(variants.bold_statement) > 0
    assert len(variants.numbered_listicle) > 0
    assert variants.validate_lengths() is True


def test_title_generator_truncates_excessive_topic():
    """Verifies extreme topic length is handled safely without exceeding 100 chars."""
    gen = TitleGenerator()
    very_long_topic = "A" * 200
    variants = gen.generate_variants(topic=very_long_topic)
    assert variants.validate_lengths() is True


def test_two_tier_analytics_ingestion_both_tiers_present():
    """Verifies ingestion when both Data API v3 and Analytics API succeed."""
    # Mock Data API client
    mock_data = MagicMock()
    mock_data.videos().list().execute.return_value = {
        "items": [{
            "statistics": {
                "viewCount": "15000",
                "likeCount": "850",
                "commentCount": "42",
            }
        }]
    }

    # Mock Analytics API client
    mock_analytics = MagicMock()
    mock_analytics.reports().query().execute.return_value = {
        "rows": [["video_001", "78.5", "42.3"]]
    }

    service = AnalyticsIngestionService(
        youtube_data_client=mock_data,
        youtube_analytics_client=mock_analytics,
    )
    metrics = service.fetch_metrics("video_001")

    assert metrics.view_count == 15000
    assert metrics.like_count == 850
    assert metrics.comment_count == 42
    assert metrics.average_view_percentage == 78.5
    assert metrics.average_view_duration_sec == 42.3
    assert metrics.tier2_available is True
    assert metrics.engagement_rate > 0.0
    assert metrics.composite_score > 0.0


def test_two_tier_analytics_graceful_degradation_on_missing_analytics_scope(caplog):
    """
    Verifies that if Analytics API returns 403 / missing scope,
    Tier 1 metrics are preserved and Tier 2 gracefully degrades to None.
    """
    mock_data = MagicMock()
    mock_data.videos().list().execute.return_value = {
        "items": [{
            "statistics": {
                "viewCount": "5000",
                "likeCount": "200",
                "commentCount": "10",
            }
        }]
    }

    # Mock Analytics API client raising 403 Forbidden
    mock_analytics = MagicMock()
    mock_analytics.reports().query().execute.side_effect = Exception("403 Forbidden: Insufficient Permission")

    service = AnalyticsIngestionService(
        youtube_data_client=mock_data,
        youtube_analytics_client=mock_analytics,
    )

    metrics = service.fetch_metrics("video_scope_missing")

    # Tier 1 should be completely intact
    assert metrics.view_count == 5000
    assert metrics.like_count == 200
    assert metrics.comment_count == 10

    # Tier 2 safely degraded
    assert metrics.average_view_percentage is None
    assert metrics.tier2_available is False
    assert metrics.composite_score > 0.0


def test_rank_archetypes():
    """Verifies that rank_archetypes sorts variants by performance composite score."""
    service = AnalyticsIngestionService()
    metrics_map = {
        "curiosity_gap": VideoPerformanceMetrics(video_id="v1", view_count=20000, like_count=1200, average_view_percentage=85.0),
        "question_hook": VideoPerformanceMetrics(video_id="v2", view_count=5000, like_count=100, average_view_percentage=40.0),
        "bold_statement": VideoPerformanceMetrics(video_id="v3", view_count=15000, like_count=900, average_view_percentage=70.0),
    }

    ranked = service.rank_archetypes(metrics_map)
    assert len(ranked) == 3
    assert ranked[0]["archetype"] == "curiosity_gap"
    assert ranked[1]["archetype"] == "bold_statement"
    assert ranked[2]["archetype"] == "question_hook"
