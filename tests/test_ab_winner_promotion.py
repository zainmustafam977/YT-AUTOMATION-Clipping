"""
Unit & Integration Tests for Component 4: Bayesian Multi-Armed Bandit A/B Winner Promotion Engine
(Conjugate Beta-Binomial Updating, Velocity Scoring, 95% Confidence Gate, Circuit Breaker Rollback)
"""

from unittest.mock import MagicMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.db.models import Clip, ClipStatus, ClipCandidate, SourceVideo, Channel, PublishLog
from shorts_engine.services.ab_testing.bandit import BayesianVariantOptimizer
from shorts_engine.services.ab_testing.winner_daemon import evaluate_and_promote_clip


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def test_posterior_parameter_updating():
    """Verifies Beta-Binomial conjugate update: alpha = alpha_0 + clicks, beta = beta_0 + (imp - clicks)."""
    optimizer = BayesianVariantOptimizer(prior_alpha=1.0, prior_beta=19.0)
    alpha, beta = optimizer.calculate_posterior_parameters(clicks=10, impressions=100)
    assert alpha == 11.0  # 1.0 + 10
    assert beta == 109.0  # 19.0 + (100 - 10)


def test_probability_b_beats_a_statistical_significance():
    """Verifies that high-performing variant crosses 95% significance gate."""
    optimizer = BayesianVariantOptimizer()

    # Variant A: 50 clicks / 1000 imp (~5% CTR)
    alpha_a, beta_a = optimizer.calculate_posterior_parameters(clicks=50, impressions=1000)
    # Variant B: 110 clicks / 1000 imp (~11% CTR)
    alpha_b, beta_b = optimizer.calculate_posterior_parameters(clicks=110, impressions=1000)

    p_b_beats_a = optimizer.probability_b_beats_a(alpha_a, beta_a, alpha_b, beta_b)
    assert p_b_beats_a >= 0.99  # Clearly beats baseline with overwhelming confidence

    # Identical performance should be around ~50%
    alpha_c, beta_c = optimizer.calculate_posterior_parameters(clicks=50, impressions=1000)
    p_tie = optimizer.probability_b_beats_a(alpha_a, beta_a, alpha_c, beta_c)
    assert 0.45 <= p_tie <= 0.55


def test_view_velocity_and_circuit_breaker():
    """Verifies view velocity calculation and > 50% velocity drop circuit breaker."""
    optimizer = BayesianVariantOptimizer(rollback_velocity_drop=0.50)

    # 150 views over 2.5 hours = 60 views/hr
    velocity = optimizer.calculate_view_velocity(views_delta=150, time_delta_hours=2.5)
    assert velocity == 60.0

    # Baseline: 100 v/hr -> Current: 40 v/hr (60% drop > 50% threshold) -> Breaker fires!
    assert optimizer.check_circuit_breaker(current_velocity=40.0, baseline_velocity=100.0) is True

    # Baseline: 100 v/hr -> Current: 80 v/hr (20% drop <= 50% threshold) -> Breaker does not fire
    assert optimizer.check_circuit_breaker(current_velocity=80.0, baseline_velocity=100.0) is False


