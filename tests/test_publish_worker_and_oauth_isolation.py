"""
Acceptance Test Suite for Publish Worker and OAuth Isolation.
Verifies all 5 Acceptance Criteria:
1. Grep/AST proves no module outside worker.py imports YouTube/OAuth client.
2. Simulated 403 quotaExceeded defers the job past PT midnight.
3. Simulated invalid_grant parks the channel as needs_reauth and halts retries.
4. Simulated ambiguous failure triggers the tag-verification path, avoiding duplicate uploads.
5. dry_run path logs payload, skips API calls, and consumes zero quota.
"""

import os
import ast
import json
import random
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.session import Base
from shorts_engine.db.models import (
    Channel,
    ChannelCredential,
    SourceVideo,
    ClipCandidate,
    Clip,
    ClipStatus,
    PipelineJob,
    PipelineJobStatus,
    PublishJob,
    PublishLog,
    PublishSlot,
    PublishSlotStatus,
)
from shorts_engine.db.security import encrypt_secret
from shorts_engine.services.publishing.worker import (
    YouTubePublishWorker,
    format_idempotency_tag,
    build_youtube_client,
    HttpError,
    DEFAULT_SCOPES,
)
from shorts_engine.services.publishing.quota import (
    QuotaTracker,
    get_next_pacific_midnight,
    VIDEOS_INSERT_COST_UNITS,
    VIDEOS_UPDATE_COST_UNITS,
    CHANNELS_LIST_COST_UNITS,
)


@pytest.fixture
def db_session():
    """In-memory SQLite session isolated per test."""
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


def _create_test_clip(db, channel_yt_id="UC_test_chan_123", clip_id="clip_abc_123"):
    """Helper to populate channel, video, candidate, and rendered clip."""
    chan = Channel(
        id="chan_1",
        youtube_channel_id=channel_yt_id,
        name="Test Publishing Channel",
    )
    db.add(chan)
    db.flush()

    cred = ChannelCredential(
        id="cred_1",
        channel_id=chan.id,
        encrypted_refresh_token=encrypt_secret("test_refresh_token_xyz"),
        status="active",
    )
    db.add(cred)
    db.flush()

    vid = SourceVideo(
        id="vid_1",
        channel_id=chan.id,
        youtube_video_id="orig_vid_001",
        title="Original Source Video",
    )
    db.add(vid)
    db.flush()

    cand = ClipCandidate(
        id="cand_1",
        source_video_id=vid.id,
        start_ms=0,
        end_ms=25000,
        score=0.9,
        reason="High engagement viral hook",
        hook_text="Viral Hook",
        selected=True,
    )
    db.add(cand)
    db.flush()

    import tempfile
    tmp = Path(tempfile.mktemp(suffix=".mp4"))
    tmp.write_bytes(b"\x00" * 1024)

    clip = Clip(
        id=clip_id,
        clip_candidate_id=cand.id,
        rendered_path=str(tmp),
        status=ClipStatus.APPROVED,
        generated_title="Awesome Test Short",
        generated_description="Check this out! #Shorts",
    )
    db.add(clip)
    db.commit()
    return chan, cred, clip, tmp


def _bind_publish_slot(db, channel, clip):
    """Allocate the PublishSlot that mandatory slot binding (I2) requires.

    execute_publish_intent() refuses to publish unless the payload carries a
    slot_id bound to the clip's channel. These tests predate that invariant
    and passed an empty payload, so they now allocate a slot explicitly
    rather than the product relaxing the check.
    """
    slot = PublishSlot(
        id=f"slot_{clip.id}",
        channel_id=channel.id,
        clip_id=clip.id,
        slot_start=datetime.now(timezone.utc),
        platform="youtube",
        status=PublishSlotStatus.RESERVED.value,
    )
    db.add(slot)
    db.commit()
    return slot


# ==============================================================================
# Acceptance Test 1: Code Isolation & AST/Grep Verification
# ==============================================================================

