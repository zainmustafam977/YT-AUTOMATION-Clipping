"""
Comprehensive Acceptance Tests for Quota-Safe, Heuristic A/B Winner Daemon.

Acceptance Criteria:
1. Seeded metrics below thresholds: no swap, reason logged.
   - Below impressions threshold (< 1000): MAINTAIN, logged.
   - Below duration threshold (< 72h): MAINTAIN, logged.
   - Below relative margin threshold (< 10%): MAINTAIN, logged.
   - Kill-switch disabled (AB_ENABLE_TITLE_SWAPPING = False): MAINTAIN, logged.
2. Seeded metrics above thresholds: exactly one metadata_update job enqueued.
3. Second swap attempt for the same video is refused (max swaps = 1).
4. Module docstring explicitly documents that title CTR is a weak signal for Shorts.
5. All pytest green.
"""

import logging
from datetime import datetime, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.config.settings import settings
from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    Clip,
    ClipStatus,
    ClipCandidate,
    SourceVideo,
    Channel,
    PublishLog,
    AnalyticsMetric,
    PipelineJob,
)
from shorts_engine.services.ab_testing.bandit import BayesianVariantOptimizer
from shorts_engine.services.ab_testing.winner_daemon import evaluate_and_promote_clip
import shorts_engine.services.ab_testing.bandit as bandit_mod
import shorts_engine.services.ab_testing.winner_daemon as daemon_mod


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


def _create_clip_fixture(db, clip_id="clip_ab_001", published_hours_ago=80.0):
    ch = Channel(name="Test Channel", youtube_channel_id="UC_test_ch")
    db.add(ch)
    db.commit()

    src = SourceVideo(youtube_video_id="src_001", title="Source Master", channel_id=ch.id)
    db.add(src)
    db.commit()

    cand = ClipCandidate(
        source_video_id=src.id,
        start_ms=0,
        end_ms=30000,
        hook_text="Hook",
        reason="Viral",
        score=9.0,
    )
    db.add(cand)
    db.commit()

    clip = Clip(
        id=clip_id,
        clip_candidate_id=cand.id,
        status=ClipStatus.PUBLISHED,
        rendered_path="out.mp4",
        generated_title="Baseline Title A",
    )
    db.add(clip)
    db.commit()

    pub_log = PublishLog(
        clip_id=clip.id,
        youtube_video_id="yt_test_vid_123",
        title="Baseline Title A",
        description="Test Description",
        status="SUCCESS",
        published_at=datetime.utcnow() - timedelta(hours=published_hours_ago),
    )
    db.add(pub_log)
    db.commit()

    return clip, pub_log


# ==============================================================================
# Acceptance Test 1: Seeded metrics below thresholds -> no swap, reason logged
# ==============================================================================

def test_below_impressions_threshold_no_swap_reason_logged(db_session, caplog):
    """
    CRITERION 1a: Challenger impressions < AB_MIN_IMPRESSIONS (1000).
    Must not swap; reason must be logged.
    """
    clip, _ = _create_clip_fixture(db_session, clip_id="clip_imp_low", published_hours_ago=80.0)
    clip.variations = [
        {"title": "Baseline Title A", "impressions": 1200, "clicks": 60, "is_active": True, "is_baseline": True},
        {"title": "Challenger Title B", "impressions": 600, "clicks": 90, "is_active": False, "is_baseline": False},
    ]
    db_session.commit()

    with caplog.at_level(logging.INFO):
        res = evaluate_and_promote_clip(db_session, clip)

    assert res is not None
    assert res.action == "MAINTAIN"
    assert "impressions (600) < threshold (1000)" in caplog.text

    # Verify no metadata_update job enqueued
    queued_jobs = db_session.query(PipelineJob).filter_by(entity_id=clip.id).all()
    assert len(queued_jobs) == 0


def test_below_duration_threshold_no_swap_reason_logged(db_session, caplog):
    """
    CRITERION 1b: Test duration < AB_MIN_TEST_DURATION_HOURS (72h).
    Must not swap; reason must be logged.
    """
    # Published only 24 hours ago (< 72h)
    clip, _ = _create_clip_fixture(db_session, clip_id="clip_dur_low", published_hours_ago=24.0)
    clip.variations = [
        {"title": "Baseline Title A", "impressions": 1200, "clicks": 60, "is_active": True, "is_baseline": True},
        {"title": "Challenger Title B", "impressions": 1500, "clicks": 225, "is_active": False, "is_baseline": False},
    ]
    db_session.commit()

    with caplog.at_level(logging.INFO):
        res = evaluate_and_promote_clip(db_session, clip)

    assert res is not None
    assert res.action == "MAINTAIN"
    assert "test duration" in caplog.text.lower()
    assert "below threshold" in res.details.get("status", "") or "Minimum test duration" in res.details.get("reason", "")

    queued_jobs = db_session.query(PipelineJob).filter_by(entity_id=clip.id).all()
    assert len(queued_jobs) == 0


