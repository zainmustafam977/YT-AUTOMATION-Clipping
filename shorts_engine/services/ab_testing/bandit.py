"""
Bayesian Multi-Armed Bandit A/B Testing & Dynamic Title Optimization.
Leverages Beta-Binomial conjugate updating for posterior Click-Through Rate (CTR) modeling,
view velocity scoring, statistical significance gating (P(B > A) >= 0.95),
and an automatic circuit breaker rollback for velocity drops > 50%.
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
    Mathematical optimizer for A/B/n headline and title testing on YouTube Shorts.
    Uses Beta distributions for CTR belief modeling and Thompson Sampling principles.
    """

    def __init__(
        self,
        prior_alpha: float = 1.0,
        prior_beta: float = 19.0,
        confidence_threshold: Optional[float] = None,
        min_impressions: Optional[int] = None,
        rollback_velocity_drop: Optional[float] = None,
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
            else getattr(settings, "AB_MIN_IMPRESSIONS", 100)
        )
        self.rollback_velocity_drop = (
            rollback_velocity_drop
            if rollback_velocity_drop is not None
            else getattr(settings, "AB_ROLLBACK_VELOCITY_DROP", 0.50)
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
    ) -> VariantEvaluationResult:
        """
        Evaluates active A/B variations against the baseline variant.

        Args:
            variations: List of variation dicts, each with keys like:
                'title', 'impressions', 'clicks', 'is_active', 'is_baseline', 'promoted_at'
            current_metrics: Overall YouTube performance dict (views, likes, etc.)
            baseline_velocity: Measured view velocity before the most recent switch
            current_velocity: Measured view velocity during the current window

        Returns:
            VariantEvaluationResult specifying whether to PROMOTE, ROLLBACK, or MAINTAIN.
        """
        if not variations:
            return VariantEvaluationResult(action="INSUFFICIENT_DATA")

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

        # 1. Circuit Breaker Check
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

        # 2. Check candidate variants for statistical significance
        alpha_base, beta_base = self.calculate_posterior_parameters(
            clicks=baseline_variant.get("clicks", 0),
            impressions=baseline_variant.get("impressions", 0),
        )

        best_challenger_idx = None
        best_challenger_prob = 0.0
        best_challenger_title = None

        for idx, var in enumerate(variations):
            if idx == active_idx or idx == baseline_idx:
                continue

            impressions = var.get("impressions", 0)
            clicks = var.get("clicks", 0)

            # Minimum sample size gate
            if impressions < self.min_impressions:
                continue

            alpha_c, beta_c = self.calculate_posterior_parameters(clicks=clicks, impressions=impressions)
            prob_beats_base = self.probability_b_beats_a(alpha_base, beta_base, alpha_c, beta_c)

            if prob_beats_base > best_challenger_prob:
                best_challenger_prob = prob_beats_base
                best_challenger_idx = idx
                best_challenger_title = var.get("title")

        # 3. Decision Rule: Does the best challenger cross the 95% confidence threshold?
        if best_challenger_idx is not None and best_challenger_prob >= self.confidence_threshold:
            logger.info(
                "A/B WINNER FOUND: Variant '%s' beats baseline with %.2f%% confidence (threshold: %.0f%%).",
                best_challenger_title, best_challenger_prob * 100, self.confidence_threshold * 100
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
                    "confidence_pct": round(best_challenger_prob * 100, 2),
                    "impressions": variations[best_challenger_idx].get("impressions", 0),
                    "clicks": variations[best_challenger_idx].get("clicks", 0),
                },
            )

        return VariantEvaluationResult(
            action="MAINTAIN",
            winning_variant_index=active_idx,
            winning_title=active_variant.get("title"),
            confidence=best_challenger_prob,
            baseline_velocity=baseline_velocity,
            current_velocity=current_velocity,
            details={"status": "No challenger variant has reached statistical significance yet."},
        )
