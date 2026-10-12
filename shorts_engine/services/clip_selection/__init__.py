"""
Clip selection package.
"""

from shorts_engine.services.clip_selection.selector import ClipSelectionService, SelectedClip
from shorts_engine.services.clip_selection.boundaries import BoundarySnapper
from shorts_engine.services.clip_selection.estimator import estimate_optimal_clip_count
from shorts_engine.services.clip_selection.validator import (
    ClipCandidateValidator,
    CandidateValidationResult,
)

__all__ = [
    "ClipSelectionService",
    "SelectedClip",
    "BoundarySnapper",
    "estimate_optimal_clip_count",
    "ClipCandidateValidator",
    "CandidateValidationResult",
]