def test_below_relative_margin_threshold_no_swap_reason_logged(db_session, caplog):
    """
    CRITERION 1c: Relative CTR margin < AB_RELATIVE_MARGIN_THRESHOLD (10%).
    Baseline CTR = 5.0%, Challenger CTR = 5.2% (+4% relative margin < 10%).
    Must not swap; reason must be logged.
    """
    clip, _ = _create_clip_fixture(db_session, clip_id="clip_margin_low", published_hours_ago=80.0)
    clip.variations = [
        {"title": "Baseline Title A", "impressions": 2000, "clicks": 100, "is_active": True, "is_baseline": True},
        {"title": "Challenger Title B", "impressions": 2000, "clicks": 104, "is_active": False, "is_baseline": False},
    ]
    db_session.commit()

    with caplog.at_level(logging.INFO):
        res = evaluate_and_promote_clip(db_session, clip)

    assert res is not None
    assert res.action == "MAINTAIN"
    assert "rel_margin" in caplog.text or "relative margin" in caplog.text.lower()
    assert "Relative CTR margin threshold not reached" in res.details.get("reason", "")

    queued_jobs = db_session.query(PipelineJob).filter_by(entity_id=clip.id).all()
    assert len(queued_jobs) == 0


def test_kill_switch_disabled_no_swap_reason_logged(db_session, caplog, monkeypatch):
    """
    CRITERION 1d: Master kill-switch AB_ENABLE_TITLE_SWAPPING = False.
    All metric thresholds exceeded, but kill-switch halts all title swapping.
    """
    monkeypatch.setattr(settings, "AB_ENABLE_TITLE_SWAPPING", False)
    clip, _ = _create_clip_fixture(db_session, clip_id="clip_killswitch", published_hours_ago=80.0)
    clip.variations = [
        {"title": "Baseline Title A", "impressions": 1200, "clicks": 60, "is_active": True, "is_baseline": True},
        {"title": "Challenger Title B", "impressions": 1500, "clicks": 240, "is_active": False, "is_baseline": False},
    ]
    db_session.commit()

    with caplog.at_level(logging.INFO):
        res = evaluate_and_promote_clip(db_session, clip)

    assert res is not None
    assert res.action == "MAINTAIN"
    assert "kill switch" in caplog.text.lower()
    assert res.details.get("enable_title_swapping") is False

    queued_jobs = db_session.query(PipelineJob).filter_by(entity_id=clip.id).all()
    assert len(queued_jobs) == 0


# ==============================================================================
# Acceptance Test 2: Seeded metrics above thresholds -> exactly one job enqueued
# ==============================================================================

