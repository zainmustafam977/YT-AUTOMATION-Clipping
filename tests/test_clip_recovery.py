"""
Unit and integration tests for:
- Clip Recovery & Failure Diagnostics Service (Tech, Frame, Semantic, Advisory)
- Force approve and operator overrides
- Native OS Open in Folder functionality
- Pipeline Resume button state machine control
- Queue recovery API endpoints
"""

import json
from datetime import datetime, timedelta
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from shorts_engine.db.models import Clip, ClipStatus, ClipCandidate, SourceVideo, SourceVideoStatus, Channel, User, UserRole
from shorts_engine.services.quality_gate.recovery import ClipRecoveryService
from shorts_engine.dashboard.routes.status import build_recent_videos_payload


def test_clip_recovery_diagnostics():
    service = ClipRecoveryService()

    # Mock clip with quality report containing Tech, Frame, and Semantic issues
    clip = MagicMock()
    clip.id = "test-clip-123"
    clip.status = ClipStatus.REJECTED
    clip.rendered_path = "/path/to/rendered.mp4"
    clip.quality_check_result = json.dumps({
        "technical_check": {
            "all_passed": False,
            "error": "Duration exceeds maximum ceiling",
            "checks": {"correct_resolution": True, "duration_within_bounds": False, "audio_stream_present": True},
        },
        "render_inspection": {
            "all_passed": False,
            "caption_collision_violations": 2,
            "subject_framing_violations": 0,
        },
        "semantic_check": {
            "pass_decision": False,
            "critique_reason": "Incomplete narrative arc",
        },
        "advisories": ["Pacing note: fast transition"],
    })

    diag = service.diagnose_clip(clip)

    assert diag["clip_id"] == "test-clip-123"
    assert diag["status"] == "REJECTED"
    assert diag["tech_gate"]["passed"] is False
    assert "Duration exceeds maximum ceiling" in diag["tech_gate"]["errors"]
    assert diag["framing_gate"]["passed"] is False
    assert any("Caption collided" in e for e in diag["framing_gate"]["errors"])
    assert diag["semantic_gate"]["passed"] is False
    assert "Incomplete narrative arc" in diag["semantic_gate"]["errors"]
    assert diag["has_advisories"] is True
    assert len(diag["recommended_fixes"]) >= 2


def test_clip_force_approve():
    service = ClipRecoveryService()

    mock_video = MagicMock()
    mock_video.channel = MagicMock(human_review_required=True)
    mock_cand = MagicMock(source_video=mock_video)

    clip = MagicMock()
    clip.id = "force-clip-456"
    clip.status = ClipStatus.REJECTED
    clip.candidate = mock_cand
    clip.quality_check_result = json.dumps({"technical_check": {"all_passed": False}})

    mock_db = MagicMock()

    new_status, q_report = service.force_approve_clip(
        clip=clip,
        db=mock_db,
        operator_name="lead_editor",
        note="Manual quality pass granted",
    )

    assert new_status == ClipStatus.WAITING_APPROVAL
    assert "operator_override" in q_report
    assert q_report["operator_override"]["approved_by"] == "lead_editor"
    assert q_report["operator_override"]["previous_status"] == "REJECTED"
    mock_db.commit.assert_called_once()


def test_open_in_folder_cross_platform(tmp_path):
    test_file = tmp_path / "test_short.mp4"
    test_file.write_bytes(b"dummy")

    with patch("subprocess.Popen") as mock_popen:
        with patch("platform.system", return_value="Windows"):
            res = ClipRecoveryService.open_in_folder(test_file)
            assert res["status"] == "success"
            mock_popen.assert_called_once()
            args = mock_popen.call_args[0][0]
            assert args[0] == "explorer.exe"


