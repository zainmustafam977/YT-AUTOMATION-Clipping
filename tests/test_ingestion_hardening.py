"""
Tests for Ingestion Hardening, SSRF Protection, Pre-Download Bounds,
Subprocess Hardening, Rate Limiting, and Authentication Coverage.
"""

import subprocess
import time
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from shorts_engine.utils.url_guard import (
    validate_youtube_url_or_id,
    SSRFValidationError,
    is_safe_public_ip,
    is_ip_literal,
)
from shorts_engine.services.ingestion.downloader import IngestionService
from shorts_engine.services.execution.watchdog import SubprocessWatchdog
from shorts_engine.dashboard.rate_limit import IngestionRateLimiter, ingestion_rate_limiter
from shorts_engine.orchestration.exceptions import classify_exception, PermanentError
from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.dashboard.auth import create_session_token, hash_password
from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import User, UserRole


# ── 1. SSRF & URL Guard Tests ────────────────────────────────────────────────

def test_ssrf_probes_rejected_before_network_request():
    """All private IPs, loopback, cloud metadata, and malicious domains are rejected immediately."""
    probes = [
        "http://169.254.169.254/",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost",
        "http://localhost:8080/watch?v=12345678901",
        "http://127.0.0.1/",
        "http://127.0.0.1:8000/api",
        "http://10.0.0.1/video",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "file:///etc/passwd",
        "ftp://youtube.com/watch?v=12345678901",
        "https://evil-youtube.com/watch?v=12345678901",
        "https://youtube.com.attacker.com/watch?v=12345678901",
        "https://notyoutube.com/watch?v=12345678901",
        "https://google.com/",
        "",
        "   ",
        "http://0.0.0.0/",
    ]

    for probe in probes:
        with pytest.raises(SSRFValidationError):
            validate_youtube_url_or_id(probe)


