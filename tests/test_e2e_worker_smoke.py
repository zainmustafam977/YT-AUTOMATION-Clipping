"""
Permanent End-to-End Worker Pipeline Smoke Test.

Validates the full decoupled pipeline:
  Watch Folder Drop -> Ingest -> Transcribe -> Select -> Reframe & Captions -> Render -> Review Queue

This test caught 3 production defects that unit tests missed:
  1. Scoped session detachment during stage telemetry / cost checks.
  2. CropPlan JSON serialization in crop_plan_stage.
  3. Word-level dictionary vs WordSegment object handling in caption_stage.

MANUAL INVOCATION:
  PowerShell:
    $env:RUN_WORKER_SMOKE_TEST = "1"
    .venv\\Scripts\\python.exe -m pytest tests/test_e2e_worker_smoke.py -v -s
    Remove-Item env:RUN_WORKER_SMOKE_TEST
"""

import os
import sys
import json
import time
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
import pytest

from shorts_engine.config.settings import settings
from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import (
    SourceVideo,
    PipelineJob,
    PipelineJobStatus,
    ClipCandidate,
    Clip,
    ClipStatus,
    Transcript,
)
from shorts_engine.services.ingestion.watch_folder import WatchFolderDaemon
from shorts_engine.services.worker.daemon import WorkerDaemon


@pytest.mark.skipif(
    os.getenv("RUN_WORKER_SMOKE_TEST") != "1",
    reason="Manual E2E worker smoke test. To run: set RUN_WORKER_SMOKE_TEST=1 in environment.",
)
def test_e2e_worker_pipeline_smoke(tmp_path):
    """End-to-end integration smoke test running real media through WorkerDaemon."""
    smoke_start_utc = datetime.utcnow()
    watch_folder = Path(settings.STORAGE_DIR) / "watch_folder"
    watch_folder.mkdir(parents=True, exist_ok=True)

    wav_path = tmp_path / "smoke_speech.wav"
    mp4_path = watch_folder / "e2e_smoke_speech.mp4"

    # 1. Synthesize clear speech audio
    ps_cmd = (
        f"Add-Type -AssemblyName System.Speech; "
        f"$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{str(wav_path).replace(chr(92), '/')}'); "
        f"$s.Speak('Welcome to the automated shorts engine pipeline. In this test video, we demonstrate artificial intelligence video clipping, automated transcription, speech recognition, and high performance video reframing. Why is video automation so powerful? Because it allows creators to scale their content effortlessly. What is the secret to high engagement? Remember, quality and consistency are the two most important factors for viral reach! Thank you for watching, and stay tuned for more insights.'); "
        f"$s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], check=True, capture_output=True)
    assert wav_path.exists() and wav_path.stat().st_size > 0

    # 2. Mux with 720p test video
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30",
            "-i", str(wav_path),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest", str(mp4_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert mp4_path.exists() and mp4_path.stat().st_size > 0

    video_id = None
    created_clip_ids = []

    try:
        # 3. Detect via WatchFolderDaemon
        daemon = WatchFolderDaemon(
            watch_dir=watch_folder,
            stable_time_sec=0.1,
            poll_interval_sec=0.1,
        )
        daemon.scan_directory_candidates()
        daemon.poll_and_process_stable_files()
        time.sleep(0.2)
        daemon.poll_and_process_stable_files()

        session = SessionLocal()
        try:
            video = (
                session.query(SourceVideo)
                .filter(SourceVideo.downloaded_path == os.path.normpath(str(mp4_path)))
                .first()
            )
            assert video is not None, "SourceVideo was not created by WatchFolderDaemon"
            video_id = video.youtube_video_id
        finally:
            session.close()

        # 4. Process all stages via WorkerDaemon
        worker = WorkerDaemon(poll_interval_sec=0.1)
        worker.acquire_sqlite_lock()
        try:
            for _ in range(50):
                session = SessionLocal()
                try:
                    pending = (
                        session.query(PipelineJob)
                        .filter(PipelineJob.status.in_([PipelineJobStatus.PENDING, PipelineJobStatus.RUNNING]))
                        .count()
                    )
                finally:
                    session.close()

                if pending == 0:
                    break

                job = worker.run_once()
                if not job:
                    time.sleep(0.3)
        finally:
            worker.release_sqlite_lock()

        # 5. Assertions
        session = SessionLocal()
        try:
            jobs = (
                session.query(PipelineJob)
                .filter(PipelineJob.created_at >= smoke_start_utc)
                .order_by(PipelineJob.created_at)
                .all()
            )
            job_stages = {j.stage: j.status for j in jobs}

            assert "ingest" in job_stages and job_stages["ingest"] == PipelineJobStatus.DONE
            assert "transcribe" in job_stages and job_stages["transcribe"] == PipelineJobStatus.DONE
            assert "select" in job_stages and job_stages["select"] == PipelineJobStatus.DONE
            assert "reframe" in job_stages and job_stages["reframe"] == PipelineJobStatus.DONE
            assert "captions" in job_stages and job_stages["captions"] == PipelineJobStatus.DONE
            assert "render" in job_stages and job_stages["render"] == PipelineJobStatus.DONE

            # Check review queue clip
            clips = (
                session.query(Clip)
                .join(ClipCandidate)
                .join(SourceVideo)
                .filter(SourceVideo.youtube_video_id == video_id)
                .all()
            )
            assert len(clips) >= 1
            for clip in clips:
                created_clip_ids.append(clip.id)
                assert clip.status == ClipStatus.WAITING_APPROVAL
                assert clip.rendered_path is not None
                rendered_file = Path(clip.rendered_path)
                assert rendered_file.exists() and rendered_file.stat().st_size > 0
        finally:
            session.close()

    finally:
        # Clean up database records and test files
        session = SessionLocal()
        try:
            if video_id:
                sv = session.query(SourceVideo).filter_by(youtube_video_id=video_id).first()
                if sv:
                    for cand in session.query(ClipCandidate).filter_by(source_video_id=sv.id).all():
                        for c in session.query(Clip).filter_by(clip_candidate_id=cand.id).all():
                            session.query(PipelineJob).filter_by(entity_id=c.id).delete()
                            if c.rendered_path and Path(c.rendered_path).exists():
                                Path(c.rendered_path).unlink(missing_ok=True)
                            session.delete(c)
                        session.delete(cand)
                    session.query(PipelineJob).filter_by(entity_id=sv.youtube_video_id).delete()
                    session.query(Transcript).filter_by(source_video_id=sv.id).delete()
                    session.delete(sv)
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()

        if mp4_path.exists():
            mp4_path.unlink(missing_ok=True)
        if wav_path.exists():
            wav_path.unlink(missing_ok=True)
