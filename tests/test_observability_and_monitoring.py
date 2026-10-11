"""
Comprehensive Test Suite for Package 4: System Observability & Monitoring.
Validates:
  1. Standardized Health & Readiness Probes (/api/health, /api/readiness)
  2. Failure status delegation (503 on DB, disk headroom < 1GB, missing FFmpeg)
  3. Unauthenticated monitoring whitelist
  4. YouTube API quota utilization & proactive 85% alerts with daily deduplication
  5. AI Token & Cost Tracker per-clip, per-provider, and daily aggregation
  6. Prometheus & OpenMetrics telemetry exposition
"""

import os
import json
import time
from unittest.mock import patch, MagicMock
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.services.publishing.quota import QuotaTracker
from shorts_engine.services.monitoring.token_tracker import (
    AITokenCostTracker,
    DEFAULT_TOKEN_PRICING,
)


@pytest.fixture
def dashboard_client():
    app = create_dashboard_app()
    return TestClient(app)


# ── 1. Standardized Health & Readiness Probes ───────────────────────────────

def test_api_health_endpoint_healthy(dashboard_client):
    """GET /api/health returns 200 OK with system status, uptime, and component statuses."""
    res = dashboard_client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] in ("healthy", "unhealthy")
    assert "uptime_seconds" in data
    assert "checks" in data
    assert "database" in data["checks"]
    assert "storage" in data["checks"]
    assert "media_tools" in data["checks"]


def test_api_readiness_endpoint_deep_checks(dashboard_client):
    """GET /api/readiness returns detailed latency, storage headroom, and tool paths."""
    res = dashboard_client.get("/api/readiness")
    assert res.status_code in (200, 503)
    data = res.json()
    assert "is_ready" in data
    assert "checks" in data
    assert "database" in data["checks"]
    assert "storage" in data["checks"]
    assert "media_tools" in data["checks"]
    assert "resources" in data["checks"]


def test_readiness_probe_database_failure(dashboard_client):
    """Readiness probe returns 503 SERVICE UNAVAILABLE when database query fails."""
    with patch("shorts_engine.dashboard.routes.health.SessionLocal") as mock_session:
        mock_db = MagicMock()
        mock_db.execute.side_effect = RuntimeError("Database connection pool exhausted")
        mock_session.return_value.__enter__.return_value = mock_db

        res = dashboard_client.get("/api/readiness")
        assert res.status_code == 503
        data = res.json()
        assert data["is_ready"] is False
        assert data["checks"]["database"]["status"] == "unhealthy"


def test_readiness_probe_disk_space_critical(dashboard_client):
    """Readiness probe returns 503 when disk space drops below critical threshold (1.0 GB)."""
    mock_disk = MagicMock()
    mock_disk.free = int(0.5 * 1024 ** 3)  # 500 MB remaining
    mock_disk.total = int(100 * 1024 ** 3)
    mock_disk.percent = 99.5

    with patch("psutil.disk_usage", return_value=mock_disk):
        res = dashboard_client.get("/api/readiness")
        assert res.status_code == 503
        data = res.json()
        assert data["is_ready"] is False
        assert data["checks"]["storage"]["status"] == "critical"
        assert any("critically low" in w for w in data.get("warnings", []))


def test_readiness_probe_missing_ffmpeg(dashboard_client):
    """Readiness probe returns 503 when FFmpeg or FFprobe binaries are missing from PATH."""
    with patch("shutil.which", return_value=None):
        res = dashboard_client.get("/api/readiness")
        assert res.status_code == 503
        data = res.json()
        assert data["is_ready"] is False
        assert data["checks"]["media_tools"]["status"] == "unhealthy"


def test_unauthenticated_monitoring_whitelist(dashboard_client):
    """External scrapers requesting HTML or JSON at health/metrics endpoints are never redirected to /login."""
    for path in ["/api/health", "/api/readiness", "/api/metrics/ai-tokens", "/metrics", "/livez", "/readyz", "/healthz"]:
        res = dashboard_client.get(path, headers={"accept": "text/html,application/json"})
        assert res.status_code != 303, f"{path} should not redirect to login"
        assert res.status_code != 401, f"{path} should be publicly reachable for observability"


# ── 2. YouTube API Quota Utilization & Proactive Alerts ─────────────────────

