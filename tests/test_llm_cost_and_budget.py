"""
Unit and integration tests for LLM Token Cost Tracking, Model Pricing Table,
Database Logging, Rollup Queries, Daily Budget Brake Deferral, and Multi-Key 429 Failover.
"""

import json
from datetime import datetime, timedelta, date
from unittest.mock import MagicMock, patch
from pydantic import BaseModel, Field
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.config.settings import settings, PipelineSettings
from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    LLMUsageLog,
    PipelineJob,
    PipelineJobStatus,
    SourceVideo,
    User,
    UserRole,
)
from shorts_engine.orchestration.exceptions import (
    DailyBudgetExceededError,
)
from shorts_engine.orchestration.stage_registry import (
    Stage,
    StageContext,
    StageExecutor,
)
from shorts_engine.services.monitoring.token_tracker import (
    AITokenCostTracker,
    token_tracker,
)
from shorts_engine.services.llm.router import LLMRouter
from shorts_engine.services.llm.quota_tracker import APIUsageTracker
from shorts_engine.services.events.taxonomy import BUDGET_EXCEEDED
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.auth import require_viewer
from fastapi.testclient import TestClient


class DummySchema(BaseModel):
    title: str = Field(description="Clip title")
    score: float = Field(description="Score")


@pytest.fixture
def test_db():
    """Provides a fresh isolated in-memory SQLite database with schema created."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def isolated_tracker(tmp_path):
    """Provides an isolated token tracker pointing to a temporary file."""
    state_file = tmp_path / "test_token_costs.json"
    tracker = AITokenCostTracker(state_file=state_file)
    tracker.reset_for_testing()
    return tracker


# ── 1. Hand-Computed Pricing Calculations ─────────────────────────────────────

def test_price_table_calculation_hand_computed():
    """
    Verifies that calculated costs match exact hand-computed values from the price table.
    """
    # 1. Groq: openai/gpt-oss-120b ($0.15 input / $0.60 output per 1M tokens)
    # Hand calculation for 1,000 prompt tokens and 500 completion tokens:
    # 1,000 * (0.15 / 1,000,000) = 0.000150 USD
    #   500 * (0.60 / 1,000,000) = 0.000300 USD
    # Total = 0.000450 USD
    groq_cost = token_tracker.calculate_cost("groq", "openai/gpt-oss-120b", 1000, 500)
    assert groq_cost == 0.000450

    # 2. Gemini: gemini-3.8-flash ($0.075 input / $0.30 output per 1M tokens)
    # Hand calculation for 2,000 prompt tokens and 1,000 completion tokens:
    # 2,000 * (0.075 / 1,000,000) = 0.000150 USD
    # 1,000 * (0.300 / 1,000,000) = 0.000300 USD
    # Total = 0.000450 USD
    gemini_cost = token_tracker.calculate_cost("gemini", "gemini-3.8-flash", 2000, 1000)
    assert gemini_cost == 0.000450

    # 3. Gemini: gemini-1.5-pro ($1.25 input / $5.00 output per 1M tokens)
    # Hand calculation for 10,000 prompt tokens and 2,000 completion tokens:
    # 10,000 * (1.25 / 1,000,000) = 0.012500 USD
    #  2,000 * (5.00 / 1,000,000) = 0.010000 USD
    # Total = 0.022500 USD
    gemini_pro_cost = token_tracker.calculate_cost("gemini", "gemini-1.5-pro", 10000, 2000)
    assert gemini_pro_cost == 0.022500

    # 4. OpenRouter free tier: meta-llama/llama-3.3-70b-instruct:free ($0 input / $0 output)
    or_cost = token_tracker.calculate_cost("openrouter", "meta-llama/llama-3.3-70b-instruct:free", 5000, 2000)
    assert or_cost == 0.0

    # 5. OpenAI: gpt-4o-mini ($0.15 input / $0.60 output per 1M tokens)
    openai_cost = token_tracker.calculate_cost("openai", "gpt-4o-mini", 10000, 5000)
    # 10,000 * 0.00000015 = 0.001500
    #  5,000 * 0.00000060 = 0.003000
    # Total = 0.004500 USD
    assert openai_cost == 0.004500


# ── 2. LLM Usage Log Database Persistence ─────────────────────────────────────

def test_llm_usage_logged_to_database(test_db, isolated_tracker):
    """
    Verifies that calling record_usage correctly persists all fields into llm_usage_logs.
    """
    res = isolated_tracker.record_usage(
        provider="groq",
        model="openai/gpt-oss-120b",
        input_tokens=1200,
        output_tokens=400,
        clip_id="clip-test-1",
        video_id="video-test-1",
        stage="select",
        job_id="job-test-1",
        key_index=2,
        key_masked="gsk_abc***xyz",
        db=test_db,
    )

    assert res["cost_usd"] > 0
    assert res["key_index"] == 2
    assert res["job_id"] == "job-test-1"

    # Query DB record directly
    db_record = test_db.query(LLMUsageLog).filter(LLMUsageLog.job_id == "job-test-1").first()
    assert db_record is not None
    assert db_record.provider == "groq"
    assert db_record.model == "openai/gpt-oss-120b"
    assert db_record.prompt_tokens == 1200
    assert db_record.completion_tokens == 400
    assert db_record.total_tokens == 1600
    assert db_record.key_index == 2
    assert db_record.key_masked == "gsk_abc***xyz"
    assert db_record.video_id == "video-test-1"
    assert db_record.clip_id == "clip-test-1"
    assert db_record.stage == "select"
    assert db_record.cost_usd == res["cost_usd"]


# ── 3. Queryable Rollups (Per Video, Per Day, Per Provider) ───────────────────

def test_queryable_rollups(test_db, isolated_tracker):
    """
    Verifies rollups queryable per video, per day, and per provider.
    """
    # Record multiple usages
    isolated_tracker.record_usage(
        provider="groq",
        model="openai/gpt-oss-120b",
        input_tokens=1000,
        output_tokens=500,
        video_id="video-A",
        db=test_db,
    )
    isolated_tracker.record_usage(
        provider="groq",
        model="openai/gpt-oss-120b",
        input_tokens=1000,
        output_tokens=500,
        video_id="video-A",
        db=test_db,
    )
    isolated_tracker.record_usage(
        provider="gemini",
        model="gemini-3.8-flash",
        input_tokens=2000,
        output_tokens=1000,
        video_id="video-B",
        db=test_db,
    )

    # 1. Rollup per video
    cost_a = isolated_tracker.get_cost_per_video("video-A", db=test_db)
    cost_b = isolated_tracker.get_cost_per_video("video-B", db=test_db)
    # Video A: 2 calls * 0.000450 = 0.000900
    # Video B: 1 call * 0.000450 = 0.000450
    assert round(cost_a, 6) == 0.000900
    assert round(cost_b, 6) == 0.000450

    video_rollups = isolated_tracker.get_rollups_by_video(db=test_db)
    assert len(video_rollups) == 2
    assert video_rollups[0]["video_id"] == "video-A"
    assert video_rollups[0]["total_calls"] == 2
    assert video_rollups[0]["cost_usd"] == 0.000900
    assert video_rollups[1]["video_id"] == "video-B"
    assert video_rollups[1]["total_calls"] == 1

    # 2. Rollup per provider
    provider_rollups = isolated_tracker.get_rollups_by_provider(db=test_db)
    assert len(provider_rollups) == 2
    groq_p = next(p for p in provider_rollups if p["provider"] == "groq")
    gemini_p = next(p for p in provider_rollups if p["provider"] == "gemini")
    assert groq_p["total_calls"] == 2
    assert gemini_p["total_calls"] == 1

    # 3. Daily cost and rollups
    cost_today = isolated_tracker.get_cost_today(db=test_db)
    assert round(cost_today, 6) == 0.001350

    daily_rollups = isolated_tracker.get_daily_rollups(days=7, db=test_db)
    assert len(daily_rollups) >= 1
    assert daily_rollups[0]["total_calls"] == 3


# ── 4. Daily Budget Brake & Job Deferral ───────────────────────────────────────

def test_daily_budget_brake_exceeded_refuses_call(monkeypatch, test_db, isolated_tracker):
    """
    Verifies that when daily budget is exceeded, router refuses calls and raises DailyBudgetExceededError.
    """
    monkeypatch.setattr(settings, "LLM_DAILY_BUDGET_USD", 0.001)

    # Record usage that exceeds $0.001 limit
    isolated_tracker.record_usage(
        provider="groq",
        model="openai/gpt-oss-120b",
        input_tokens=10000,
        output_tokens=5000,
        db=test_db,
    )
    # 10,000 * 0.15/1M + 5,000 * 0.60/1M = 0.001500 + 0.003000 = 0.004500 > 0.001000

    # Budget is exceeded
    with patch("shorts_engine.services.llm.router.token_tracker", isolated_tracker):
        assert isolated_tracker.is_daily_budget_exceeded(db=test_db)

        router = LLMRouter(groq_api_keys=["gsk-mock-key"])
        with pytest.raises(DailyBudgetExceededError) as exc_info:
            router.generate_structured(
                prompt="Select clips",
                schema=DummySchema,
            )

        assert "Daily LLM budget of $0.00 exceeded" in str(exc_info.value) or "exceeded" in str(exc_info.value)


def test_stage_executor_defers_job_on_budget_exceeded(monkeypatch, test_db):
    """
    Verifies that when DailyBudgetExceededError occurs during stage execution,
    the job is deferred until tomorrow 00:00:00 UTC without burning attempts,
    and a budget_exceeded event and alert are emitted.
    """
    job = PipelineJob(
        id="job-budget-defer-1",
        job_type="PROCESS_VIDEO",
        entity_type="source_video",
        entity_id="video-1",
        stage="select",
        status=PipelineJobStatus.PENDING,
        attempt_count=0,
    )
    test_db.add(job)
    test_db.commit()

    ctx = StageContext(
        db_session=test_db,
        video_id="video-1",
        job_id=job.id,
    )

    def failing_stage(c: StageContext):
        raise DailyBudgetExceededError("Daily LLM budget of $5.00 exceeded.")

    test_pipeline = {
        "select": Stage(name="select", callable=failing_stage, max_attempts=3),
    }

    executor = StageExecutor(pipeline=test_pipeline)

    events_emitted = []
    alerts_emitted = []

    def mock_emit_platform_event(event_name, **kwargs):
        events_emitted.append((event_name, kwargs))

    def mock_alert(*args, **kwargs):
        alerts_emitted.append((args, kwargs))
        return True

    with patch("shorts_engine.services.events.taxonomy.emit_platform_event", side_effect=mock_emit_platform_event):
        with patch("shorts_engine.services.monitoring.alerter.pipeline_alerter.alert", side_effect=mock_alert):
            with pytest.raises(DailyBudgetExceededError):
                executor.execute_stage("select", ctx, job=job)

    # Job must be deferred to tomorrow 00:00:00 UTC
    test_db.refresh(job)
    assert job.status == PipelineJobStatus.PENDING
    assert job.attempt_count == 0  # Did not burn a retry attempt!
    assert job.next_run_at is not None
    now = datetime.utcnow()
    assert job.next_run_at.date() > now.date()
    assert job.next_run_at.hour == 0 and job.next_run_at.minute == 0
    assert "Daily LLM budget of $5.00 exceeded" in job.last_error

    # Verify platform event and alert were emitted
    assert any(ev[0] == BUDGET_EXCEEDED for ev in events_emitted)
    assert len(alerts_emitted) >= 1


# ── 5. Per-Key 429 Cooldown & Intra-Pool Failover ─────────────────────────────

def test_per_key_429_cooldown_and_pool_failover(tmp_path):
    """
    Verifies that when a key returns HTTP 429, it enters cooldown and the router
    immediately tries the next key in the pool, recording the correct key_index.
    """
    tracker_file = tmp_path / "usage_tracker.json"
    custom_tracker = APIUsageTracker(storage_path=tracker_file)

    router = LLMRouter(
        groq_api_keys=["gsk-key-primary-1", "gsk-key-secondary-2"],
        tracker=custom_tracker,
    )

    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429
    mock_resp_429.headers = {"Retry-After": "30"}
    mock_resp_429.text = "Rate limit exceeded"

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.headers = {}
    mock_resp_200.json.return_value = {
        "choices": [{
            "message": {"content": json.dumps({"title": "Awesome Short", "score": 9.5})}
        }],
        "usage": {"prompt_tokens": 500, "completion_tokens": 100},
    }

    # Simulate key-1 failing with 429, key-2 succeeding with 200
    call_counts = {"count": 0}

    def mock_post(url, headers, json):
        auth = headers.get("Authorization", "")
        call_counts["count"] += 1
        if "gsk-key-primary-1" in auth:
            return mock_resp_429
        return mock_resp_200

    with patch("httpx.Client.post", side_effect=mock_post):
        res, meta = router.generate_structured(
            prompt="Make short",
            schema=DummySchema,
        )

    assert res is not None
    assert res.title == "Awesome Short"
    # Key-1 must now be in cooldown
    assert custom_tracker.is_cooling_down("groq", "gsk-key-primary-1")
    # Active key used was key-2
    assert "gsk-key-secondary-2"[:6] in meta["key_masked"]
    assert meta["key_index"] == 1


# ── 6. Visibility Dashboard API Endpoint ──────────────────────────────────────

def test_visibility_cost_api_endpoint(test_db, isolated_tracker):
    """
    Verifies that /api/visibility/cost returns current spend, budget, and rollups.
    """
    # Insert test data
    isolated_tracker.record_usage(
        provider="groq",
        model="openai/gpt-oss-120b",
        input_tokens=1000,
        output_tokens=500,
        video_id="video-vis-1",
        db=test_db,
    )

    app = create_dashboard_app()
    client = TestClient(app)

    # Authenticate as viewer
    dummy_user = User(
        id="user-1",
        username="operator",
        password_hash="fake",
        role=UserRole.OPERATOR,
        is_active=True,
    )
    app.dependency_overrides[require_viewer] = lambda: dummy_user

    # Test /api/visibility/cost
    resp = client.get("/api/visibility/cost")
    assert resp.status_code == 200
    data = resp.json()
    assert "cost_today_usd" in data
    assert "daily_budget_usd" in data
    assert "budget_used_pct" in data
    assert "rollups_by_provider" in data
    assert "rollups_by_video" in data

    # Test /api/visibility/overview contains 'cost'
    resp_overview = client.get("/api/visibility/overview")
    assert resp_overview.status_code == 200
    overview_data = resp_overview.json()
    assert "cost" in overview_data
    assert "cost_today_usd" in overview_data["cost"]
