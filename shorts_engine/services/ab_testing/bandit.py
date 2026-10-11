"""
Bayesian Multi-Armed Bandit A/B Testing & Heuristic Title Optimization.

NOTE ON SHORTS ALGORITHM DYNAMICS:
Title Click-Through Rate (CTR) is a relatively weak signal for YouTube Shorts
because discovery is predominantly feed-swipe based rather than search click decisions.
Consequently, expectations regarding title A/B performance should be modest.
Decision rules are implemented as quota-safe empirical heuristics rather than formal
statistical hypothesis tests to protect against false positives and avoid excessive
YouTube Data API quota consumption (50 units per videos.update call).

Decision Heuristic & Quota Guardrails:
- Config-driven threshold gates:
  1. AB_MIN_IMPRESSIONS (default 1000): variants must accrue adequate trials.
  2. AB_MIN_TEST_DURATION_HOURS (default 72.0): observational window before decisions.
  3. AB_MAX_TITLE_SWAPS (default 1): prevents algorithmic thrashing & quota burn.
  4. AB_RELATIVE_MARGIN_THRESHOLD (default 0.10): challenger CTR must beat active CTR by >= 10%.
  5. AB_ENABLE_TITLE_SWAPPING (default True): master kill-switch to halt all title swaps.
  6. Circuit breaker: emergency rollback to baseline if view velocity collapses by > 50%.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple

import numpy as np

from shorts_engine.config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class VariantEvaluationResult:
    action: str  # 'PROMOTE', 'ROLLBACK', 'MAINTAIN', 'INSUFFICIENT_DATA'
    winning_variant_index: Optional[int] = None
    winning_title: Optional[str] = None
    confidence: float = 0.0
    baseline_velocity: float = 0.0
    current_velocity: float = 0.0
    circuit_breaker_triggered: bool = False
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "winning_variant_index": self.winning_variant_index,
            "winning_title": self.winning_title,
            "confidence": round(self.confidence, 4),
            "baseline_velocity": round(self.baseline_velocity, 2),
            "current_velocity": round(self.current_velocity, 2),
            "circuit_breaker_triggered": self.circuit_breaker_triggered,
            "details": self.details,
        }


class BayesianVariantOptimizer:
    """
    Mathematical and heuristic optimizer for A/B/n headline and title testing on YouTube Shorts.
    Uses Beta distributions for CTR belief modeling and quota-safe empirical heuristic gates.
    """

    def __init__(
        self,
        prior_alpha: float = 1.0,
        prior_beta: float = 19.0,
        confidence_threshold: Optional[float] = None,
        min_impressions: Optional[int] = None,
        min_test_duration_hours: Optional[float] = None,
        max_title_swaps: Optional[int] = None,
        relative_margin_threshold: Optional[float] = None,
        rollback_velocity_drop: Optional[float] = None,
        enable_title_swapping: Optional[bool] = None,
    ):
        # Prior Beta(1, 19) expresses a weak prior belief centered around 5% CTR (1 / (1 + 19))
        self.prior_alpha = prior_alpha
        self.prior_beta = prior_beta
        self.confidence_threshold = (
            confidence_threshold
            if confidence_threshold is not None
            else getattr(settings, "AB_CONFIDENCE_THRESHOLD", 0.95)
        )
        self.min_impressions = (
            min_impressions
            if min_impressions is not None
            else getattr(settings, "AB_MIN_IMPRESSIONS", 1000)
        )
        self.min_test_duration_hours = (
            min_test_duration_hours
            if min_test_duration_hours is not None
            else getattr(settings, "AB_MIN_TEST_DURATION_HOURS", 72.0)
        )
        self.max_title_swaps = (
            max_title_swaps
            if max_title_swaps is not None
            else getattr(settings, "AB_MAX_TITLE_SWAPS", 1)
        )
        self.relative_margin_threshold = (
            relative_margin_threshold
            if relative_margin_threshold is not None
            else getattr(settings, "AB_RELATIVE_MARGIN_THRESHOLD", 0.10)
        )
        self.rollback_velocity_drop = (
            rollback_velocity_drop
            if rollback_velocity_drop is not None
            else getattr(settings, "AB_ROLLBACK_VELOCITY_DROP", 0.50)
        )
        self.enable_title_swapping = (
            enable_title_swapping
            if enable_title_swapping is not None
            else getattr(settings, "AB_ENABLE_TITLE_SWAPPING", True)
        )

    def calculate_posterior_parameters(self, clicks: int, impressions: int) -> Tuple[float, float]:
        """
        Calculates posterior Beta distribution parameters (alpha, beta)
        given observed clicks (successes) and impressions (trials).
        """
        impressions = max(0, impressions)
        clicks = max(0, min(clicks, impressions))
        alpha = self.prior_alpha + clicks
        beta = self.prior_beta + (impressions - clicks)
        return alpha, beta

    def probability_b_beats_a(
        self, alpha_a: float, beta_a: float, alpha_b: float, beta_b: float, n_samples: int = 15000
    ) -> float:
        """
        Computes P(theta_B > theta_A) using Monte Carlo sampling from conjugate Beta posteriors.
        Returns the posterior probability that variant B has a higher true CTR than variant A.
        """
        samples_a = np.random.beta(alpha_a, beta_a, size=n_samples)
        samples_b = np.random.beta(alpha_b, beta_b, size=n_samples)
        return float(np.mean(samples_b > samples_a))

    def calculate_view_velocity(self, views_delta: int, time_delta_hours: float) -> float:
        """Computes view velocity in views per hour."""
        if time_delta_hours <= 0:
            return 0.0
        return max(0.0, float(views_delta) / float(time_delta_hours))

    def check_circuit_breaker(self, current_velocity: float, baseline_velocity: float) -> bool:
        """
        Returns True if current view velocity has collapsed by more than
        the configured rollback threshold relative to the baseline velocity.
        """
        if baseline_velocity <= 0 or current_velocity >= baseline_velocity:
            return False
        velocity_drop = (baseline_velocity - current_velocity) / baseline_velocity
        return velocity_drop >= self.rollback_velocity_drop

    def evaluate_variations(
        self,
        variations: List[Dict[str, Any]],
        current_metrics: Dict[str, Any],
        baseline_velocity: float = 0.0,
        current_velocity: float = 0.0,
        test_duration_hours: Optional[float] = None,
        title_swap_count: int = 0,
    ) -> VariantEvaluationResult:
        """
        Evaluates active A/B variations against the active variant using quota-safe heuristic rules.

        Args:
            variations: List of variation dicts, each with keys like:
                'title', 'impressions', 'clicks', 'is_active', 'is_baseline', 'promoted_at'
            current_metrics: Overall YouTube performance dict (views, likes, etc.)
            baseline_velocity: Measured view velocity before the most recent switch
            current_velocity: Measured view velocity during the current window
            test_duration_hours: Elapsed hours since video publication or last title promotion
            title_swap_count: Number of title swaps already performed on this video

        Returns:
            VariantEvaluationResult specifying whether to PROMOTE, ROLLBACK, or MAINTAIN.
        """
        if not variations:
            return VariantEvaluationResult(
                action="INSUFFICIENT_DATA",
                details={"reason": "No variations provided."},
            )

        # Find currently active variation and original baseline
        active_idx = 0
        baseline_idx = 0
        for idx, var in enumerate(variations):
            if var.get("is_active"):
                active_idx = idx
            if var.get("is_baseline"):
                baseline_idx = idx

        active_variant = variations[active_idx]
        baseline_variant = variations[baseline_idx]

        # 1. Emergency Circuit Breaker Check
        # If the active variant is a previously promoted variant (not the original baseline)
        # and velocity collapsed by > 50%, immediately trigger emergency rollback!
        if active_idx != baseline_idx and baseline_velocity > 10.0:
            if self.check_circuit_breaker(current_velocity, baseline_velocity):
                logger.warning(
                    "CIRCUIT BREAKER TRIGGERED: View velocity collapsed from %.1f to %.1f v/hr (drop >= %.0f%%). Reverting to baseline.",
                    baseline_velocity, current_velocity, self.rollback_velocity_drop * 100
                )
                return VariantEvaluationResult(
                    action="ROLLBACK",
                    winning_variant_index=baseline_idx,
                    winning_title=baseline_variant.get("title"),
                    baseline_velocity=baseline_velocity,
                    current_velocity=current_velocity,
                    circuit_breaker_triggered=True,
                    details={"reason": "Emergency circuit breaker triggered due to post-promotion velocity collapse."},
                )

        # 2. Master Kill-Switch Check
        if not self.enable_title_swapping:
            logger.info("Title swapping is DISABLED via kill switch (AB_ENABLE_TITLE_SWAPPING=False).")
            return VariantEvaluationResult(
                action="MAINTAIN",
                winning_variant_index=active_idx,
                winning_title=active_variant.get("title"),
                baseline_velocity=baseline_velocity,
                current_velocity=current_velocity,
                details={
                    "status": "Title swapping is disabled via kill-switch (AB_ENABLE_TITLE_SWAPPING=False).",
                    "reason": "Kill switch enabled",
                    "enable_title_swapping": False,
                },
            )

        # 3. Maximum Title Swaps Gate (Quota & Algorithm Thrashing Safety)
        if title_swap_count >= self.max_title_swaps:
            logger.info(
                "Title swap REFUSED: swap count (%d) >= max allowed swaps (%d). Maintaining active title.",
                title_swap_count, self.max_title_swaps,
            )
            return VariantEvaluationResult(
                action="MAINTAIN",
                winning_variant_index=active_idx,
                winning_title=active_variant.get("title"),
                baseline_velocity=baseline_velocity,
                current_velocity=current_velocity,
                details={
                    "status": f"Maximum title swaps reached ({title_swap_count}/{self.max_title_swaps}).",
                    "reason": "Max swaps reached",
                    "title_swap_count": title_swap_count,
                    "max_title_swaps": self.max_title_swaps,
                },
            )

        # 4. Minimum Test Duration Gate
        if test_duration_hours is not None and test_duration_hours < self.min_test_duration_hours:
            logger.info(
                "Title swap REFUSED: test duration (%.1fh) < minimum required (%.1fh). Maintaining active title.",
                test_duration_hours, self.min_test_duration_hours,
            )
            return VariantEvaluationResult(
                action="MAINTAIN",
                winning_variant_index=active_idx,
                winning_title=active_variant.get("title"),
                baseline_velocity=baseline_velocity,
                current_velocity=current_velocity,
                details={
                    "status": f"Test duration ({test_duration_hours:.1f}h) below threshold ({self.min_test_duration_hours:.1f}h).",
                    "reason": "Minimum test duration not met",
                    "test_duration_hours": round(test_duration_hours, 2),
                    "min_test_duration_hours": self.min_test_duration_hours,
                },
            )

        # 5. Active Variant Impression Gate
        active_impressions = int(active_variant.get("impressions", 0))
        active_clicks = int(active_variant.get("clicks", 0))
        active_ctr = (active_clicks / active_impressions) if active_impressions > 0 else 0.0

        if active_impressions < self.min_impressions:
            logger.info(
                "Active variant impressions (%d) < minimum threshold (%d). Maintaining active title.",
                active_impressions, self.min_impressions,
            )
            return VariantEvaluationResult(
                action="MAINTAIN",
                winning_variant_index=active_idx,
                winning_title=active_variant.get("title"),
                baseline_velocity=baseline_velocity,
                current_velocity=current_velocity,
                details={
                    "status": f"Active variant impressions ({active_impressions}) below threshold ({self.min_impressions}).",
                    "reason": "Insufficient active impressions",
                    "active_impressions": active_impressions,
                    "min_impressions": self.min_impressions,
                },
            )

        # 6. Evaluate Candidate Variants against Active Variant
        alpha_act, beta_act = self.calculate_posterior_parameters(active_clicks, active_impressions)

        best_challenger_idx: Optional[int] = None
        best_challenger_margin: float = 0.0
        best_challenger_prob: float = 0.0
        best_challenger_title: Optional[str] = None
        best_challenger_ctr: float = 0.0

        for idx, var in enumerate(variations):
            if idx == active_idx:
                continue

            c_imp = int(var.get("impressions", 0))
            c_clicks = int(var.get("clicks", 0))
            c_ctr = (c_clicks / c_imp) if c_imp > 0 else 0.0

            # Minimum sample size gate for challenger
            if c_imp < self.min_impressions:
                logger.info(
                    "Challenger variant #%d ('%s') impressions (%d) < threshold (%d). Skipping.",
                    idx, var.get("title"), c_imp, self.min_impressions,
                )
                continue

            # Calculate relative CTR margin
            if active_ctr > 0:
                rel_margin = (c_ctr - active_ctr) / active_ctr
            else:
                rel_margin = 1.0 if c_ctr > 0 else 0.0

            # Bayesian posterior probability P(Challenger > Active)
            alpha_c, beta_c = self.calculate_posterior_parameters(clicks=c_clicks, impressions=c_imp)
            prob_beats_act = self.probability_b_beats_a(alpha_act, beta_act, alpha_c, beta_c)

            logger.info(
                "Variant evaluation: candidate #%d ('%s') ctr=%.4f (%d/%d) vs active #%d ('%s') ctr=%.4f (%d/%d) | "
                "rel_margin=%.2f%% (threshold=%.2f%%) | P(B>A)=%.2f%%.",
                idx, var.get("title"), c_ctr, c_clicks, c_imp,
                active_idx, active_variant.get("title"), active_ctr, active_clicks, active_impressions,
                rel_margin * 100, self.relative_margin_threshold * 100,
                prob_beats_act * 100,
            )

            # Empirical heuristic rule: relative margin must meet or exceed threshold
            if rel_margin >= self.relative_margin_threshold and c_ctr > active_ctr:
                if rel_margin > best_challenger_margin:
                    best_challenger_margin = rel_margin
                    best_challenger_idx = idx
                    best_challenger_title = var.get("title")
                    best_challenger_prob = prob_beats_act
                    best_challenger_ctr = c_ctr

        # 7. Final Heuristic Decision
        if best_challenger_idx is not None:
            logger.info(
                "HEURISTIC A/B WINNER FOUND: Variant #%d ('%s') wins! "
                "Challenger CTR=%.4f vs Active CTR=%.4f (+%.2f%% rel_margin >= +%.2f%% threshold).",
                best_challenger_idx, best_challenger_title,
                best_challenger_ctr, active_ctr,
                best_challenger_margin * 100, self.relative_margin_threshold * 100,
            )
            return VariantEvaluationResult(
                action="PROMOTE",
                winning_variant_index=best_challenger_idx,
                winning_title=best_challenger_title,
                confidence=best_challenger_prob,
                baseline_velocity=baseline_velocity,
                current_velocity=current_velocity,
                details={
                    "winner_index": best_challenger_idx,
                    "active_ctr": round(active_ctr, 4),
                    "challenger_ctr": round(best_challenger_ctr, 4),
                    "relative_margin": round(best_challenger_margin, 4),
                    "relative_margin_threshold": self.relative_margin_threshold,
                    "active_impressions": active_impressions,
                    "challenger_impressions": variations[best_challenger_idx].get("impressions", 0),
                    "test_duration_hours": round(test_duration_hours, 2) if test_duration_hours is not None else None,
                    "min_test_duration_hours": self.min_test_duration_hours,
                    "title_swap_count": title_swap_count,
                    "max_title_swaps": self.max_title_swaps,
                    "bayesian_confidence": round(best_challenger_prob, 4),
                },
            )

        logger.info(
            "A/B DECISION: MAINTAIN active variant '%s'. No challenger exceeded relative margin threshold (+%.2f%%).",
            active_variant.get("title"), self.relative_margin_threshold * 100,
        )
        return VariantEvaluationResult(
            action="MAINTAIN",
            winning_variant_index=active_idx,
            winning_title=active_variant.get("title"),
            confidence=0.0,
            baseline_velocity=baseline_velocity,
            current_velocity=current_velocity,
            details={
                "status": "No challenger variant exceeded the relative CTR margin threshold.",
                "reason": "Relative CTR margin threshold not reached",
                "active_ctr": round(active_ctr, 4),
                "active_impressions": active_impressions,
                "min_impressions": self.min_impressions,
                "relative_margin_threshold": self.relative_margin_threshold,
                "test_duration_hours": round(test_duration_hours, 2) if test_duration_hours is not None else None,
                "title_swap_count": title_swap_count,
            },
        )
