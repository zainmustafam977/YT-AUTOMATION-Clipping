"""
Unit and integration tests for Multi-Key LLM Routing, API Quota Tracker, and Preflight Status.
"""

import json
import time
from unittest.mock import MagicMock, patch
import pytest
from pydantic import BaseModel, Field
from fastapi.testclient import TestClient

from shorts_engine.config.settings import PipelineSettings
from shorts_engine.services.llm.quota_tracker import APIUsageTracker
from shorts_engine.services.llm.router import LLMRouter
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.auth import get_current_user, require_viewer
from shorts_engine.db.models import (
    User,
    UserRole,
    Channel,
    SourceVideo,
    SourceVideoStatus,
    ClipCandidate,
    Clip,
    ClipStatus,
)


class DummySchema(BaseModel):
    summary: str = Field(description="Summary")
    score: float = Field(description="Score")


# ── 1. Settings Multi-Key Normalization Tests ─────────────────────────────────

def test_settings_multi_key_normalization():
    s = PipelineSettings(
        GEMINI_API_KEY="key-gem-single",
        GEMINI_API_KEYS=["key-gem-1", "key-gem-2"],
        GROQ_API_KEY="groq-single",
        GROQ_API_KEYS=["groq-1", "groq-2", "groq-single"],  # deduplication check
        OPENROUTER_API_KEY="or-single",
        OPENROUTER_API_KEYS=[],
        OPENAI_API_KEY="",
        OPENAI_API_KEYS=["openai-1"],
    )

    assert s.get_gemini_keys() == ["key-gem-1", "key-gem-2", "key-gem-single"]
    assert s.get_groq_keys() == ["groq-1", "groq-2", "groq-single"]
    assert s.get_openrouter_keys() == ["or-single"]
    assert s.get_openai_keys() == ["openai-1"]


# ── 2. APIUsageTracker Tests ───────────────────────────────────────────────────

def test_api_usage_tracker_record_and_cooldown(tmp_path):
    storage_file = tmp_path / "test_api_key_usage.json"
    tracker = APIUsageTracker(storage_path=storage_file)

    test_key = "gsk_testkey1234567890abcdef"
    provider = "groq"

    # Initial state
    assert not tracker.is_key_cooling_down(provider, test_key)

    # Record 2 successful requests
    tracker.record_request(provider, test_key, latency_ms=120.0)
    tracker.record_request(provider, test_key, latency_ms=80.0)

    metrics = tracker.get_per_key_metrics()
    assert len(metrics) >= 1
    groq_metric = next((m for m in metrics if m["provider"] == "groq" and m["key_masked"].endswith("cdef")), None)
    assert groq_metric is not None
    assert groq_metric["requests_today"] == 2
    assert groq_metric["all_time_requests"] == 2
    assert groq_metric["avg_latency_ms"] == 80.0  # last latency is 80.0

    # Record 429 rate limit
    tracker.record_rate_limit(provider, test_key, cooldown_seconds=10)
    assert tracker.is_key_cooling_down(provider, test_key)

    metrics = tracker.get_per_key_metrics()
    groq_metric = next(m for m in metrics if m["provider"] == "groq" and m["key_masked"].endswith("cdef"))
    assert groq_metric["status"] == "COOLDOWN"
    assert groq_metric["rate_limit_hits"] == 1

    # Summary
    summary = tracker.get_summary()
    assert summary["total_requests_today"] >= 2


# ── 3. LLMRouter Multi-Key Round-Robin & Failover Tests ────────────────────────

def test_router_multi_key_round_robin():
    groq_keys = ["gsk_key_alpha_1111111111", "gsk_key_beta_2222222222"]
    router = LLMRouter(
        groq_api_keys=groq_keys,
        provider="groq",
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"summary": "RoundRobin Test", "score": 9.5})}}]
    }

    used_auth_headers = []

    def mock_post(*args, **kwargs):
        headers = kwargs.get("headers", {})
        used_auth_headers.append(headers.get("Authorization"))
        return mock_resp

    with patch("httpx.Client.post", side_effect=mock_post):
        # Call 1
        res1, meta1 = router.generate_structured("Prompt 1", DummySchema)
        assert res1.summary == "RoundRobin Test"

        # Call 2
        res2, meta2 = router.generate_structured("Prompt 2", DummySchema)
        assert res2.summary == "RoundRobin Test"

    # Check that both keys were used in round-robin fashion
    assert used_auth_headers == [
        "Bearer gsk_key_alpha_1111111111",
        "Bearer gsk_key_beta_2222222222",
    ]