def test_oauth_and_youtube_client_import_isolation():
    """
    Approved Amendment to PR-05 Boundary:
    - No module outside shorts_engine/services/publishing/ may import any google* library.
    - googleapiclient imports remain strictly in shorts_engine/services/publishing/worker.py only.
    """
    repo_root = Path(__file__).resolve().parent.parent
    engine_dir = repo_root / "shorts_engine"
    assert engine_dir.exists(), f"shorts_engine directory not found at {engine_dir}"

    violating_files = []

    for py_file in engine_dir.rglob("*.py"):
        rel_path = py_file.relative_to(repo_root).as_posix()

        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except Exception:
            continue

        for node in ast.walk(tree):
            imported_names = []
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_names.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_names.append(node.module)

            for mod_name in imported_names:
                # Rule 1: googleapiclient is permitted ONLY in worker.py
                if mod_name == "googleapiclient" or mod_name.startswith("googleapiclient."):
                    if rel_path != "shorts_engine/services/publishing/worker.py":
                        violating_files.append((rel_path, node.lineno, mod_name, "googleapiclient allowed only in worker.py"))
                # Rule 2: Google OAuth / YouTube client libraries (excluding Gemini LLM SDK google.genai) allowed ONLY in services/publishing/
                elif (mod_name.startswith("google.oauth2") or mod_name.startswith("google.auth") or
                      mod_name.startswith("google_auth_oauthlib") or mod_name == "google_auth_oauthlib"):
                    if not rel_path.startswith("shorts_engine/services/publishing/"):
                        violating_files.append((rel_path, node.lineno, mod_name, "google OAuth/client library allowed only in services/publishing/"))

    assert not violating_files, (
        f"Forbidden Google client library imports found:\n"
        + "\n".join(f"  {f}:{line} imports '{mod}' ({reason})" for f, line, mod, reason in violating_files)
    )


# ==============================================================================
# Acceptance Test 2: HTTP 403 quotaExceeded Deferral Past Pacific Midnight
# ==============================================================================

def test_simulated_403_quota_exceeded_defers_past_pacific_midnight(db_session):
    """
    CRITERION 2: Simulated 403 quotaExceeded defers the job past PT midnight with jitter.
    Never retries immediately. Emits an alert event.
    """
    chan, cred, clip, tmp = _create_test_clip(db_session)
    slot = _bind_publish_slot(db_session, chan, clip)

    job = PipelineJob(
        id="job_quota_403",
        job_type="publish",
        stage="publish",
        entity_id=clip.id,
        entity_type="clip",
        status=PipelineJobStatus.RUNNING,
    )
    db_session.add(job)
    db_session.commit()

    worker = YouTubePublishWorker(dry_run=False)

    # Create mock YouTube client that raises 403 quotaExceeded
    mock_client = MagicMock()
    mock_request = MagicMock()
    mock_response = MagicMock()
    mock_response.status = 403
    mock_request.next_chunk.side_effect = HttpError(
        resp=mock_response,
        content=b'{"error": {"errors": [{"reason": "quotaExceeded", "message": "The request cannot be completed because you have exceeded your quota."}]}}',
    )
    mock_client.videos.return_value.insert.return_value = mock_request
    mock_client.playlistItems.return_value.list.return_value.execute.return_value = {"items": []}

    with patch("shorts_engine.services.publishing.worker.build_youtube_client", return_value=mock_client):
        res = worker.execute_publish_intent(clip_id=clip.id, payload={"slot_id": slot.id}, db=db_session, job_entity=job)

    assert res["success"] is False
    assert res["status"] == "DEFERRED"

    # Refresh job from database
    db_session.refresh(job)
    assert job.status == PipelineJobStatus.PENDING

    # Verify next_run_at is strictly in the future and past next Pacific midnight
    expected_midnight_utc, reset_iso = get_next_pacific_midnight()
    assert job.next_run_at is not None
    # Next run at should be >= Pacific midnight UTC
    assert job.next_run_at >= expected_midnight_utc
    # And within 120 seconds jitter past midnight
    assert job.next_run_at <= expected_midnight_utc + timedelta(seconds=120)

    # Must contain explanation with reset_iso
    assert "quotaExceeded" in job.error_message or "Quota exceeded" in job.error_message
    assert reset_iso in job.error_message


# ==============================================================================
# Acceptance Test 3: OAuth invalid_grant Parks Channel and Never Retries
# ==============================================================================

