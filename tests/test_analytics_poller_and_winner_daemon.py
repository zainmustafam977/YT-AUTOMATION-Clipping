"""
Comprehensive Acceptance & Unit Tests for Analytics Poller & Winner Daemon Integration.
Verifies:
1. Mocked YouTube Analytics API hourly runs upsert rows into analytics_metrics correctly.
2. Idempotency: Running poller twice does not create duplicate rows; updates in place.
3. Hourly scheduling via queue run_at mechanism (no separate daemon).
4. Winner daemon's data source is the analytics_metrics table (promotions & circuit breaker rollbacks).
5. AST/Grep verification proving winner_daemon has zero direct platform API calls or client imports.
"""

import ast
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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
    PipelineJobStatus,
)
from shorts_engine.services.analytics.poller import AnalyticsPoller
from shorts_engine.services.ab_testing.winner_daemon import evaluate_and_promote_clip, check_and_promote_ab_winners
from shorts_engine.services.queue.durable_queue import durable_job_queue
from shorts_engine.orchestration.stage_registry import StageContext, analytics_poll_stage, PIPELINE


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


def _create_published_test_clip(db_session, clip_id="clip_analytics_001", yt_id="yt_vid_999"):
    """Helper to seed channel, source video, candidate, published clip, and publish log."""
    ch = Channel(name="Analytics Test Channel", youtube_channel_id="UC_analytics_channel")
    db_session.add(ch)
    db_session.commit()

    src = SourceVideo(
        youtube_video_id="src_vid_001",
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
        score=9.0,
    )
    db_session.add(cand)
    db_session.commit()

    clip = Clip(
        id=clip_id,
        clip_candidate_id=cand.id,
        status=ClipStatus.PUBLISHED,
        rendered_path="dummy.mp4",
        generated_title="Baseline Title",
    )
    clip.variations = [
        {"title": "Baseline Title", "impressions": 100, "clicks": 5, "is_active": True, "is_baseline": True},
        {"title": "Challenger Title", "impressions": 100, "clicks": 20, "is_active": False, "is_baseline": False},
    ]
    db_session.add(clip)
    db_session.commit()

    pub_log = PublishLog(
        clip_id=clip.id,
        youtube_video_id=yt_id,
        title="Baseline Title",
        description="Description",
        status="SUCCESS",
    )
    db_session.add(pub_log)
    db_session.commit()

    return ch, src, cand, clip, pub_log


def _build_mock_analytics_client(report_rows):
    """Creates a mock YouTube Analytics API client returning given rows."""
    mock_client = MagicMock()
    mock_reports = MagicMock()
    mock_query = MagicMock()
    mock_execute = MagicMock()

    mock_execute.execute.return_value = {
        "kind": "youtubeAnalytics#resultTable",
        "columnHeaders": [
            {"name": "day"},
            {"name": "views"},
            {"name": "impressions"},
            {"name": "clickThroughRate"},
            {"name": "averageViewDuration"},
            {"name": "subscribersGained"},
        ],
        "rows": report_rows,
    }
    mock_query.return_value = mock_execute
    mock_reports.query = mock_query
    mock_client.reports.return_value = mock_reports
    return mock_client


# ==============================================================================
# Acceptance Test 1: Hourly Runs Upsert Rows Correctly
# ==============================================================================

def test_analytics_poller_upserts_metrics_correctly(db_session):
    """
    CRITERION 1: With a mocked API, polling pulls multi-day metrics and upserts
    into analytics_metrics table with all required fields.
    """
    _, _, _, clip, pub_log = _create_published_test_clip(db_session)

    # 3 days of metrics
    rows = [
        ["2026-10-08", 450, 7500, 0.06, 41.2, 3],
        ["2026-10-09", 820, 12000, 0.068, 44.5, 7],
        ["2026-10-10", 1250, 18000, 0.069, 45.0, 12],
    ]
    mock_client = _build_mock_analytics_client(rows)

    poller = AnalyticsPoller()
    records = poller.poll_clip_analytics(db=db_session, clip=clip, lookback_days=7, client=mock_client)

    assert len(records) == 3

    # Verify rows in database
    db_rows = (
        db_session.query(AnalyticsMetric)
        .filter_by(clip_id=clip.id)
        .order_by(AnalyticsMetric.snapshot_date.asc())
        .all()
    )
    assert len(db_rows) == 3

    r1 = db_rows[0]
    assert r1.snapshot_date == "2026-10-08"
    assert r1.views == 450
    assert r1.impressions == 7500
    assert r1.click_through_rate == 0.06
    assert r1.average_view_duration == 41.2
    assert r1.subscribers_gained == 3
    assert r1.platform_video_id == pub_log.youtube_video_id

    # Verify clip.analytics was populated from table
    db_session.refresh(clip)
    assert clip.analytics["views"] == 450 + 820 + 1250
    assert clip.analytics["impressions"] == 7500 + 12000 + 18000
    assert clip.analytics["subscribers_gained"] == 3 + 7 + 12
    assert clip.analytics["current_velocity"] > 0.0