def test_router_intra_provider_429_failover(tmp_path):
    groq_keys = ["gsk_key_failing_111111111", "gsk_key_working_222222222"]
    tracker = APIUsageTracker(storage_path=tmp_path / "test_usage.json")

    router = LLMRouter(
        groq_api_keys=groq_keys,
        provider="groq",
        tracker=tracker,
    )

    # First key gets 429, second key returns 200
    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429
    mock_resp_429.text = "Rate limit exceeded: 429 Too Many Requests"

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"summary": "Second Key Recovered", "score": 9.9})}}]
    }

    def mock_post(*args, **kwargs):
        auth = kwargs.get("headers", {}).get("Authorization", "")
        if "gsk_key_failing" in auth:
            return mock_resp_429
        return mock_resp_200

    with patch("httpx.Client.post", side_effect=mock_post):
        res, meta = router.generate_structured("Test 429 failover", DummySchema)

        assert res is not None
        assert res.summary == "Second Key Recovered"
        assert meta["provider"] == "groq"
        # Failing key should now be in cooldown
        assert tracker.is_key_cooling_down("groq", "gsk_key_failing_111111111")
        # Working key is not in cooldown
        assert not tracker.is_key_cooling_down("groq", "gsk_key_working_222222222")


# ── 4. Dashboard Video Status Pre-Flight API Tests ────────────────────────────

