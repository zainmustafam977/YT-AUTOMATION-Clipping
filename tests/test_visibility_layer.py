"""
Acceptance Tests for System Visibility Layer and Operational Webhook Alerts.

Validates:
1. Metric 1: Queue depth per stage and oldest pending job age
2. Metric 2: Active running jobs with lock details and heartbeat
3. Metric 3: Quarantined dead-letter jobs with failure error details
4. Metric 4: Channel token health and last successful publish
5. Metric 5: Quota units remaining today from Pacific-aware quota ledger
6. Metric 6: Media root storage capacity and disk usage
7. Metric 7: Jobs succeeded and failed in the rolling 24-hour window
8. Consolidated snapshot (/api/visibility/overview) and HTML control panel (/visibility)
9. Webhook alerts with real local HTTP receiver:
   - Simulated job quarantine triggers webhook
   - Channel marked needs_reauth triggers webhook
   - Low quota (<20%) triggers webhook
   - Disk space (>80%) triggers webhook
"""

import json
import time
import shutil
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.config.settings import settings
from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    PipelineJob,
    PipelineJobStatus,
    EventLog,
    Channel,
    ChannelCredential,
    PublishLog,
    Clip,
    ClipCandidate,
    ClipStatus,
    SourceVideo,
    User,
    UserRole,
    QuotaLedger,
)
from shorts_engine.db.security import encrypt_secret
from shorts_engine.dashboard.app import app
from shorts_engine.dashboard.routes.visibility import get_db
from shorts_engine.dashboard.auth import require_viewer, require_operator, verify_csrf
from shorts_engine.services.monitoring.visibility import (
    get_queue_depth_and_oldest_age,
    get_running_jobs,
    get_quarantined_jobs,
    get_channels_visibility,
    get_quota_visibility,
    get_media_root_disk_usage,
    get_jobs_summary_24h,
    get_full_visibility_snapshot,
    evaluate_system_thresholds_and_alert,
)
from shorts_engine.services.events.taxonomy import (
    emit_platform_event,
    STAGE_QUARANTINED,
    CHANNEL_HEALTH,
    QUOTA_LOW,
)
from shorts_engine.services.monitoring.alerter import pipeline_alerter


# ── Local Webhook Receiver for Acceptance Testing ────────────────────────────

class MockWebhookHandler(BaseHTTPRequestHandler):
    received_payloads = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            data = json.loads(body)
        except Exception:
            data = {"raw": body}
        MockWebhookHandler.received_payloads.append(data)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"received"}')

    def log_message(self, format, *args):
        # Silence console log noise in pytest output
        pass