def test_valid_youtube_urls_and_ids():
    """Valid YouTube URLs across all supported standard formats are parsed to canonical form."""
    valid_inputs = [
        ("dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("http://youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ]

    for raw_input, expected_url, expected_id in valid_inputs:
        canonical_url, video_id = validate_youtube_url_or_id(raw_input)
        assert canonical_url == expected_url
        assert video_id == expected_id


# ── 2. Pre-Download Caps (Duration & File Size) Tests ────────────────────────

def test_pre_download_duration_cap_rejected():
    """Videos exceeding MAX_INGESTION_DURATION_HOURS (default 4h = 14400s) are rejected before download."""
    service = IngestionService()
    # Mock get_metadata to return 5-hour video metadata
    overlong_metadata = {
        "id": "dQw4w9WgXcQ",
        "title": "5 Hour Long Video",
        "duration": 5 * 3600,  # 18000s > 14400s
        "filesize": 500 * 1024 * 1024,
    }

    with patch.object(service, "get_metadata", return_value=overlong_metadata):
        with pytest.raises(ValueError) as exc_info:
            service.download_video("dQw4w9WgXcQ")
        assert "exceeds maximum allowed duration" in str(exc_info.value)


def test_pre_download_file_size_cap_rejected():
    """Videos exceeding MAX_INGESTION_FILE_SIZE_MB (default 5120 MB) are rejected before download."""
    service = IngestionService()
    # Mock get_metadata to return 6GB file metadata (> 5GB default limit)
    oversized_metadata = {
        "id": "dQw4w9WgXcQ",
        "title": "Huge Video",
        "duration": 1800,  # 30 mins
        "filesize": 6 * 1024 * 1024 * 1024,  # 6GB > 5GB
    }

    with patch.object(service, "get_metadata", return_value=oversized_metadata):
        with pytest.raises(ValueError) as exc_info:
            service.download_video("dQw4w9WgXcQ")
        assert "exceeds maximum allowed limit" in str(exc_info.value)


# ── 3. Overall Download Timeout & Quarantine Classification ──────────────────

def test_download_timeout_triggers_permanent_quarantine():
    """Overall download timeout raises TimeoutError and is classified as PermanentError (quarantined)."""
    service = IngestionService()
    service.max_retries = 1

    import yt_dlp
    normal_meta = {"id": "dQw4w9WgXcQ", "duration": 60, "filesize": 1024 * 1024}
    with patch.object(service, "get_metadata", return_value=normal_meta), \
         patch("shorts_engine.services.ingestion.downloader.settings.INGESTION_DOWNLOAD_TIMEOUT_SEC", 0.01):
        with patch.object(service, "extract_audio"), \
             patch.object(yt_dlp.YoutubeDL, "extract_info", side_effect=lambda *args, **kwargs: time.sleep(0.05)):
            with pytest.raises(TimeoutError) as exc_info:
                service.download_video("dQw4w9WgXcQ")
            assert "Overall download timeout expired" in str(exc_info.value)

            # Assert classification maps this to PermanentError
            classified = classify_exception(exc_info.value)
            assert isinstance(classified, PermanentError)


# ── 4. Subprocess Hardening (Thread Capping & Process Group) ──────────────────

def test_subprocess_watchdog_caps_ffmpeg_threads():
    """SubprocessWatchdog automatically injects -threads min(4, CPU count) for ffmpeg and ffprobe."""
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("ok", "")
        mock_proc.returncode = 0
        mock_proc.pid = 12345
        mock_proc._handle = None
        mock_popen.return_value = mock_proc

        SubprocessWatchdog.run(["ffmpeg", "-i", "input.mp4", "output.mp4"], check=False)

        # Inspect command passed to Popen
        called_cmd = mock_popen.call_args[0][0]
        assert "-threads" in called_cmd
        thread_idx = called_cmd.index("-threads")
        assert int(called_cmd[thread_idx + 1]) <= 4

        # Inspect process group keyword argument
        kwargs = mock_popen.call_args[1]
        assert "creationflags" in kwargs or "start_new_session" in kwargs


# ── 5. Ingestion Rate Limiting Tests ─────────────────────────────────────────

def test_rate_limiter_allows_five_and_blocks_sixth():
    """Sliding-window rate limiter allows 5 requests/minute and rejects 6th with False."""
    limiter = IngestionRateLimiter(limit_per_minute=5)
    user_id = "test-user-uuid"

    # First 5 should succeed
    for i in range(5):
        assert limiter.check_rate_limit(user_id) is True

    # 6th request within same minute should fail
    assert limiter.check_rate_limit(user_id) is False

    # Reset clears history
    limiter.reset()
    assert limiter.check_rate_limit(user_id) is True


# ── 6. Authentication Coverage Tests ─────────────────────────────────────────

def test_unauthenticated_requests_return_401_or_redirect():
    """Every dashboard route returns 401 (or 303 for document GETs) when accessed unauthenticated."""
    app = create_dashboard_app()
    client = TestClient(app)

    protected_endpoints = [
        ("GET", "/api/estimate-clips?duration_sec=60"),
        ("GET", "/api/thumbnails/dQw4w9WgXcQ"),
        ("GET", "/dead-letter/clear-all"),
        ("GET", "/api/jobs/quarantined"),
        ("GET", "/api/metrics/clips/clip-123/ai-tokens"),
        ("GET", "/api/events/history"),
        ("GET", "/api/events/recent"),
        ("POST", "/api/events/publish"),
        ("POST", "/api/events/prune"),
        ("POST", "/process-video"),
        ("POST", "/api/ingest"),
        ("GET", "/app/publish"),
    ]

    for method, path in protected_endpoints:
        if method == "GET":
            # API request (JSON header)
            res = client.get(path, headers={"Accept": "application/json"})
            assert res.status_code in (401, 303, 307), f"{path} returned {res.status_code}"
        elif method == "POST":
            res = client.post(path, json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}, headers={"Accept": "application/json"})
            assert res.status_code in (401, 403), f"{path} returned {res.status_code}"


def test_ssrf_url_rejected_at_endpoint_intake():
    """Submitting an SSRF URL to /process-video or /api/ingest returns HTTP 400 Bad Request."""
    app = create_dashboard_app()
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username="operator_test").first()
        if not user:
            user = User(
                username="operator_test",
                password_hash=hash_password("OperatorPass123!"),
                role=UserRole.OPERATOR,
                is_active=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        session_token = create_session_token(str(user.id))
    finally:
        db.close()

    ingestion_rate_limiter.reset()
    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {session_token}",
        "Accept": "application/json",
    }

    # SSRF Cloud Metadata attempt via API ingest
    res = client.post(
        "/api/ingest",
        json={"url": "http://169.254.169.254/latest/meta-data/"},
        headers=headers,
    )
    assert res.status_code == 400
    assert "SSRF Protection" in res.text