def test_video_status_preflight_endpoint(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from shorts_engine.db.session import Base

    test_db = f"sqlite:///{tmp_path}/test_status.db"
    engine = create_engine(test_db)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Pre-populate channel and source video
    db = TestingSessionLocal()
    chan = Channel(
        id="chan_test_01",
        youtube_channel_id="UC_TEST123",
        name="Tech Channel",
    )
    db.add(chan)
    db.flush()

    v = SourceVideo(
        id="sv_test_01",
        channel_id=chan.id,
        youtube_video_id="tphyf6Y3OcY",
        title="Existing Processed Video Title",
        status=SourceVideoStatus.DONE,
        duration_sec=360,
    )
    db.add(v)
    db.flush()

    cand = ClipCandidate(
        id="cand_test_01",
        source_video_id=v.id,
        start_ms=1000,
        end_ms=30000,
        reason="High engagement",
        hook_text="Hook text",
        score=9.0,
    )
    db.add(cand)
    db.commit()
    db.close()

    app = create_dashboard_app()

    mock_user = User(
        id="user_admin",
        username="admin",
        password_hash="x",
        role=UserRole.ADMIN,
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[require_viewer] = lambda: mock_user

    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    from shorts_engine.db.session import get_db
    app.dependency_overrides[get_db] = override_get_db

    client = TestClient(app)

    # 1. Check existing video
    res_found = client.get("/api/video-status/tphyf6Y3OcY")
    assert res_found.status_code == 200
    data = res_found.json()
    assert data["exists"] is True
    assert data["status"] == "DONE"
    assert data["title"] == "Existing Processed Video Title"
    assert data["clips_count"] == 1
    assert data["channel_name"] == "Tech Channel"

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_oembed = MagicMock()
    mock_oembed.status_code = 200
    mock_oembed.json.return_value = {"title": "Unseen Video", "author_name": "Author"}
    mock_client.get.return_value = mock_oembed
    with patch("shorts_engine.dashboard.routes.status.httpx.Client", return_value=mock_client):
        res_not_found = client.get("/api/video-status/HErzoaoos_s")
        assert res_not_found.status_code == 200
        data_not = res_not_found.json()
        assert data_not["exists"] is False


def test_settings_key_management_methods():
    from shorts_engine.services.llm.quota_tracker import hash_api_key
    from shorts_engine.config.settings import settings

    test_key = "gsk_custom_test_unique_key_9999"
    # 1. Add key
    added = settings.add_api_key("groq", test_key)
    assert added is True
    assert test_key in settings.get_groq_keys()

    # 2. Duplicate rejection
    duplicate = settings.add_api_key("groq", test_key)
    assert duplicate is False

    # 3. Remove key
    k_hash = hash_api_key(test_key)[:16]
    removed = settings.remove_api_key("groq", k_hash)
    assert removed == test_key
    assert test_key not in settings.get_groq_keys()


def test_settings_keys_endpoints():
    from shorts_engine.services.llm.quota_tracker import hash_api_key
    from shorts_engine.dashboard.auth import require_operator, verify_csrf
    import httpx

    app = create_dashboard_app()
    mock_op = User(
        id="user_op",
        username="test_operator",
        password_hash="x",
        role=UserRole.OPERATOR,
        is_active=True,
    )
    app.dependency_overrides[require_operator] = lambda: mock_op
    app.dependency_overrides[require_viewer] = lambda: mock_op
    app.dependency_overrides[verify_csrf] = lambda: True

    client = TestClient(app)

    new_key = "sk-or-v1-test-interactive-add-999"
    # 1. Add key endpoint
    resp_add = client.post(
        "/settings/keys/add",
        data={"provider": "openrouter", "new_key": new_key, "csrf_token": "dummy"},
        headers={"Accept": "application/json"},
    )
    assert resp_add.status_code == 200
    assert resp_add.json()["status"] == "success"

    # 2. Test key endpoint with mock ping
    k_hash = hash_api_key(new_key)[:16]
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        resp_test = client.post(
            "/settings/keys/test",
            data={"provider": "openrouter", "key_hash": k_hash, "csrf_token": "dummy"},
            headers={"Accept": "application/json"},
        )
        assert resp_test.status_code == 200
        data = resp_test.json()
        assert data["status"] == "success"
        assert data["active"] is True

    # 3. Delete key endpoint
    resp_del = client.post(
        "/settings/keys/delete",
        data={"provider": "openrouter", "key_hash": k_hash, "csrf_token": "dummy"},
        headers={"Accept": "application/json"},
    )
    assert resp_del.status_code == 200
    assert resp_del.json()["status"] == "success"


# ── 6. Live Telemetry & Error Classification Tests ─────────────────────────────

def test_live_in_flight_telemetry_and_failover(tmp_path):
    tracker = APIUsageTracker(storage_path=tmp_path / "test_tracker.json")
    
    # 1. Start in-flight call
    cid = tracker.set_active_call("groq", "openai/gpt-oss-120b", "key_test_123")
    assert cid is not None
    
    telemetry = tracker.get_live_telemetry()
    assert telemetry["in_flight_count"] == 1
    assert telemetry["active_calls"][0]["provider"] == "groq"
    assert telemetry["active_calls"][0]["model"] == "openai/gpt-oss-120b"
    
    # 2. Record failover event
    tracker.record_failover_event(
        from_provider="groq",
        to_provider="gemini",
        reason="HTTP 429 quota exhausted",
        video_id="test_vid_failover",
    )
    
    telemetry = tracker.get_live_telemetry()
    assert len(telemetry["recent_failovers"]) == 1
    assert telemetry["recent_failovers"][0]["from"] == "groq"
    assert telemetry["recent_failovers"][0]["to"] == "gemini"
    
    # 3. Clear in-flight call
    tracker.clear_active_call(cid)
    telemetry_after = tracker.get_live_telemetry()
    assert telemetry_after["in_flight_count"] == 0


def test_router_error_classification_and_retry_delay():
    router = LLMRouter(provider="auto")
    
    # 1. Error classification
    assert router._classify_error(404, "model_not_found") == "PERMANENT"
    assert router._classify_error(401, "invalid_api_key") == "PERMANENT"
    assert router._classify_error(400, "bad_request") == "PERMANENT"
    assert router._classify_error(429, "Rate limit exceeded") == "TRANSIENT"
    assert router._classify_error(503, "Service unavailable") == "TRANSIENT"
    assert router._classify_error(500, "Internal error") == "TRANSIENT"
    
    # 2. Retry delay parsing
    body_with_seconds = '{"error": {"message": "Resource exhausted. Please retry in 55.8s."}}'
    delay = router._extract_retry_delay(body_with_seconds, default_delay=60.0)
    assert delay == 55.8
    
    body_with_retry_after = '{"error": "Quota limit reached, retry after 42 seconds"}'
    delay2 = router._extract_retry_delay(body_with_retry_after, default_delay=60.0)
    assert delay2 == 42.0
    
    body_fallback = '{"error": "Too many requests"}'
    delay3 = router._extract_retry_delay(body_fallback, default_delay=45.0)
    assert delay3 == 45.0


def test_video_status_oembed_validation(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from shorts_engine.db.session import Base
    from shorts_engine.db.session import get_db

    test_db = f"sqlite:///{tmp_path}/test_oembed.db"
    engine = create_engine(test_db)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app = create_dashboard_app()
    mock_user = User(
        id="user_admin",
        username="admin",
        password_hash="x",
        role=UserRole.ADMIN,
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[require_viewer] = lambda: mock_user

    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # 1. Invalid ID syntax (less than 11 chars or illegal chars)
    resp_invalid = client.get("/api/video-status/short")
    assert resp_invalid.status_code == 200
    data_inv = resp_invalid.json()
    assert data_inv["is_valid"] is False
    assert "Invalid YouTube" in data_inv["error"]

    # 2. Valid 11-char ID with mocked successful oEmbed response
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_oembed = MagicMock()
    mock_oembed.status_code = 200
    mock_oembed.json.return_value = {
        "title": "Mocked YouTube Video Title",
        "author_name": "Mocked Creator",
        "thumbnail_url": "https://i.ytimg.com/vi/11111111111/hqdefault.jpg",
    }
    mock_client.get.return_value = mock_oembed
    with patch("shorts_engine.dashboard.routes.status.httpx.Client", return_value=mock_client):
        resp_valid = client.get("/api/video-status/11111111111")
        assert resp_valid.status_code == 200
        data_val = resp_valid.json()
        assert data_val["is_valid"] is True
        assert data_val["exists"] is False
        assert data_val["title"] == "Mocked YouTube Video Title"
        assert data_val["author"] == "Mocked Creator"