def test_youtube_quota_threshold_alert_85_percent(tmp_path):
    """Verifies that QuotaTracker proactively alerts at >= 85% and enforces Pacific-day deduplication."""
    QuotaTracker.reset_alert_cooldown()

    state_file = tmp_path / "test_quota_state.json"
    tracker = QuotaTracker(state_file=state_file)

    # 1. Below threshold (5,000 units = 50%): No alert
    with patch.object(tracker, "get_used_units", return_value=5000):
        with patch("shorts_engine.services.monitoring.alerter.PipelineAlerter.alert") as mock_alert:
            res = tracker.check_and_alert_quota()
            assert res["alert_sent"] is False
            mock_alert.assert_not_called()

    # 2. At 85% threshold (8,500 units): Triggers WARNING alert
    with patch.object(tracker, "get_used_units", return_value=8500):
        with patch("shorts_engine.services.monitoring.alerter.PipelineAlerter.alert", return_value=True) as mock_alert:
            res = tracker.check_and_alert_quota()
            assert res["alert_sent"] is True
            assert res["level"] == "WARNING"
            mock_alert.assert_called_once()
            call_kwargs = mock_alert.call_args[1]
            assert "YouTube Quota Warning" in mock_alert.call_args[1]["title"]
            assert call_kwargs["level"] == "WARNING"
            assert call_kwargs["context"]["pct"] == 85.0

    # 3. Same day repeated call: Deduplicated, no spam
    with patch.object(tracker, "get_used_units", return_value=8600):
        with patch("shorts_engine.services.monitoring.alerter.PipelineAlerter.alert") as mock_alert:
            res = tracker.check_and_alert_quota()
            assert res["alert_sent"] is False
            assert res.get("deduplicated") is True
            mock_alert.assert_not_called()

    # 4. Escalates to 100% (quota exhaustion): Triggers CRITICAL alert
    with patch.object(tracker, "get_used_units", return_value=10000):
        with patch("shorts_engine.services.monitoring.alerter.PipelineAlerter.alert", return_value=True) as mock_alert:
            res = tracker.check_and_alert_quota()
            assert res["alert_sent"] is True
            assert res["level"] == "CRITICAL"
            mock_alert.assert_called_once()
            assert mock_alert.call_args[1]["level"] == "CRITICAL"


def test_quota_tracker_record_publish_triggers_alert(tmp_path):
    """Calling record_publish() triggers quota alert evaluation automatically."""
    QuotaTracker.reset_alert_cooldown()
    state_file = tmp_path / "quota_alert_test.json"
    tracker = QuotaTracker(state_file=state_file)

    with patch.object(tracker, "record_usage"):
        with patch.object(tracker, "get_used_units", return_value=9000):
            with patch("shorts_engine.services.monitoring.alerter.PipelineAlerter.alert", return_value=True) as mock_alert:
                tracker.record_publish(cost=1600)
                mock_alert.assert_called_once()
                assert mock_alert.call_args[1]["level"] == "WARNING"


# ── 3. AI Token & Cost Tracking ─────────────────────────────────────────────

def test_token_tracker_cost_calculation(tmp_path):
    """Verifies token tracker pricing calculation per provider according to pricing model."""
    tracker = AITokenCostTracker(state_file=tmp_path / "pricing_test.json")

    # Gemini: $0.075 / 1M in, $0.30 / 1M out
    # 10,000 input = $0.00075, 2,000 output = $0.00060 -> Total $0.00135
    cost_gemini = tracker.calculate_cost("gemini", "gemini-3.8-flash", 10000, 2000)
    assert cost_gemini == 0.00135

    # OpenAI: $0.15 / 1M in, $0.60 / 1M out
    # 1,000 input = $0.00015, 500 output = $0.00030 -> Total $0.00045
    cost_openai = tracker.calculate_cost("openai", "gpt-4o-mini", 1000, 500)
    assert cost_openai == 0.00045

    # Groq: Free tier default or $0.05 / 1M in, $0.08 / 1M out
    cost_groq = tracker.calculate_cost("groq", "openai/gpt-oss-120b", 5000, 1000)
    assert cost_groq >= 0.0