# ── 7. Additional Hardening & Security Unit Tests ─────────────────────────────

def test_security_module_validate_ingestion_url():
    """Validates security.validate_ingestion_url rejects SSRF probes and accepts legitimate URLs."""
    from shorts_engine.services.ingestion.security import (
        validate_ingestion_url,
        SSRFValidationError,
    )

    # Probes that must be rejected immediately before any network request
    evil_probes = [
        "http://169.254.169.254/",
        "http://169.254.169.254/latest/meta-data",
        "http://localhost/",
        "http://localhost:8080/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://10.0.0.5/test",
        "http://192.168.1.100/",
        "http://172.16.5.5/",
        "http://100.64.0.1/",
        "file:///etc/passwd",
        "file://C:/Windows/win.ini",
        "gopher://youtube.com/",
        "ftp://youtube.com/",
        "https://attacker.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com.attacker.com/watch?v=dQw4w9WgXcQ",
        "",
        "   ",
    ]

    for probe in evil_probes:
        with pytest.raises(SSRFValidationError):
            validate_ingestion_url(probe)

    # Valid inputs
    assert validate_ingestion_url("dQw4w9WgXcQ") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert "https://www.youtube.com/watch?v=dQw4w9WgXcQ" in validate_ingestion_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert "https://www.youtube.com/watch?v=dQw4w9WgXcQ" in validate_ingestion_url("https://youtu.be/dQw4w9WgXcQ")


def test_security_module_rate_limiter_raises_429():
    """IngestionRateLimiter in security.py raises HTTP 429 after 5 requests per minute."""
    from shorts_engine.services.ingestion.security import IngestionRateLimiter
    from fastapi import HTTPException

    limiter = IngestionRateLimiter(requests_per_minute=5, window_seconds=60.0)
    user_key = "user:42"

    for _ in range(5):
        limiter.check(user_key)

    with pytest.raises(HTTPException) as exc_info:
        limiter.check(user_key)
    assert exc_info.value.status_code == 429
    assert "rate limit exceeded" in exc_info.value.detail.lower()


def test_subprocess_watchdog_clamps_excessive_threads():
    """SubprocessWatchdog clamps explicit -threads 16 down to FFMPEG_MAX_THREADS."""
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("ok", "")
        mock_proc.returncode = 0
        mock_proc.pid = 99999
        mock_popen.return_value = mock_proc

        SubprocessWatchdog.run(["ffmpeg", "-threads", "16", "-i", "input.mp4", "output.mp4"], check=False)

        called_cmd = mock_popen.call_args[0][0]
        thread_idx = called_cmd.index("-threads")
        assert int(called_cmd[thread_idx + 1]) <= 4


def test_spa_and_events_stream_require_auth():
    """Unauthenticated requests to /app and /api/events/stream return 401 or redirect."""
    app = create_dashboard_app()
    client = TestClient(app)

    # /app as API client (JSON accept)
    res_app_api = client.get("/app", headers={"Accept": "application/json"})
    assert res_app_api.status_code == 401

    # /app as browser client (HTML accept)
    res_app_browser = client.get("/app", headers={"Accept": "text/html", "Sec-Fetch-Dest": "document"}, follow_redirects=False)
    assert res_app_browser.status_code in (302, 303)
    assert "/login" in res_app_browser.headers.get("location", "")

    # /api/events/stream with max_ticks=1
    res_stream = client.get("/api/events/stream?max_ticks=1", headers={"Accept": "application/json"})
    assert res_stream.status_code in (401, 303)
