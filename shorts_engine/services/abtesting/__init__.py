"""
Alias package for A/B testing service.

This exists only to resolve the legacy import name
`shorts_engine.services.abtesting` used by some tests. The real
implementation lives in `shorts_engine.services.ab_testing`.
"""

from shorts_engine.services.ab_testing import (
    ABTestService,
    ABTestError,
    wilson_lower_bound,
    wilson_upper_bound,
)

__all__ = [
    "ABTestService",
    "ABTestError",
    "wilson_lower_bound",
    "wilson_upper_bound",
]