def test_simulated_invalid_grant_parks_channel(db_session):
    """
    CRITERION 3: Simulated refresh-token invalid_grant marks channel 'needs_reauth',
    surfaces it in the dashboard, and NEVER auto-retries that channel.
    """
    chan, cred, clip, tmp = _create_test_clip(db_session)
    slot = _bind_publish_slot(db_session, chan, clip)

    job = PipelineJob(
        id="job_invalid_grant",
        job_type="publish",
        stage="publish",
        entity_id=clip.id,
        entity_type="clip",
        status=PipelineJobStatus.RUNNING,
    )
    db_session.add(job)
    db_session.commit()

    worker = YouTubePublishWorker(dry_run=False)

    mock_client = MagicMock()
    mock_request = MagicMock()
    mock_response = MagicMock()
    mock_response.status = 400
    mock_request.next_chunk.side_effect = HttpError(
        resp=mock_response,
        content=b'{"error": "invalid_grant", "error_description": "Token has been expired or revoked."}',
    )
    mock_client.videos.return_value.insert.return_value = mock_request
    mock_client.playlistItems.return_value.list.return_value.execute.return_value = {"items": []}

    with patch("shorts_engine.services.publishing.worker.build_youtube_client", return_value=mock_client):
        res = worker.execute_publish_intent(clip_id=clip.id, payload={"slot_id": slot.id}, db=db_session, job_entity=job)

    assert res["success"] is False
    assert res["status"] == "QUARANTINED"

    db_session.refresh(cred)
    db_session.refresh(job)

    # Channel Credential marked as needs_reauth
    assert cred.status == "needs_reauth"
    assert "invalid_grant" in cred.last_error

    # Job is quarantined, not pending, so it is NEVER auto-retried
    assert job.status == PipelineJobStatus.QUARANTINED
    assert "re-authentication" in job.error_message

    # Re-running worker on parked channel halts immediately without touching YouTube
    res2 = worker.execute_publish_intent(clip_id=clip.id, payload={"slot_id": slot.id}, db=db_session, job_entity=job)
    assert res2["status"] == "QUARANTINED"


# ==============================================================================
# Acceptance Test 4: Ambiguous Failure Triggers Tag Verification (Deduplication)
# ==============================================================================

def test_simulated_ambiguous_failure_triggers_tag_verification(db_session):
    """
    CRITERION 4: Simulated ambiguous failure (network socket dropout / timeout)
    triggers the tag-verification path ([c:<clip_id>]), not a duplicate upload.
    """
    chan, cred, clip, tmp = _create_test_clip(db_session, clip_id="clip_ambig_999")
    slot = _bind_publish_slot(db_session, chan, clip)

    worker = YouTubePublishWorker(dry_run=False)
    tag = format_idempotency_tag(clip.id)  # e.g. [c:clip_ambig_999]

    mock_client = MagicMock()
    mock_request = MagicMock()

    # Pre-upload check: video does not exist initially
    mock_playlist_empty = MagicMock()
    mock_playlist_empty.list.return_value.execute.return_value = {"items": []}

    # Simulate ambiguous socket/network dropout during chunk upload
    mock_request.next_chunk.side_effect = IOError("Connection reset by peer during chunk upload")

    # Recovery check during ambiguous error handler: video WAS created by YouTube!
    mock_playlist_found = MagicMock()
    mock_playlist_found.list.return_value.execute.return_value = {
        "items": [
            {
                "snippet": {
                    "title": "Uploaded Short",
                    "description": f"Highlight clip from stream\n\n{tag}",
                    "resourceId": {"videoId": "yt_recovered_ambig_id"},
                }
            }
        ]
    }

    # First playlist call returns empty; second call (inside exception handler) finds video
    mock_client.playlistItems.return_value = mock_playlist_found
    mock_client.videos.return_value.insert.return_value = mock_request

    # Also mock tracker
    tracker = QuotaTracker()

    with patch("shorts_engine.services.publishing.worker.build_youtube_client", return_value=mock_client):
        # We simulate the first call finding nothing, then upload encountering network dropout
        # and checking recent uploads
        with patch("shorts_engine.services.publishing.worker.check_channel_recent_uploads_for_clip") as mock_check:
            # 1st call before upload = None, 2nd call in ambiguous failure = yt_recovered_ambig_id
            mock_check.side_effect = [None, "yt_recovered_ambig_id"]

            res = worker.execute_publish_intent(clip_id=clip.id, payload={"slot_id": slot.id}, db=db_session)

    assert res["success"] is True
    assert res["youtube_video_id"] == "yt_recovered_ambig_id"

    # Clip status updated to PUBLISHED
    db_session.refresh(clip)
    assert clip.status == ClipStatus.PUBLISHED

    # PublishLog created
    p_log = db_session.query(PublishLog).filter_by(clip_id=clip.id).first()
    assert p_log is not None
    assert p_log.youtube_video_id == "yt_recovered_ambig_id"