def test_evaluate_and_promote_clip_winner_promotion(db_session):
    """Verifies that a statistically significant winning title is promoted live."""
    ch = Channel(name="Test Channel", youtube_channel_id="UC_bandit_1")
    db_session.add(ch)
    db_session.commit()

    src = SourceVideo(
        youtube_video_id="src_bandit_1",
        title="Source Video Master",
        channel_id=ch.id,
    )
    db_session.add(src)
    db_session.commit()

    cand = ClipCandidate(
        source_video_id=src.id,
        start_ms=0,
        end_ms=25000,
        hook_text="Original Hook",
        reason="Good",
        score=8.5,
    )
    db_session.add(cand)
    db_session.commit()

    clip = Clip(
        clip_candidate_id=cand.id,
        status=ClipStatus.PUBLISHED,
        rendered_path="dummy.mp4",
        generated_title="Original Baseline Title",
    )
    # Configure A/B variations: Variant 0 (baseline) vs Variant 1 (viral challenger)
    variations = [
        {
            "title": "Original Baseline Title",
            "impressions": 500,
            "clicks": 25,  # 5% CTR
            "is_active": True,
            "is_baseline": True,
        },
        {
            "title": "Insanely Viral Winning Title!",
            "impressions": 500,
            "clicks": 80,  # 16% CTR -> heavily beats baseline!
            "is_active": False,
            "is_baseline": False,
        },
    ]
    clip.variations = variations
    db_session.add(clip)
    db_session.commit()

    pub_log = PublishLog(
        clip_id=clip.id,
        youtube_video_id="yt_live_123",
        title="Original Baseline Title",
        description="Description",
        status="SUCCESS",
    )
    db_session.add(pub_log)
    db_session.commit()

    mock_pub = MagicMock()
    mock_pub.update_video_title.return_value = {"success": True, "title": "Insanely Viral Winning Title! #Shorts"}

    res = evaluate_and_promote_clip(db_session, clip, publisher=mock_pub)

    assert res is not None
    assert res.action == "PROMOTE"
    assert res.winning_variant_index == 1
    assert "Insanely Viral" in res.winning_title
    assert res.confidence >= 0.95

    # Verify live title update was dispatched
    mock_pub.update_video_title.assert_called_once()

    # Verify atomic update on Clip model
    db_session.refresh(clip)
    assert clip.generated_title == "Insanely Viral Winning Title!"
    updated_vars = clip.variations
    assert updated_vars[1]["is_active"] is True
    assert updated_vars[0]["is_active"] is False
    assert "promoted_at" in updated_vars[1]


def test_evaluate_and_promote_clip_circuit_breaker_rollback(db_session):
    """Verifies that a > 50% view velocity collapse triggers an emergency rollback to baseline."""
    ch = Channel(name="Test Channel", youtube_channel_id="UC_bandit_2")
    db_session.add(ch)
    db_session.commit()

    src = SourceVideo(
        youtube_video_id="src_bandit_2",
        title="Source Video Master 2",
        channel_id=ch.id,
    )
    db_session.add(src)
    db_session.commit()

    cand = ClipCandidate(
        source_video_id=src.id,
        start_ms=0,
        end_ms=25000,
        hook_text="Original Hook",
        reason="Good",
        score=8.5,
    )
    db_session.add(cand)
    db_session.commit()

    clip = Clip(
        clip_candidate_id=cand.id,
        status=ClipStatus.PUBLISHED,
        rendered_path="dummy.mp4",
        generated_title="New Risky Title",
    )
    # Variant 1 is currently active but underperforming in velocity
    variations = [
        {
            "title": "Safe Proven Baseline",
            "impressions": 1000,
            "clicks": 80,
            "is_active": False,
            "is_baseline": True,
        },
        {
            "title": "New Risky Title",
            "impressions": 200,
            "clicks": 10,
            "is_active": True,
            "is_baseline": False,
        },
    ]
    clip.variations = variations
    # Baseline was 120 views/hr, but current collapsed to 30 views/hr (75% drop!)
    clip.analytics = {"baseline_velocity": 120.0, "current_velocity": 30.0}
    db_session.add(clip)
    db_session.commit()

    pub_log = PublishLog(
        clip_id=clip.id,
        youtube_video_id="yt_live_456",
        title="New Risky Title",
        description="Description",
        status="SUCCESS",
    )
    db_session.add(pub_log)
    db_session.commit()

    mock_pub = MagicMock()
    mock_pub.update_video_title.return_value = {"success": True, "title": "Safe Proven Baseline #Shorts"}

    res = evaluate_and_promote_clip(db_session, clip, publisher=mock_pub)

    assert res is not None
    assert res.action == "ROLLBACK"
    assert res.circuit_breaker_triggered is True
    assert res.winning_title == "Safe Proven Baseline"

    # Verify rollback title update was dispatched
    mock_pub.update_video_title.assert_called_once()

    # Verify atomic update reverted active title
    db_session.refresh(clip)
    assert clip.generated_title == "Safe Proven Baseline"
    updated_vars = clip.variations
    assert updated_vars[0]["is_active"] is True
    assert updated_vars[1]["is_active"] is False
    assert "circuit_breaker_rollback" in clip.analytics
