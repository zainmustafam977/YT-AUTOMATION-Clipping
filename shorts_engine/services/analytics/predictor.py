"""
Machine Learning Virality Score Predictor:
Extracts linguistic, emotional, and structural features from candidate hooks and durations,
and predicts virality using a closed-loop model trained on actual YouTube analytics.
"""

import re
import math
import logging
from typing import Optional, Dict, Any, List, Tuple
from sqlalchemy.orm import Session

import numpy as np
try:
    from sklearn.linear_model import Ridge
    SKLEARN_AVAILABLE = True
except ImportError:
    Ridge = None
    SKLEARN_AVAILABLE = False

from shorts_engine.db.models import Clip, ClipCandidate, ClipStatus

logger = logging.getLogger(__name__)

# Curated high-engagement viral power keywords
POWER_WORDS = {
    "secret", "secrets", "never", "always", "stop", "best", "worst", "truth",
    "why", "how", "insane", "shocking", "money", "mistake", "mistakes",
    "warning", "fast", "hack", "hacks", "nobody", "everyone", "hidden",
    "rules", "rule", "dangerous", "crazy", "simple", "easy", "proven", "exposed"
}

SECOND_PERSON_PRONOUNS = {"you", "your", "you're", "yourself", "yours"}


class ViralPredictor:
    """
    ML-driven Virality Score Predictor.
    Combines rule-based behavioral heuristics with a closed-loop Ridge regression model
    trained on historical YouTube view counts, retention rates, and engagement.
    """

    def __init__(self):
        self.model: Optional[Any] = None
        self.is_fitted: bool = False
        self.sample_count: int = 0
        self.last_trained_at: Optional[str] = None

    def extract_features(
        self,
        hook_text: str,
        duration_sec: float,
        payoff_text: Optional[str] = None,
    ) -> np.ndarray:
        """
        Extracts a numeric feature vector from hook text, duration, and payoff text.
        Vector dimensions:
          0: Hook character length
          1: Hook word count
          2: Average word length
          3: Has question mark (0 or 1)
          4: Has exclamation mark (0 or 1)
          5: Has numeric digits (0 or 1)
          6: Power words count
          7: Second person pronoun count ("you", "your")
          8: All-caps word count (emphasis)
          9: Duration in seconds
          10: Distance from optimal duration (35s sweet spot)
          11: Payoff character length
        """
        hook = (hook_text or "").strip()
        payoff = (payoff_text or "").strip()
        words = re.findall(r"\b[a-zA-Z0-9']+\b", hook.lower())

        char_len = float(len(hook))
        word_count = float(len(words))
        avg_word_len = (char_len / word_count) if word_count > 0 else 0.0

        has_question = 1.0 if "?" in hook else 0.0
        has_exclamation = 1.0 if "!" in hook else 0.0
        has_numbers = 1.0 if re.search(r"\b\d+\b", hook) else 0.0

        power_word_count = float(sum(1 for w in words if w in POWER_WORDS))
        second_person_count = float(sum(1 for w in words if w in SECOND_PERSON_PRONOUNS))

        raw_tokens = hook.split()
        all_caps_count = float(sum(1 for t in raw_tokens if len(t) > 1 and t.isupper()))

        dur = float(duration_sec or 30.0)
        sweet_spot_dist = abs(dur - 35.0)
        payoff_len = float(len(payoff))

        return np.array([
            char_len,
            word_count,
            avg_word_len,
            has_question,
            has_exclamation,
            has_numbers,
            power_word_count,
            second_person_count,
            all_caps_count,
            dur,
            sweet_spot_dist,
            payoff_len,
        ], dtype=np.float32)

    def compute_heuristic_score(
        self,
        hook_text: str,
        duration_sec: float,
        payoff_text: Optional[str] = None,
    ) -> float:
        """
        Calibrated behavioral heuristic virality score (0 - 100).
        Used when insufficient training data is available or as a Bayesian prior.
        """
        hook = (hook_text or "").strip()
        if not hook:
            return 40.0

        words = re.findall(r"\b[a-zA-Z0-9']+\b", hook.lower())
        word_count = len(words)

        # 1. Hook strength component (up to 45 pts)
        hook_score = 15.0
        if "?" in hook:
            hook_score += 8.0
        if "!" in hook:
            hook_score += 4.0
        if re.search(r"\b\d+\b", hook):
            hook_score += 6.0

        power_hits = sum(1 for w in words if w in POWER_WORDS)
        hook_score += min(12.0, power_hits * 4.0)

        pronoun_hits = sum(1 for w in words if w in SECOND_PERSON_PRONOUNS)
        hook_score += min(8.0, pronoun_hits * 4.0)

        # Optimal hook length is 6 to 14 words
        if 6 <= word_count <= 14:
            hook_score += 5.0
        elif word_count < 4 or word_count > 25:
            hook_score -= 5.0

        hook_score = max(5.0, min(45.0, hook_score))

        # 2. Duration component (up to 30 pts)
        dur = float(duration_sec or 30.0)
        if 25.0 <= dur <= 45.0:
            dur_score = 30.0
        elif 15.0 <= dur < 25.0:
            dur_score = 22.0
        elif 45.0 < dur <= 58.0:
            dur_score = 24.0
        elif dur < 15.0:
            dur_score = 12.0
        else:
            dur_score = 16.0

        # 3. Payoff & structure component (up to 25 pts)
        struct_score = 10.0
        if payoff_text and len(payoff_text.strip()) > 10:
            struct_score += 10.0
        if 20 <= len(hook) <= 90:
            struct_score += 5.0

        total = hook_score + dur_score + struct_score
        return float(round(max(10.0, min(99.0, total)), 1))

    def train_from_db(self, db: Session, min_samples: int = 5) -> Dict[str, Any]:
        """
        Closed-loop training against historical published clips in the database
        that have populated analytics metrics.
        """
        if not SKLEARN_AVAILABLE:
            return {"trained": False, "reason": "scikit-learn not available"}

        clips = (
            db.query(Clip)
            .filter(
                Clip.analytics_json.isnot(None),
                Clip.analytics_json != "",
                Clip.analytics_json != "{}",
            )
            .all()
        )

        X_list: List[np.ndarray] = []
        y_list: List[float] = []

        for clip in clips:
            analytics = clip.analytics or {}
            views = float(analytics.get("views", 0) or analytics.get("view_count", 0))
            likes = float(analytics.get("likes", 0) or analytics.get("like_count", 0))
            avg_pct = analytics.get("average_percentage_viewed") or analytics.get("average_view_percentage")

            # Need at least views or engagement to compute ground truth
            if views <= 0 and not avg_pct:
                continue

            # Composite target score (0 - 100)
            view_pts = min(40.0, (views / 5000.0) * 40.0)
            like_ratio = (likes / views) if views > 0 else 0.0
            like_pts = min(30.0, like_ratio * 300.0)
            ret_pts = (float(avg_pct) * 0.3) if avg_pct is not None else 15.0
            actual_score = max(10.0, min(99.0, view_pts + like_pts + ret_pts))

            cand = clip.candidate
            hook = cand.hook_text if cand else (clip.generated_title or "")
            payoff = cand.payoff_text if cand else ""
            dur = 30.0
            if cand and cand.start_ms is not None and cand.end_ms is not None:
                dur = max(1.0, (cand.end_ms - cand.start_ms) / 1000.0)

            feats = self.extract_features(hook, dur, payoff)
            X_list.append(feats)
            y_list.append(actual_score)

        if len(X_list) < min_samples:
            logger.info("Insufficient analytics samples for ML virality training (%d < %d). Using heuristic baseline.", len(X_list), min_samples)
            return {
                "trained": False,
                "samples": len(X_list),
                "reason": f"Insufficient samples ({len(X_list)} < {min_samples})",
            }

        X = np.array(X_list, dtype=np.float32)
        y = np.array(y_list, dtype=np.float32)

        model = Ridge(alpha=1.0)
        model.fit(X, y)

        self.model = model
        self.is_fitted = True
        self.sample_count = len(X_list)
        from datetime import datetime
        self.last_trained_at = datetime.utcnow().isoformat()

        logger.info("Successfully trained ViralPredictor on %d historical video samples.", len(X_list))
        return {
            "trained": True,
            "samples": len(X_list),
            "model": "Ridge",
            "last_trained_at": self.last_trained_at,
        }

    def predict_virality(
        self,
        hook_text: str,
        duration_sec: float,
        payoff_text: Optional[str] = None,
    ) -> float:
        """
        Predicts virality score (0.0 to 100.0).
        Blends ML prediction with calibrated heuristic baseline when trained.
        """
        heuristic = self.compute_heuristic_score(hook_text, duration_sec, payoff_text)

        if not self.is_fitted or self.model is None:
            return heuristic

        feats = self.extract_features(hook_text, duration_sec, payoff_text)
        try:
            pred = float(self.model.predict(feats.reshape(1, -1))[0])
            # Blend 65% ML model with 35% heuristic prior to prevent out-of-distribution drift
            blended = (pred * 0.65) + (heuristic * 0.35)
            return float(round(max(10.0, min(99.0, blended)), 1))
        except Exception as e:
            logger.warning("Error predicting virality with ML model: %s. Using heuristic.", e)
            return heuristic

    def explain_prediction(
        self,
        hook_text: str,
        duration_sec: float,
        payoff_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Returns an analytical breakdown explaining why the clip received its virality score."""
        score = self.predict_virality(hook_text, duration_sec, payoff_text)
        hook = (hook_text or "").strip()
        words = re.findall(r"\b[a-zA-Z0-9']+\b", hook.lower())

        found_power_words = [w for w in words if w in POWER_WORDS]
        found_pronouns = [w for w in words if w in SECOND_PERSON_PRONOUNS]

        return {
            "score": score,
            "is_ml_driven": self.is_fitted,
            "sample_count": self.sample_count,
            "features": {
                "word_count": len(words),
                "duration_sec": round(duration_sec, 1),
                "has_question": "?" in hook,
                "has_exclamation": "!" in hook,
                "has_numbers": bool(re.search(r"\b\d+\b", hook)),
                "power_words": found_power_words,
                "pronouns": found_pronouns,
                "has_payoff": bool(payoff_text and len(payoff_text.strip()) > 5),
            }
        }


# Global singleton instance
viral_predictor = ViralPredictor()