@pytest.fixture
def local_webhook_server():
    """Starts a local HTTP receiver on an ephemeral port to verify real outgoing webhooks."""
    MockWebhookHandler.received_payloads.clear()
    server = HTTPServer(("127.0.0.1", 0), MockWebhookHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    webhook_url = f"http://127.0.0.1:{port}/webhook"
    original_url = settings.ALERT_WEBHOOK_URL
    settings.ALERT_WEBHOOK_URL = webhook_url

    yield webhook_url, MockWebhookHandler.received_payloads

    settings.ALERT_WEBHOOK_URL = original_url
    server.shutdown()
    server.server_close()
    MockWebhookHandler.received_payloads.clear()


@pytest.fixture
def db_session(tmp_path):
    """Provides an isolated SQLite database session."""
    db_file = tmp_path / "test_visibility.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    yield session

    session.close()
    engine.dispose()


@pytest.fixture
def client(db_session):
    """TestClient wired with database session override and authenticated viewer/operator."""
    test_user = User(
        id="vis-op-1",
        username="visibility_admin",
        role=UserRole.ADMIN,
        password_hash="fake",
    )

    def _get_test_db():
        yield db_session

    app.dependency_overrides[get_db] = _get_test_db
    app.dependency_overrides[require_viewer] = lambda: test_user
    app.dependency_overrides[require_operator] = lambda: test_user
    app.dependency_overrides[verify_csrf] = lambda: True

    c = TestClient(app)
    yield c

    app.dependency_overrides.clear()


# ── Acceptance Tests ─────────────────────────────────────────────────────────

def test_metric_1_queue_depth_and_oldest_age(db_session, client):
    """Verifies pending queue depth breakdown by stage and oldest job age calculation."""
    now = datetime.utcnow()

    # Seed 2 pending ingest jobs, 1 pending render job, and 1 done job
    j1 = PipelineJob(
        id="job-pend-1",
        stage="ingest",
        job_type="ingest_video",
        entity_id="vid-1",
        entity_type="source_video",
        status="PENDING",
        created_at=now - timedelta(seconds=7200),  # 2 hours old
    )
    j2 = PipelineJob(
        id="job-pend-2",
        stage="ingest",
        job_type="ingest_video",
        entity_id="vid-2",
        entity_type="source_video",
        status="PENDING",
        created_at=now - timedelta(seconds=3600),  # 1 hour old
    )
    j3 = PipelineJob(
        id="job-pend-3",
        stage="render",
        job_type="render_clip",
        entity_id="clip-3",
        entity_type="clip",
        status="PENDING",
        created_at=now - timedelta(seconds=1800),  # 30 mins old
    )
    j4 = PipelineJob(
        id="job-done-1",
        stage="transcribe",
        job_type="transcribe_video",
        entity_id="vid-4",
        entity_type="source_video",
        status="DONE",
        created_at=now - timedelta(seconds=10000),
    )
    db_session.add_all([j1, j2, j3, j4])
    db_session.commit()

    # Core function assertion
    data = get_queue_depth_and_oldest_age(db_session)
    assert data["total_pending"] == 3
    assert data["overall_oldest_job_id"] == "job-pend-1"
    assert data["overall_oldest_stage"] == "ingest"
    assert data["overall_oldest_age_seconds"] >= 7190
    assert "2h" in data["overall_oldest_age_human"]

    assert "ingest" in data["stages"]
    assert data["stages"]["ingest"]["depth"] == 2
    assert "render" in data["stages"]
    assert data["stages"]["render"]["depth"] == 1

    # API endpoint assertion
    resp = client.get("/api/visibility/queue")
    assert resp.status_code == 200
    res_json = resp.json()
    assert res_json["total_pending"] == 3
    assert res_json["overall_oldest_job_id"] == "job-pend-1"


def test_metric_2_running_jobs(db_session, client):
    """Verifies reporting of active running jobs, worker lock owner, and progress."""
    now = datetime.utcnow()

    running_job = PipelineJob(
        id="job-run-1",
        stage="render",
        job_type="render_clip",
        entity_id="clip-abc-123",
        entity_type="clip",
        status="RUNNING",
        progress_pct=65.0,
        progress_detail="Rendering 9:16 composition 65%",
        locked_by="worker-pid-8844",
        locked_at=now - timedelta(seconds=45),
        heartbeat_at=now - timedelta(seconds=2),
        created_at=now - timedelta(seconds=60),
    )
    db_session.add(running_job)
    db_session.commit()

    # Core function assertion
    items = get_running_jobs(db_session)
    assert len(items) == 1
    item = items[0]
    assert item["id"] == "job-run-1"
    assert item["stage"] == "render"
    assert item["locked_by"] == "worker-pid-8844"
    assert item["progress_pct"] == 65.0
    assert item["runtime_seconds"] >= 40
    assert item["heartbeat_at"] is not None

    # API endpoint assertion
    resp = client.get("/api/visibility/running")
    assert resp.status_code == 200
    res_list = resp.json()
    assert len(res_list) == 1
    assert res_list[0]["id"] == "job-run-1"
    assert res_list[0]["locked_by"] == "worker-pid-8844"


def test_metric_3_quarantined_jobs(db_session, client):
    """Verifies quarantined dead-letter jobs with failure reasons (last_error)."""
    now = datetime.utcnow()

    q_job = PipelineJob(
        id="job-quar-1",
        stage="publish",
        job_type="publish_video",
        entity_id="clip-fail-99",
        entity_type="clip",
        status="QUARANTINED",
        error_message="Permanent 403 Forbidden: videos.insert quota exceeded or channel blocked",
        attempt_count=3,
        created_at=now - timedelta(minutes=15),
        completed_at=now - timedelta(minutes=2),
    )
    db_session.add(q_job)
    db_session.commit()

    # Core function assertion
    items = get_quarantined_jobs(db_session)
    assert len(items) == 1
    item = items[0]
    assert item["id"] == "job-quar-1"
    assert item["stage"] == "publish"
    assert "Permanent 403" in item["last_error"]
    assert item["attempt_count"] == 3

    # API endpoint assertion
    resp = client.get("/api/visibility/quarantined")
    assert resp.status_code == 200
    res_list = resp.json()
    assert len(res_list) == 1
    assert res_list[0]["id"] == "job-quar-1"
    assert "Permanent 403" in res_list[0]["last_error"]


def test_metric_4_channels_token_health_and_last_publish(db_session, client):
    """Verifies channel token status (HEALTHY / NEEDS_REAUTH) and last publish metadata."""
    now = datetime.utcnow()

    # 1. Healthy channel with successful publish
    chan1 = Channel(
        id="chan-h1",
        youtube_channel_id="UC_HEALTHY_1",
        name="Healthy Gaming Shorts",
        title="Healthy Gaming Shorts",
    )
    cred1 = ChannelCredential(
        id="cred-h1",
        channel_id=chan1.id,
        encrypted_refresh_token=encrypt_secret("valid_refresh_token_123"),
        status="active",
        verified_at=now - timedelta(hours=1),
    )
    vid1 = SourceVideo(
        id="vid-h1",
        channel_id=chan1.id,
        youtube_video_id="orig_vid_11",
        title="Source Long Video",
    )
    cand1 = ClipCandidate(
        id="cand-h1",
        source_video_id=vid1.id,
        start_ms=0,
        end_ms=30000,
        score=0.9,
        reason="high viral hook",
    )
    clip1 = Clip(
        id="clip-h1",
        clip_candidate_id=cand1.id,
        status=ClipStatus.PUBLISHED,
    )
    pub_log1 = PublishLog(
        id="pub-log-1",
        clip_id=clip1.id,
        youtube_video_id="yt_short_abc",
        title="Incredible Clutch Short",
        description="Epic gameplay short clip",
        status="SUCCESS",
        published_at=now - timedelta(minutes=45),
    )

    # 2. Channel marked needs_reauth
    chan2 = Channel(
        id="chan-reauth",
        youtube_channel_id="UC_NEEDS_REAUTH",
        name="Broken Token Channel",
        title="Broken Token Channel",
    )
    cred2 = ChannelCredential(
        id="cred-reauth",
        channel_id=chan2.id,
        encrypted_refresh_token=encrypt_secret("expired_token"),
        status="needs_reauth",
        last_error="invalid_grant: Token has been expired or revoked",
        verified_at=now - timedelta(hours=5),
    )

    db_session.add_all([chan1, cred1, vid1, cand1, clip1, pub_log1, chan2, cred2])
    db_session.commit()

    # Core function assertion
    channels_info = get_channels_visibility(db_session)
    assert len(channels_info) == 2

    # Map by channel id
    c_map = {c["channel_id"]: c for c in channels_info}
    assert c_map["chan-h1"]["token_status"] == "HEALTHY"
    assert c_map["chan-h1"]["token_healthy"] is True
    assert c_map["chan-h1"]["last_publish"] is not None
    assert c_map["chan-h1"]["last_publish"]["title"] == "Incredible Clutch Short"
    assert c_map["chan-h1"]["last_publish"]["youtube_video_id"] == "yt_short_abc"

    assert c_map["chan-reauth"]["token_status"] == "NEEDS_REAUTH"
    assert c_map["chan-reauth"]["token_healthy"] is False
    assert "invalid_grant" in c_map["chan-reauth"]["token_last_error"]

    # API endpoint assertion
    resp = client.get("/api/visibility/channels")
    assert resp.status_code == 200
    res_list = resp.json()
    assert len(res_list) == 2


def test_metric_5_quota_visibility(db_session, client):
    """Verifies quota units spent and remaining today from the quota ledger."""
    # Record 1600 units spent today (e.g. 1 video upload)
    entry = QuotaLedger(
        operation="videos.insert",
        units=1600,
        channel_id="UC_SAMPLE",
        ts=datetime.utcnow(),
    )
    db_session.add(entry)
    db_session.commit()

    data = get_quota_visibility(db_session)
    assert data["daily_limit"] == settings.DAILY_QUOTA_LIMIT
    assert data["spent_units"] == 1600
    assert data["remaining_units"] == settings.DAILY_QUOTA_LIMIT - 1600
    assert data["remaining_pct"] == round(((settings.DAILY_QUOTA_LIMIT - 1600) / settings.DAILY_QUOTA_LIMIT) * 100, 1)
    assert data["is_low"] is False

    # API endpoint assertion
    resp = client.get("/api/visibility/quota")
    assert resp.status_code == 200
    res_json = resp.json()
    assert res_json["spent_units"] == 1600
    assert res_json["remaining_units"] == settings.DAILY_QUOTA_LIMIT - 1600


def test_metric_6_media_root_disk_usage(client):
    """Verifies disk usage calculation and critical headroom alerts."""
    # 1. Normal disk space
    with patch("shutil.disk_usage", return_value=(100 * 1024**3, 40 * 1024**3, 60 * 1024**3)):
        disk = get_media_root_disk_usage()
        assert disk["total_gb"] == 100.0
        assert disk["used_gb"] == 40.0
        assert disk["free_gb"] == 60.0
        assert disk["percent_used"] == 40.0
        assert disk["is_critical"] is False

    # 2. Critical disk space (> 80%)
    with patch("shutil.disk_usage", return_value=(100 * 1024**3, 85 * 1024**3, 15 * 1024**3)):
        disk = get_media_root_disk_usage()
        assert disk["percent_used"] == 85.0
        assert disk["is_critical"] is True

    # API endpoint assertion
    resp = client.get("/api/visibility/storage")
    assert resp.status_code == 200
    assert "total_gb" in resp.json()
    assert "percent_used" in resp.json()


def test_metric_7_jobs_summary_24h(db_session, client):
    """Verifies calculation of job outcomes in the rolling 24-hour window."""
    now = datetime.utcnow()

    # 3 DONE jobs in last 24h
    for i in range(3):
        db_session.add(PipelineJob(
            id=f"job-done-{i}",
            stage=f"transcribe_{i}",
            job_type="transcribe",
            entity_id=f"ent-done-{i}",
            entity_type="source_video",
            status="DONE",
            completed_at=now - timedelta(hours=i + 1),
            updated_at=now - timedelta(hours=i + 1),
            created_at=now - timedelta(hours=i + 2),
        ))

    # 1 FAILED job and 1 QUARANTINED job in last 24h
    db_session.add(PipelineJob(
        id="job-fail-1",
        stage="render",
        job_type="render",
        entity_id="ent-fail-1",
        entity_type="clip",
        status="FAILED",
        updated_at=now - timedelta(hours=3),
        created_at=now - timedelta(hours=4),
    ))
    db_session.add(PipelineJob(
        id="job-quar-1",
        stage="publish",
        job_type="publish",
        entity_id="ent-quar-1",
        entity_type="clip",
        status="QUARANTINED",
        updated_at=now - timedelta(hours=2),
        created_at=now - timedelta(hours=3),
    ))

    # 1 DONE job 48 hours ago (strictly outside 24h window)
    db_session.add(PipelineJob(
        id="job-old-done",
        stage="ingest",
        job_type="ingest",
        entity_id="ent-old-1",
        entity_type="source_video",
        status="DONE",
        completed_at=now - timedelta(hours=48),
        updated_at=now - timedelta(hours=48),
        created_at=now - timedelta(hours=49),
    ))

    db_session.commit()

    summary = get_jobs_summary_24h(db_session)
    assert summary["succeeded_jobs"] == 3
    assert summary["failed_jobs"] == 2
    assert summary["total_completed"] == 5
    assert summary["success_rate_pct"] == 60.0  # 3 / 5 = 60%

    # API endpoint assertion
    resp = client.get("/api/visibility/jobs-24h")
    assert resp.status_code == 200
    assert resp.json()["succeeded_jobs"] == 3
    assert resp.json()["failed_jobs"] == 2
    assert resp.json()["success_rate_pct"] == 60.0


def test_overview_snapshot_and_html_panel(db_session, client):
    """Verifies that the consolidated snapshot and HTML control room panel render completely."""
    # Seed minimal data
    db_session.add(PipelineJob(
        id="job-test-overview",
        stage="ingest",
        job_type="ingest",
        entity_id="vid-test-overview",
        entity_type="source_video",
        status="PENDING",
        created_at=datetime.utcnow(),
    ))
    db_session.commit()

    # 1. Overview API
    resp = client.get("/api/visibility/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert "queue" in data
    assert "running_jobs" in data
    assert "quarantined_jobs" in data
    assert "channels" in data
    assert "quota" in data
    assert "disk" in data
    assert "jobs_24h" in data

    # 2. HTML Control Panel
    html_resp = client.get("/visibility")
    assert html_resp.status_code == 200
    content = html_resp.text
    assert "System Visibility &amp; Operational Health" in content or "System Visibility & Operational Health" in content
    assert "Oldest Pending Age" in content
    assert "Active Running" in content
    assert "Quarantined (DLQ)" in content
    assert "Quota Remaining" in content
    assert "Media Root Disk" in content
    assert "Queue Depths by Stage" in content
    assert "Channel OAuth Token Health" in content


# ── Webhook Alert Acceptance Tests ───────────────────────────────────────────

def test_webhook_alert_on_simulated_quarantine(local_webhook_server, db_session):
    """
    CRITICAL ACCEPTANCE TEST:
    A simulated job quarantine triggers the webhook with structured payload.
    Tested against a live local HTTP receiver.
    """
    _, received_payloads = local_webhook_server

    # Trigger stage.quarantined event via taxonomy
    emit_platform_event(
        STAGE_QUARANTINED,
        payload={
            "stage": "publish",
            "last_error": "Simulated permanent API rejection: token revoked",
            "attempt_count": 5,
        },
        entity_id="job-quar-alert-test",
        entity_type="job",
        level="CRITICAL",
        message="Simulated permanent API rejection: token revoked",
        db=db_session,
    )

    # Allow worker thread / wait for dispatch
    time.sleep(0.5)

    assert len(received_payloads) >= 1
    webhook_event = received_payloads[-1]
    assert webhook_event["event"] == "alert"
    assert webhook_event["alert_type"] == "quarantined_job"
    assert webhook_event["level"] == "CRITICAL"
    assert "publish" in webhook_event["title"]
    assert "token revoked" in webhook_event["message"]
    assert webhook_event["context"]["job_id"] == "job-quar-alert-test"
    assert webhook_event["context"]["stage"] == "publish"
    assert webhook_event["context"]["attempt_count"] == 5


def test_webhook_alert_on_channel_needs_reauth(local_webhook_server, db_session):
    """Verifies that marking a channel needs_reauth triggers the alert webhook."""
    _, received_payloads = local_webhook_server

    emit_platform_event(
        CHANNEL_HEALTH,
        payload={
            "channel_id": "chan-alert-test",
            "channel_name": "Top Clips Channel",
            "status": "needs_reauth",
            "error": "invalid_grant: Refresh token expired",
        },
        entity_id="chan-alert-test",
        entity_type="channel",
        level="CRITICAL",
        message="Channel marked needs_reauth: invalid_grant",
        db=db_session,
    )

    time.sleep(0.5)

    assert len(received_payloads) >= 1
    webhook_event = received_payloads[-1]
    assert webhook_event["event"] == "alert"
    assert webhook_event["alert_type"] == "channel_needs_reauth"
    assert webhook_event["level"] == "ERROR"
    assert "Top Clips Channel" in webhook_event["title"]
    assert "invalid_grant" in webhook_event["message"]


def test_webhook_alert_on_quota_low(local_webhook_server, db_session):
    """Verifies that quota running low triggers the alert webhook."""
    _, received_payloads = local_webhook_server

    emit_platform_event(
        QUOTA_LOW,
        payload={
            "remaining_units": 400,
            "spent_units": 9600,
            "limit": 10000,
        },
        entity_id="system",
        entity_type="system",
        level="WARNING",
        message="Daily YouTube API quota remaining is critically low (400 / 10,000 units remaining)",
        db=db_session,
    )

    time.sleep(0.5)

    assert len(received_payloads) >= 1
    webhook_event = received_payloads[-1]
    assert webhook_event["event"] == "alert"
    assert webhook_event["alert_type"] == "quota_exhaustion_warning"
    assert webhook_event["level"] == "WARNING"
    assert "400" in webhook_event["message"]


def test_system_threshold_evaluation_dispatcher(local_webhook_server, db_session, client):
    """
    Verifies evaluate_system_thresholds_and_alert() checks disk and quota thresholds
    and dispatches webhooks when exceeded.
    """
    _, received_payloads = local_webhook_server

    # Set mock disk usage to 88% (exceeding default 80% threshold)
    with patch("shutil.disk_usage", return_value=(100 * 1024**3, 88 * 1024**3, 12 * 1024**3)):
        result = evaluate_system_thresholds_and_alert(db_session, wait=True)
        assert "disk_space_critical" in result["alerts_fired"]

    assert len(received_payloads) >= 1
    disk_alert = [p for p in received_payloads if p.get("alert_type") == "disk_space_critical"]
    assert len(disk_alert) == 1
    assert disk_alert[0]["context"]["percent_used"] == 88.0

    # Also test through the API endpoint
    MockWebhookHandler.received_payloads.clear()
    with patch("shutil.disk_usage", return_value=(100 * 1024**3, 85 * 1024**3, 15 * 1024**3)):
        post_resp = client.post("/api/visibility/alerts/check")
        assert post_resp.status_code == 200
        assert "disk_space_critical" in post_resp.json()["alerts_fired"]