def test_resume_button_state_machine():
    now = MagicMock()

    now_time = datetime.utcnow()

    # Case 1: Video is currently RUNNING (recently updated, lock is held)
    video_running = MagicMock(spec=SourceVideo)
    video_running.id = "v-running"
    video_running.youtube_video_id = "yt-run-1"
    video_running.title = "Running Video"
    video_running.status = SourceVideoStatus.DOWNLOADING
    video_running.created_at = now_time - timedelta(seconds=60)
    video_running.updated_at = now_time - timedelta(seconds=10)
    video_running.channel = None
    video_running.candidates = []
    video_running.transcript = None
    video_running.downloaded_path = None
    video_running.error_message = None
    video_running.runtime_overrides = None
    video_running.retry_count = 0
    video_running.duration_sec = 100

    # Case 2: Video is INTERRUPTED (status is TRANSCRIBING, stale > 600s, lock is NOT held)
    video_interrupted = MagicMock(spec=SourceVideo)
    video_interrupted.id = "v-interrupted"
    video_interrupted.youtube_video_id = "yt-int-2"
    video_interrupted.title = "Interrupted Video"
    video_interrupted.status = SourceVideoStatus.TRANSCRIBING
    video_interrupted.created_at = now_time - timedelta(minutes=30)
    video_interrupted.updated_at = now_time - timedelta(minutes=20)
    video_interrupted.channel = None
    video_interrupted.candidates = []
    video_interrupted.transcript = None
    video_interrupted.downloaded_path = None
    video_interrupted.error_message = None
    video_interrupted.runtime_overrides = None
    video_interrupted.retry_count = 0
    video_interrupted.duration_sec = 100

    # Case 3: Video is DONE (completed)
    video_done = MagicMock(spec=SourceVideo)
    video_done.id = "v-done"
    video_done.youtube_video_id = "yt-done-3"
    video_done.title = "Done Video"
    video_done.status = SourceVideoStatus.DONE
    video_done.created_at = now_time - timedelta(minutes=30)
    video_done.updated_at = now_time - timedelta(minutes=10)
    video_done.channel = None
    video_done.candidates = []
    video_done.transcript = None
    video_done.downloaded_path = None
    video_done.error_message = None
    video_done.runtime_overrides = None
    video_done.retry_count = 0
    video_done.duration_sec = 100

    mock_db = MagicMock()
    mock_db.query.return_value.options.return_value.order_by.return_value.limit.return_value.all.return_value = [
        video_running, video_interrupted, video_done
    ]
    mock_db.query.return_value.filter.return_value.all.return_value = []

    def mock_get_video_lock(yt_id):
        lock = MagicMock()
        lock.locked.return_value = (yt_id == "yt-run-1")
        lock.acquire.return_value = (yt_id != "yt-run-1")
        return lock

    with patch("shorts_engine.dashboard.routes.status.get_video_lock", side_effect=mock_get_video_lock):
        res = build_recent_videos_payload(mock_db)

        run_res = next(r for r in res if r["youtube_video_id"] == "yt-run-1")
        int_res = next(r for r in res if r["youtube_video_id"] == "yt-int-2")
        done_res = next(r for r in res if r["youtube_video_id"] == "yt-done-3")

        # Running video: can_cancel=True, can_resume=FALSE!
        assert run_res["can_cancel"] is True
        assert run_res["can_resume"] is False

        # Interrupted video: can_cancel=False, can_resume=TRUE!
        assert int_res["can_cancel"] is False
        assert int_res["can_resume"] is True

        # Done video: can_cancel=False, can_resume=FALSE!
        assert done_res["can_cancel"] is False
        assert done_res["can_resume"] is False


def test_recovery_api_endpoints(tmp_path):
    from fastapi.testclient import TestClient
    from shorts_engine.dashboard.app import create_dashboard_app
    from shorts_engine.dashboard.auth import require_operator, require_viewer, verify_csrf
    from shorts_engine.db.session import get_db

    app = create_dashboard_app()

    # Create dummy rendered file
    dummy_video = tmp_path / "clip_101.mp4"
    dummy_video.write_bytes(b"dummy video bytes")

    mock_clip = MagicMock(spec=Clip)
    mock_clip.id = "clip-abc-101"
    mock_clip.status = ClipStatus.REJECTED
    mock_clip.rendered_path = str(dummy_video)
    mock_clip.quality_check_result = json.dumps({
        "technical_check": {"all_passed": True},
        "render_inspection": {"all_passed": True},
        "semantic_check": {"pass_decision": True},
    })
    mock_clip.candidate = MagicMock()
    mock_clip.candidate.source_video = MagicMock()
    mock_clip.candidate.source_video.channel = MagicMock(human_review_required=True)
    mock_clip.candidate.source_video.runtime_overrides = None

    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.first.return_value = mock_clip

    from shorts_engine.dashboard.routes import queue as queue_route

    # Override dependencies
    mock_user = MagicMock(spec=User)
    mock_user.id = "usr-1"
    mock_user.username = "admin"
    mock_user.role = UserRole.ADMIN

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[queue_route.get_db] = lambda: mock_db
    app.dependency_overrides[require_viewer] = lambda: mock_user
    app.dependency_overrides[require_operator] = lambda: mock_user
    app.dependency_overrides[verify_csrf] = lambda: True

    client = TestClient(app)

    # 1. Test diagnostics endpoint
    resp_diag = client.get("/queue/clip/clip-abc-101/diagnostics")
    assert resp_diag.status_code == 200
    d_data = resp_diag.json()
    assert d_data["clip_id"] == "clip-abc-101"
    assert d_data["tech_gate"]["passed"] is True

    # 2. Test open-folder endpoint
    with patch("subprocess.Popen"):
        resp_folder = client.post("/api/clip/clip-abc-101/open-folder")
        assert resp_folder.status_code == 200
        assert resp_folder.json()["status"] == "success"

    # 3. Test force-approve endpoint
    resp_force = client.post(
        "/queue/clip/clip-abc-101/force-approve",
        data={"note": "Test override note"},
        headers={"Accept": "application/json"},
    )
    assert resp_force.status_code == 200
    assert resp_force.json()["status"] == "success"
    assert resp_force.json()["new_status"] in ("WAITING_APPROVAL", "APPROVED")

    # 4. Test re-evaluate endpoint
    with patch.object(ClipRecoveryService, "re_evaluate_clip", return_value=(ClipStatus.WAITING_APPROVAL, {"all_passed": True})):
        resp_reeval = client.post(
            "/queue/clip/clip-abc-101/re-evaluate",
            headers={"Accept": "application/json"},
        )
        assert resp_reeval.status_code == 200
        assert resp_reeval.json()["status"] == "success"

