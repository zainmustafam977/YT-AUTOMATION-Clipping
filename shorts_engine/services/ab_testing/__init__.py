"""
A/B Testing & Analytics Ingestion Module.
Generates 4 title archetypes and ingests Tier 1 & Tier 2 performance metrics.
"""

from shorts_engine.services.ab_testing.title_generator import TitleGenerator, TitleArchetype, TitleVariantSet
from shorts_engine.services.ab_testing.analytics_ingestion import AnalyticsIngestionService, VideoPerformanceMetrics

__all__ = [
    "TitleGenerator",
    "TitleArchetype",
    "TitleVariantSet",
    "AnalyticsIngestionService",
    "VideoPerformanceMetrics",
]