def test_above_thresholds_exactly_one_metadata_update_job_enqueued(db_session, caplog):
    """
    CRITERION 2: All thresholds satisfied:
    - Impressions >= 1000 (1200 and 1500)
    - Test duration >= 72h (80h)
    - Relative CTR margin >= 10% (16% vs 5% -> +220%)
    - Title swap count = 0 (< 1)
    - Kill-switch enabled
    Result: Action is PROMOTE, and EXACTLY ONE metadata_update job is enqueued.
    """
    clip, pub_log = _create_clip_fixture(db_session, clip_id="clip_promote_success", published_hours_ago=80.0)
    clip.variations = [
        {
            "title": "Baseline Title A",
            "impressions": 1200,
            "clicks": 60,
            "is_active": True,
            "is_baseline": True,
            "thumbnail_path": "thumbs/thumb_a.jpg",
        },
        {
            "title": "Challenger Title B Wins!",
            "impressions": 1500,
            "clicks": 240,
            "is_active": False,
            "is_baseline": False,
            "thumbnail_path": "thumbs/thumb_b.jpg",
        },
    ]
    db_session.commit()

    with caplog.at_level(logging.INFO):
        res = evaluate_and_promote_clip(db_session, clip)

    assert res is not None
    assert res.action == "PROMOTE"
    assert res.winning_variant_index == 1
    assert res.winning_title == "Challenger Title B Wins!"

    # Verify log output records the numbers behind the decision
    assert "HEURISTIC A/B WINNER" in caplog.text or "Promoting variant" in caplog.text
    assert "rel_margin" in caplog.text or "rel_margin" in str(res.details)

    # Verify EXACTLY ONE metadata_update job was enqueued in durable_job_queue
    queued_jobs = (
        db_session.query(PipelineJob)
        .filter_by(entity_id=clip.id, stage="metadata_update")
        .all()
    )
    assert len(queued_jobs) == 1
    job = queued_jobs[0]
    assert job.job_type == "metadata_update"
    import json
    payload = json.loads(job.payload_json)
    assert payload["youtube_video_id"] == pub_log.youtube_video_id
    assert payload["title"] == "Challenger Title B Wins!"
    assert payload["thumbnail_path"] == "thumbs/thumb_b.jpg"

    # Verify Clip state updated
    db_session.refresh(clip)
    assert clip.generated_title == "Challenger Title B Wins!"
    assert clip.variations[1]["is_active"] is True
    assert clip.variations[0]["is_active"] is False

    # Verify swap count incremented in clip.analytics
    assert clip.analytics.get("title_swap_count") == 1
    assert clip.analytics.get("last_promotion", {}).get("swap_number") == 1


# ==============================================================================
# Acceptance Test 3: Second swap attempt for same video is refused (max swaps = 1)
# ==============================================================================

def test_second_swap_attempt_refused_max_swaps_one(db_session, caplog):
    """
    CRITERION 3: Second swap attempt on the same video is refused because
    AB_MAX_TITLE_SWAPS = 1.
    """
    clip, pub_log = _create_clip_fixture(db_session, clip_id="clip_second_swap", published_hours_ago=80.0)
    clip.variations = [
        {"title": "Baseline Title A", "impressions": 1200, "clicks": 60, "is_active": True, "is_baseline": True},
        {"title": "Winner Title B", "impressions": 1500, "clicks": 240, "is_active": False, "is_baseline": False},
    ]
    db_session.commit()

    # 1. First evaluation: Winner Title B is promoted
    res1 = evaluate_and_promote_clip(db_session, clip)
    assert res1 is not None
    assert res1.action == "PROMOTE"
    assert res1.winning_title == "Winner Title B"

    # Check 1 job enqueued and title_swap_count == 1
    db_session.refresh(clip)
    assert clip.analytics.get("title_swap_count") == 1
    assert db_session.query(PipelineJob).filter_by(entity_id=clip.id, stage="metadata_update").count() == 1

    # 2. Second evaluation: Introduce Challenger Title C with even higher CTR (25%), but video has already swapped once!
    current_vars = list(clip.variations)
    current_vars.append(
        {"title": "Challenger Title C", "impressions": 2000, "clicks": 500, "is_active": False, "is_baseline": False}
    )
    clip.variations = current_vars
    db_session.commit()

    caplog.clear()
    with caplog.at_level(logging.INFO):
        res2 = evaluate_and_promote_clip(db_session, clip)

    assert res2 is not None
    assert res2.action == "MAINTAIN"
    assert "Title swap REFUSED: swap count (1) >= max allowed swaps (1)" in caplog.text
    assert res2.details.get("reason") == "Max swaps reached" or "Maximum title swaps reached" in res2.details.get("status", "")

    # Crucial: NO second job enqueued! Total remains exactly 1
    total_jobs = db_session.query(PipelineJob).filter_by(entity_id=clip.id, stage="metadata_update").count()
    assert total_jobs == 1


# ==============================================================================
# Acceptance Test 4: Weak Signal Documentation in Module Docstrings
# ==============================================================================

def test_module_docstrings_weak_signal_notice():
    """
    CRITERION 4: bandit.py and winner_daemon.py docstrings must document that
    title CTR is a weak signal for YouTube Shorts due to feed-swipe discovery.
    """
    assert "feed-swipe" in bandit_mod.__doc__.lower()
    assert "weak signal" in bandit_mod.__doc__.lower()
    assert "modest" in bandit_mod.__doc__.lower()

    assert "feed-swipe" in daemon_mod.__doc__.lower()
    assert "weak signal" in daemon_mod.__doc__.lower()
    assert "modest" in daemon_mod.__doc__.lower()