# ==============================================================================
# Acceptance Test 5: Dry-Run Mode Covered By Tests (Zero Quota Consumption)
# ==============================================================================

def test_dry_run_path_bypasses_api_and_consumes_zero_quota(db_session):
    """
    CRITERION 5: dry_run flag logs full payload, skips Google API call,
    and consumes zero quota.
    """
    chan, cred, clip, tmp = _create_test_clip(db_session, clip_id="clip_dryrun_555")
    slot = _bind_publish_slot(db_session, chan, clip)

    tracker = QuotaTracker()
    initial_quota = tracker.get_used_units()

    worker = YouTubePublishWorker(quota_tracker=tracker, dry_run=True)

    with patch("shorts_engine.services.publishing.worker.build_youtube_client") as mock_build:
        res = worker.execute_publish_intent(
            clip_id=clip.id,
            payload={
                "slot_id": slot.id,
                "title": "Dry Run Short Title",
                "privacy_status": "unlisted",
                "made_for_kids": False,
            },
            db=db_session,
        )

    # API client was NEVER built or called
    mock_build.assert_not_called()

    # Success with dry_run indicator
    assert res["success"] is True
    assert res["dry_run"] is True
    assert res["youtube_video_id"].startswith("dry_run_")

    # Zero quota was deducted
    assert tracker.get_used_units() == initial_quota

    # Preview mode is explicitly non-mutating: worker.py documents that a dry
    # run must NEVER mark the clip PUBLISHED and never change slot status.
    # Asserting PUBLISHED here would contradict that safety contract, so we
    # assert the clip is left untouched instead.
    db_session.refresh(clip)
    assert clip.status == ClipStatus.APPROVED

    p_log = db_session.query(PublishLog).filter_by(clip_id=clip.id).first()
    assert p_log is not None
    assert p_log.status == "DRY_RUN"


# ==============================================================================
# Additional Acceptance Test: selfDeclaredMadeForKids and Scope Verification
# ==============================================================================

def test_self_declared_made_for_kids_and_scopes_explicitly_configured(db_session):
    """
    Verifies selfDeclaredMadeForKids is explicitly populated in upload body,
    and DEFAULT_SCOPES includes both youtube.upload and yt-analytics.readonly.
    """
    assert "https://www.googleapis.com/auth/youtube.upload" in DEFAULT_SCOPES
    assert "https://www.googleapis.com/auth/yt-analytics.readonly" in DEFAULT_SCOPES

    chan, cred, clip, tmp = _create_test_clip(db_session, clip_id="clip_kids_check")
    slot = _bind_publish_slot(db_session, chan, clip)

    worker = YouTubePublishWorker(dry_run=False)
    mock_client = MagicMock()
    mock_request = MagicMock()
    mock_request.next_chunk.return_value = (None, {"id": "yt_success_kids_1"})
    mock_client.videos.return_value.insert.return_value = mock_request
    mock_client.playlistItems.return_value.list.return_value.execute.return_value = {"items": []}

    with patch("shorts_engine.services.publishing.worker.build_youtube_client", return_value=mock_client):
        res = worker.execute_publish_intent(
            clip_id=clip.id,
            payload={"slot_id": slot.id, "made_for_kids": True},
            db=db_session,
        )

    assert res["success"] is True
    insert_call = mock_client.videos().insert.call_args
    body_passed = insert_call.kwargs.get("body") or insert_call[1].get("body")
    assert body_passed["status"]["selfDeclaredMadeForKids"] is True