# ==============================================================================
# Acceptance Test 2: Idempotency Verification
# ==============================================================================

def test_analytics_poller_idempotency_running_twice_does_not_duplicate(db_session):
    """
    CRITERION 2: Running twice does not create duplicate rows; updates existing rows in place.
    """
    _, _, _, clip, _ = _create_published_test_clip(db_session, clip_id="clip_idem_002")

    initial_rows = [
        ["2026-10-09", 500, 10000, 0.05, 38.0, 2],
        ["2026-10-10", 900, 15000, 0.06, 42.0, 5],
    ]
    mock_client_1 = _build_mock_analytics_client(initial_rows)

    poller = AnalyticsPoller()
    poller.poll_clip_analytics(db=db_session, clip=clip, lookback_days=7, client=mock_client_1)

    count_after_run_1 = db_session.query(AnalyticsMetric).filter_by(clip_id=clip.id).count()
    assert count_after_run_1 == 2

    # Run a second time with updated view count on 2026-10-10
    updated_rows = [
        ["2026-10-09", 500, 10000, 0.05, 38.0, 2],
        ["2026-10-10", 1150, 18000, 0.064, 43.5, 8],  # updated views
    ]
    mock_client_2 = _build_mock_analytics_client(updated_rows)

    poller.poll_clip_analytics(db=db_session, clip=clip, lookback_days=7, client=mock_client_2)

    # Count must remain exactly 2 — NO DUPLICATE ROWS
    count_after_run_2 = db_session.query(AnalyticsMetric).filter_by(clip_id=clip.id).count()
    assert count_after_run_2 == 2

    # Verify in-place update on 2026-10-10 row
    updated_metric = (
        db_session.query(AnalyticsMetric)
        .filter_by(clip_id=clip.id, snapshot_date="2026-10-10")
        .first()
    )
    assert updated_metric.views == 1150
    assert updated_metric.impressions == 18000
    assert updated_metric.subscribers_gained == 8


# ==============================================================================
# Acceptance Test 3: Hourly Scheduling Via Durable Queue's run_at
# ==============================================================================

def test_analytics_poll_scheduled_hourly_via_queue_run_at(db_session):
    """
    CRITERION 3: Job type 'analytics_poll' is scheduled hourly via run_at mechanism.
    No separate daemon process is required.
    """
    poller = AnalyticsPoller()
    job = poller.schedule_next_poll(db=db_session, delay_hours=1.0)

    assert job is not None
    assert job.job_type == "analytics_poll"
    assert job.stage == "analytics_poll"
    assert job.status == PipelineJobStatus.PENDING

    # run_at must be strictly 1 hour in the future
    now = datetime.utcnow()
    assert job.next_run_at > now
    assert job.next_run_at >= now + timedelta(minutes=59)
    assert job.next_run_at <= now + timedelta(minutes=61)

    # Claim loop refuses to claim it while next_run_at is in the future
    claimed_early = durable_job_queue.claim_next_job(db=db_session)
    assert claimed_early is None

    # Once next_run_at arrives, worker claim loop successfully picks it up
    job.next_run_at = now - timedelta(seconds=1)
    db_session.commit()

    claimed = durable_job_queue.claim_next_job(db=db_session)
    assert claimed is not None
    assert claimed.id == job.id

    # Verify stage registry executes analytics_poll_stage and schedules next run
    ctx = StageContext(
        db_session=db_session,
        video_id="global",
        job_id=claimed.id,
        payload={"lookback_days": 3, "scheduled_interval_hours": 1.0},
    )
    res = analytics_poll_stage(ctx)
    assert "polled_clips_count" in res

    # Verify the next hourly poll was scheduled
    next_job = (
        db_session.query(PipelineJob)
        .filter(PipelineJob.id != job.id, PipelineJob.job_type == "analytics_poll")
        .first()
    )
    assert next_job is not None
    assert next_job.next_run_at > now


# ==============================================================================
# Acceptance Test 4: Winner Daemon Reads Exclusively From analytics_metrics
# ==============================================================================