def test_token_tracker_clip_aggregation_and_summary(tmp_path):
    """Records multiple AI calls across providers and verifies per-clip and global aggregation."""
    tracker = AITokenCostTracker(state_file=tmp_path / "token_agg_test.json")
    tracker.reset_for_testing()

    # Clip 1: Groq hook analysis + Gemini captioning
    tracker.record_usage(
        provider="groq",
        model="openai/gpt-oss-120b",
        input_tokens=2000,
        output_tokens=400,
        clip_id="clip_001",
        video_id="vid_100",
        stage="analysis",
    )
    tracker.record_usage(
        provider="gemini",
        model="gemini-3.8-flash",
        input_tokens=5000,
        output_tokens=1000,
        clip_id="clip_001",
        video_id="vid_100",
        stage="captioning",
    )

    # Clip 2: OpenAI thumbnail generation
    tracker.record_usage(
        provider="openai",
        model="gpt-4o-mini",
        input_tokens=3000,
        output_tokens=600,
        clip_id="clip_002",
        video_id="vid_200",
        stage="thumbnail",
    )

    # 1. Per-clip verification for clip_001
    clip_1_metrics = tracker.get_clip_cost("clip_001")
    assert clip_1_metrics["clip_id"] == "clip_001"
    assert clip_1_metrics["total_tokens"] == (2400 + 6000)
    assert clip_1_metrics["input_tokens"] == 7000
    assert clip_1_metrics["output_tokens"] == 1400
    assert clip_1_metrics["total_calls"] == 2
    assert clip_1_metrics["estimated_cost_usd"] > 0.0
    assert len(clip_1_metrics["calls"]) == 2

    # 2. Global summary verification
    summary = tracker.get_summary()
    assert summary["totals"]["total_calls"] == 3
    assert summary["totals"]["total_tokens"] == (2400 + 6000 + 3600)
    assert "groq" in summary["providers"]
    assert "gemini" in summary["providers"]
    assert "openai" in summary["providers"]
    assert summary["total_tracked_clips"] == 2


def test_token_tracker_persistence_and_reload(tmp_path):
    """Verifies that atomic persistence preserves all tracked tokens across process restarts."""
    state_file = tmp_path / "persistent_tokens.json"
    tracker1 = AITokenCostTracker(state_file=state_file)
    tracker1.reset_for_testing()

    tracker1.record_usage(
        provider="gemini",
        model="gemini-3.8-flash",
        input_tokens=15000,
        output_tokens=3500,
        clip_id="clip_restart_test",
    )

    assert state_file.exists()

    # Re-instantiate a new tracker reading the same file
    tracker2 = AITokenCostTracker(state_file=state_file)
    clip_data = tracker2.get_clip_cost("clip_restart_test")
    assert clip_data["total_tokens"] == 18500
    assert clip_data["estimated_cost_usd"] > 0.0


# ── 4. API & Prometheus Metrics Telemetry Routes ────────────────────────────

def test_api_metrics_ai_tokens_json(dashboard_client, tmp_path):
    """GET /api/metrics/ai-tokens returns structured JSON token summary."""
    res = dashboard_client.get("/api/metrics/ai-tokens")
    assert res.status_code == 200
    data = res.json()
    assert "totals" in data
    assert "providers" in data
    assert "pricing_tiers" in data


def test_api_metrics_clip_ai_tokens_json(dashboard_client):
    """GET /api/metrics/clips/{clip_id}/ai-tokens returns per-clip token cost breakdown."""
    from shorts_engine.dashboard.auth import require_viewer
    from shorts_engine.db.models import User, UserRole
    fake_user = User(id="u1", username="viewer", role=UserRole.VIEWER, is_active=True)
    dashboard_client.app.dependency_overrides[require_viewer] = lambda: fake_user
    try:
        res = dashboard_client.get("/api/metrics/clips/nonexistent_clip/ai-tokens")
        assert res.status_code == 200
        data = res.json()
        assert data["clip_id"] == "nonexistent_clip"
        assert data["total_tokens"] == 0
    finally:
        dashboard_client.app.dependency_overrides.pop(require_viewer, None)


def test_prometheus_metrics_exposition_contains_quota_and_ai(dashboard_client):
    """GET /metrics text output includes YouTube quota gauges and AI token cost metrics."""
    res = dashboard_client.get("/metrics")
    assert res.status_code == 200
    body = res.text
    assert "shorts_youtube_quota_used_units" in body
    assert "shorts_youtube_quota_daily_limit" in body
    assert "shorts_ai_tokens_total" in body
    assert "shorts_ai_cost_usd_total" in body
