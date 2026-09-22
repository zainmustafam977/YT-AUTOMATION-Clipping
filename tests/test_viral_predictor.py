"""
Tests for Component 4: Machine Learning Viral Score Predictor (ViralPredictor)
"""

import pytest
import numpy as np
from unittest.mock import MagicMock

from shorts_engine.services.analytics.predictor import ViralPredictor
from shorts_engine.db.models import Clip, ClipCandidate, ClipStatus


def test_feature_extraction_dimensions():
    """Verifies that extract_features produces a 12-dimensional float array with expected values."""
    predictor = ViralPredictor()
    hook = "Why you should NEVER make this huge mistake in 2026!"
    duration = 32.5
    payoff = "Because it will cost you everything."

    feats = predictor.extract_features(hook, duration, payoff)

    assert isinstance(feats, np.ndarray)
    assert feats.shape == (12,)
    # Dim 3 (has_question) should be 0, Dim 4 (has_exclamation) should be 1
    assert feats[3] == 0.0
    assert feats[4] == 1.0
    # Dim 5 (has_numbers) should be 1.0 because of '2026'
    assert feats[5] == 1.0
    # Dim 6 (power words: never, mistake) should be >= 2.0
    assert feats[6] >= 2.0
    # Dim 7 (pronouns: you) should be >= 1.0
    assert feats[7] >= 1.0
    # Dim 8 (all caps: NEVER) should be >= 1.0
    assert feats[8] >= 1.0
    # Dim 9 (duration)
    assert feats[9] == 32.5
    # Dim 10 (sweet spot dist: |32.5 - 35.0| = 2.5)
    assert abs(feats[10] - 2.5) < 0.01


def test_heuristic_scoring_bounds():
    """Verifies heuristic virality scoring remains calibrated between 10.0 and 99.0."""
    predictor = ViralPredictor()

    # Weak hook
    weak_score = predictor.compute_heuristic_score("Hello world", 10.0)
    assert 10.0 <= weak_score <= 50.0

    # Viral hook with question, power words, pronouns, optimal duration (32s)
    viral_hook = "Why did you NEVER know this secret rule? 5 insane facts revealed!"
    viral_score = predictor.compute_heuristic_score(viral_hook, 32.0, "This changes the entire game.")
    assert 70.0 <= viral_score <= 99.0
    assert viral_score > weak_score


def test_explain_prediction():
    """Verifies explain_prediction returns detailed feature breakdown."""
    predictor = ViralPredictor()
    hook = "Stop doing this mistake right now!"
    explanation = predictor.explain_prediction(hook, 30.0)

    assert "score" in explanation
    assert "features" in explanation
    assert explanation["features"]["has_exclamation"] is True
    assert "stop" in explanation["features"]["power_words"] or "mistake" in explanation["features"]["power_words"]


def test_train_from_db_insufficient_samples():
    """Verifies graceful fallback when fewer than min_samples are available."""
    predictor = ViralPredictor()
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = []

    res = predictor.train_from_db(mock_db, min_samples=5)
    assert res["trained"] is False
    assert res["samples"] == 0
    assert "Insufficient" in res["reason"]
    assert predictor.is_fitted is False


def test_train_from_db_and_predict():
    """Verifies model training when sufficient analytics data exists in database."""
    predictor = ViralPredictor()

    mock_clips = []
    for i in range(8):
        mock_cand = MagicMock()
        mock_cand.hook_text = f"Hook number {i} with secret tip for you!"
        mock_cand.payoff_text = "Here is the result."
        mock_cand.start_ms = 0
        mock_cand.end_ms = 30000 + (i * 2000)

        mock_clip = MagicMock()
        mock_clip.candidate = mock_cand
        mock_clip.analytics = {
            "views": 2000 + (i * 1500),
            "likes": 100 + (i * 80),
            "average_percentage_viewed": 65.0 + (i * 3.0),
        }
        mock_clips.append(mock_clip)

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = mock_clips

    res = predictor.train_from_db(mock_db, min_samples=5)
    assert res["trained"] is True
    assert res["samples"] == 8
    assert predictor.is_fitted is True

    # Test prediction with fitted model
    pred_score = predictor.predict_virality("Never do this crazy mistake!", 32.0)
    assert 10.0 <= pred_score <= 99.0
