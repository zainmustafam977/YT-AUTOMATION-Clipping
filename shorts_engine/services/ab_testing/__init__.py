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

# Legacy API re-export: some tests import
# `shorts_engine.services.abtesting` (no underscore). Keep that surface
# available via the canonical package so the alias can forward to it.
from shorts_engine.services.ab_testing.abtesting_api import (
    ABTestService,
    ABTestError,
    wilson_lower_bound,
    wilson_upper_bound,
)