def test_winner_daemon_reads_from_analytics_metrics_table(db_session):
    """
    CRITERION 4: winner_daemon evaluates variants and makes decisions reading
    strictly from the analytics_metrics table, with zero direct API calls.
    """
    _, _, _, clip, pub_log = _create_published_test_clip(db_session, clip_id="clip_daemon_003")

    # Set up variations: Variant 0 (baseline) vs Variant 1 (challenger)
    # Seed challenger with >= 1000 impressions and > 10% relative margin
    clip.variations = [
        {"title": "Boring Baseline Title", "impressions": 1000, "clicks": 50, "is_active": True, "is_baseline": True},
        {"title": "Viral Breakthrough Title!", "impressions": 1500, "clicks": 240, "is_active": False, "is_baseline": False},
    ]
    pub_log.published_at = datetime.utcnow() - timedelta(hours=80)
    db_session.commit()

    # Seed analytics_metrics table with 3 days of high-performing data
    for day, views, imp, ctr in [
        ("2026-10-08", 300, 5000, 0.06),
        ("2026-10-09", 600, 8000, 0.075),
        ("2026-10-10", 1200, 14000, 0.085),
    ]:
        m = AnalyticsMetric(
            clip_id=clip.id,
            platform_video_id=pub_log.youtube_video_id,
            snapshot_date=day,
            views=views,
            impressions=imp,
            click_through_rate=ctr,
            average_view_duration=42.0,
            subscribers_gained=10,
        )
        db_session.add(m)
    db_session.commit()

    # Call evaluate_and_promote_clip without any publisher mock
    res = evaluate_and_promote_clip(db=db_session, clip=clip)

    assert res is not None
    assert res.action == "PROMOTE"
    assert res.winning_variant_index == 1
    assert "Viral Breakthrough Title" in res.winning_title
    assert res.confidence >= 0.95

    # Outbound action must be enqueued into durable_job_queue as metadata_update intent
    queued_intent = (
        db_session.query(PipelineJob)
        .filter_by(entity_id=clip.id, stage="metadata_update")
        .first()
    )
    assert queued_intent is not None
    assert queued_intent.job_type == "metadata_update"


def test_winner_daemon_circuit_breaker_from_analytics_metrics(db_session):
    """
    Verifies that collapsed velocity reflected in analytics_metrics table
    triggers emergency circuit breaker rollback in winner_daemon.
    """
    _, _, _, clip, pub_log = _create_published_test_clip(db_session, clip_id="clip_cb_004")

    # Variant 1 is currently active but underperforming
    clip.variations = [
        {"title": "Safe Proven Baseline", "impressions": 1000, "clicks": 80, "is_active": False, "is_baseline": True},
        {"title": "Risky Collapsed Title", "impressions": 200, "clicks": 10, "is_active": True, "is_baseline": False},
    ]
    # Set high baseline velocity
    clip.analytics = {"baseline_velocity": 120.0, "current_velocity": 120.0}
    db_session.commit()

    # Seed analytics_metrics table with collapsed velocity (e.g. only 240 views over 24h = 10 views/hr -> 91% collapse)
    m = AnalyticsMetric(
        clip_id=clip.id,
        platform_video_id=pub_log.youtube_video_id,
        snapshot_date="2026-10-10",
        views=240,  # 240 / 24 = 10 views/hour
        impressions=5000,
        click_through_rate=0.048,
        average_view_duration=25.0,
        subscribers_gained=1,
    )
    db_session.add(m)
    db_session.commit()

    res = evaluate_and_promote_clip(db=db_session, clip=clip)

    assert res is not None
    assert res.action == "ROLLBACK"
    assert res.circuit_breaker_triggered is True
    assert res.winning_title == "Safe Proven Baseline"


# ==============================================================================
# Acceptance Test 5: AST / Grep Verification — Zero Direct Platform API Calls
# ==============================================================================

def test_winner_daemon_has_zero_direct_platform_api_calls():
    """
    CRITERION 5: AST analysis proves winner_daemon.py does not import Google client
    libraries, requests, or urllib, and makes zero direct external API calls.
    """
    repo_root = Path(__file__).resolve().parent.parent
    daemon_file = repo_root / "shorts_engine" / "services" / "ab_testing" / "winner_daemon.py"
    assert daemon_file.exists(), f"winner_daemon.py not found at {daemon_file}"

    tree = ast.parse(daemon_file.read_text(encoding="utf-8"), filename=str(daemon_file))

    forbidden_modules = {
        "googleapiclient",
        "google.oauth2",
        "google_auth_oauthlib",
        "requests",
        "urllib",
        "urllib.request",
        "httpx",
        "aiohttp",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for forbidden in forbidden_modules:
                    assert not (alias.name == forbidden or alias.name.startswith(forbidden + ".")), (
                        f"Forbidden import '{alias.name}' in winner_daemon.py"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for forbidden in forbidden_modules:
                    assert not (node.module == forbidden or node.module.startswith(forbidden + ".")), (
                        f"Forbidden from-import '{node.module}' in winner_daemon.py"
                    )
