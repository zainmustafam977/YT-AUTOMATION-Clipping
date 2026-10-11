"""
Status route: live pipeline summary shown on the dashboard home page.
Returns counts for videos, clips, review queue, quota usage, recent failures,
and handles manual video processing triggers.
"""

import os
import json
import re
import shutil
import asyncio
import threading
import logging
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from shorts_engine.config.settings import settings
from shorts_engine.config.logging_config import reload_runtime_settings
from shorts_engine.dashboard.auth import require_operator, require_viewer, verify_csrf, get_csrf_token
from shorts_engine.db.session import SessionLocal, get_db
from shorts_engine.db.models import SourceVideo, SourceVideoStatus, Clip, ClipStatus, ClipCandidate, Channel, User
from shorts_engine.services.publishing.quota import QuotaTracker
from shorts_engine.services.llm.quota_tracker import usage_tracker
from shorts_engine.services.clip_selection.selector import parse_ai_provenance
from shorts_engine.services.telemetry.progress_tracker import job_progress_tracker
from shorts_engine.orchestration.runner import (
    PipelineRunner,
    get_video_lock,
    release_video_lock,
    clear_cancellation,
)
from shorts_engine.services.worker.runner import reconcile_stale_claims
from shorts_engine.services.execution.watchdog import SubprocessWatchdog

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["settings"] = settings


def get_ai_provider_status() -> Dict[str, Any]:
    """Inspects configured LLM settings and API keys to report active provider health."""
    provider = (settings.LLM_PROVIDER or "auto").lower()
    has_groq = bool(settings.GROQ_API_KEY and len(settings.GROQ_API_KEY.strip()) > 5)
    has_gemini = bool(settings.GEMINI_API_KEY and len(settings.GEMINI_API_KEY.strip()) > 5)
    has_openrouter = bool(settings.OPENROUTER_API_KEY and len(settings.OPENROUTER_API_KEY.strip()) > 5)
    has_openai = bool(settings.OPENAI_API_KEY and len(settings.OPENAI_API_KEY.strip()) > 5)

    if provider == "groq":
        active_name = "⚡ Groq LPU"
        model = settings.GROQ_MODEL_NAME
        healthy = has_groq
    elif provider == "openrouter":
        active_name = "🌐 OpenRouter"
        model = settings.OPENROUTER_MODEL_NAME
        healthy = has_openrouter
    elif provider == "openai":
        active_name = "🟢 OpenAI"
        model = settings.OPENAI_MODEL_NAME
        healthy = has_openai
    elif provider == "gemini":
        active_name = "🤖 Gemini"
        model = settings.LLM_MODEL_NAME
        healthy = has_gemini
    else:
        # "auto" tiered routing: Groq -> Gemini -> OpenRouter -> OpenAI -> Heuristic
        if has_groq:
            active_name = "⚡ Groq (Tier 1 Free LPU)"
            model = settings.GROQ_MODEL_NAME
            healthy = True
        elif has_gemini:
            active_name = "🤖 Gemini (Tier 2 Free)"
            model = settings.LLM_MODEL_NAME
            healthy = True
        elif has_openrouter:
            active_name = "🌐 OpenRouter (Tier 3 Free)"
            model = settings.OPENROUTER_MODEL_NAME
            healthy = True
        elif has_openai:
            active_name = "🟢 OpenAI (Tier 4 Paid)"
            model = settings.OPENAI_MODEL_NAME
            healthy = True
        else:
            active_name = "⚙️ Algorithmic Heuristic Fallback"
            model = "Rule-based pause detector"
            healthy = True

    return {
        "provider": active_name,
        "model": model,
        "healthy": healthy,
        "has_groq": has_groq,
        "has_gemini": has_gemini,
        "has_openrouter": has_openrouter,
        "has_openai": has_openai,
        "failover_enabled": settings.LLM_FALLBACK_ON_RATE_LIMIT,
    }



def get_storage_stats() -> Dict[str, Any]:
    """Calculates disk space metrics and directory usage under storage/."""
    storage_path = Path(settings.STORAGE_DIR)
    total_gb = 0.0
    used_gb = 0.0
    free_gb = 0.0
    percent_used = 0.0

    try:
        total_b, used_b, free_b = shutil.disk_usage(storage_path)
        total_gb = round(total_b / (1024**3), 1)
        used_gb = round(used_b / (1024**3), 1)
        free_gb = round(free_b / (1024**3), 1)
        percent_used = round((used_b / total_b) * 100, 1) if total_b > 0 else 0.0
    except Exception as e:
        logger.warning("Could not query disk_usage for %s: %s", storage_path, e)

    def _calc_dir_mb(p: Path) -> float:
        if not p.exists():
            return 0.0
        try:
            total_b = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            return round(total_b / (1024 * 1024), 1)
        except Exception:
            return 0.0

    raw_mb = _calc_dir_mb(Path(settings.RAW_DIR))
    outputs_mb = _calc_dir_mb(Path(settings.OUTPUT_DIR))
    temp_mb = _calc_dir_mb(Path(settings.TEMP_DIR))

    return {
        "total_gb": total_gb,
        "used_gb": used_gb,
        "free_gb": free_gb,
        "percent_used": percent_used,
        "is_low_space": free_gb < 10.0 or percent_used > 90.0,
        "raw_mb": raw_mb,
        "outputs_mb": outputs_mb,
        "temp_mb": temp_mb,
        "storage_path": str(storage_path),
    }


def _run_pipeline_background(
    video_url_or_id: str,
    channel_id: Optional[str],
    dry_run: bool,
    caption_preset: Optional[str] = None,
    runtime_overrides: Optional[Dict[str, Any]] = None,
):
    """Executes pipeline in an isolated thread with its own database session."""
    # Ensure any stale cancellation flag from a previous cancellation or lock release is cleared
    m = re.search(r"(?:v=|\/|youtu\.be\/|shorts\/)([0-9A-Za-z_-]{11})", video_url_or_id)
    vid = m.group(1) if m else video_url_or_id
    clear_cancellation(vid)
    clear_cancellation(video_url_or_id)

    db = SessionLocal()
    job = None
    try:
        from shorts_engine.services.queue.durable_queue import durable_job_queue

        channel = db.query(Channel).filter_by(id=channel_id).first() if channel_id else None

        # Track execution inside durable job queue
        job = durable_job_queue.enqueue(
            job_type="INGEST_VIDEO",
            entity_id=vid,
            entity_type="source_video",
            payload={
                "video_url_or_id": video_url_or_id,
                "channel_id": channel_id,
                "dry_run": dry_run,
                "caption_preset": caption_preset,
                "runtime_overrides": runtime_overrides,
            },
            db=db,
        )
        durable_job_queue.claim_next_job(job_type="INGEST_VIDEO", db=db)

        runner = PipelineRunner(db)
        logger.info(
            "Background processing started for video: %s (job=%s, dry_run=%s, caption_preset=%s, overrides=%s)",
            video_url_or_id,
            job.id,
            dry_run,
            caption_preset,
            bool(runtime_overrides),
        )
        res = runner.run_pipeline_for_video(
            video_url_or_id,
            channel=channel,
            dry_run=dry_run,
            caption_preset=caption_preset,
            runtime_overrides=runtime_overrides,
        )
        durable_job_queue.complete_job(job.id, result=res if isinstance(res, dict) else {}, db=db)
        logger.info("Background processing completed successfully for video: %s (job=%s)", video_url_or_id, job.id)
    except Exception as e:
        logger.exception("Background processing error for video %s: %s", video_url_or_id, e)
        if job:
            try:
                from shorts_engine.services.queue.durable_queue import durable_job_queue
                durable_job_queue.fail_job(job.id, str(e), db=db)
            except Exception:
                pass
    finally:
        db.close()


def compute_pipeline_stages(
    status_val: str,
    total_clips: int,
    waiting_clips: int,
    approved_clips: int,
    published_clips: int,
    failed_clips: int,
    is_skipped: bool,
) -> Dict[str, str]:
    """
    Computes visual state ('done', 'active', 'pending', 'failed') for each stage:
    ingest, transcribe, select, reframe, render, gate.
    """
    stages = {
        "ingest": "pending",
        "transcribe": "pending",
        "select": "pending",
        "reframe": "pending",
        "render": "pending",
        "gate": "pending",
    }
    if is_skipped:
        stages["ingest"] = "done"
        return stages

    if status_val == SourceVideoStatus.DETECTED.value:
        stages["ingest"] = "active"
    elif status_val == SourceVideoStatus.DOWNLOADING.value:
        stages["ingest"] = "active"
    elif status_val == SourceVideoStatus.TRANSCRIBING.value:
        stages["ingest"] = "done"
        stages["transcribe"] = "active"
    elif status_val == SourceVideoStatus.ANALYZING.value:
        stages["ingest"] = "done"
        stages["transcribe"] = "done"
        stages["select"] = "active"
    elif status_val == SourceVideoStatus.SEGMENTS_SELECTED.value:
        stages["ingest"] = "done"
        stages["transcribe"] = "done"
        stages["select"] = "done"
        stages["reframe"] = "done"
        stages["render"] = "active"
    elif status_val in (SourceVideoStatus.DONE.value, "COMPLETED"):
        stages["ingest"] = "done"
        stages["transcribe"] = "done"
        stages["select"] = "done"
        stages["reframe"] = "done"
        stages["render"] = "done"
        stages["gate"] = "done" if failed_clips == 0 else "failed"
    elif status_val == SourceVideoStatus.FAILED.value:
        if total_clips > 0:
            stages["ingest"] = "done"
            stages["transcribe"] = "done"
            stages["select"] = "done"
            stages["reframe"] = "done"
            stages["render"] = "failed"
        else:
            stages["ingest"] = "failed"
    return stages


def build_recent_videos_payload(
    db: Session,
    limit: int = 50,
    date_filter: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Builds an enriched, batched list of recent source videos with thumbnails,
    origin detection, child clip breakdown, timing telemetry, candidate summaries,
    and optional date-range filtering.
    Executes exactly 2 queries regardless of video count (eliminating N+1 query loops).
    """
    from datetime import timedelta

    query = (
        db.query(SourceVideo)
        .options(joinedload(SourceVideo.channel), joinedload(SourceVideo.transcript))
    )

    now_naive = datetime.utcnow()
    df = (date_filter or "all").lower()

    if df == "today":
        start_of_day = now_naive.replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.filter(SourceVideo.created_at >= start_of_day)
    elif df == "yesterday":
        start_of_today = now_naive.replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_yesterday = start_of_today - timedelta(days=1)
        query = query.filter(SourceVideo.created_at >= start_of_yesterday, SourceVideo.created_at < start_of_today)
    elif df == "7days":
        seven_days_ago = now_naive - timedelta(days=7)
        query = query.filter(SourceVideo.created_at >= seven_days_ago)
    elif df == "30days":
        thirty_days_ago = now_naive - timedelta(days=30)
        query = query.filter(SourceVideo.created_at >= thirty_days_ago)
    elif df == "custom" and start_date:
        try:
            s_dt = datetime.fromisoformat(start_date.split("T")[0])
            query = query.filter(SourceVideo.created_at >= s_dt)
            if end_date:
                e_dt = datetime.fromisoformat(end_date.split("T")[0]) + timedelta(days=1)
                query = query.filter(SourceVideo.created_at < e_dt)
        except Exception as e:
            logger.warning("Invalid custom date range (%s - %s): %s", start_date, end_date, e)

    recent_videos_raw = query.order_by(SourceVideo.updated_at.desc()).limit(limit).all()
    if not recent_videos_raw:
        return []

    v_ids = [v.id for v in recent_videos_raw]

    # Query 2: Single batched eager-loaded fetch of all candidates & clips
    all_candidates = (
        db.query(ClipCandidate)
        .options(joinedload(ClipCandidate.clip))
        .filter(ClipCandidate.source_video_id.in_(v_ids))
        .order_by(ClipCandidate.score.desc())
        .all()
    )

    # Group candidates by source_video_id in memory
    candidates_by_vid: Dict[str, List[ClipCandidate]] = {vid: [] for vid in v_ids}
    for cand in all_candidates:
        candidates_by_vid.setdefault(cand.source_video_id, []).append(cand)

    stage_progress_map = {
        SourceVideoStatus.DETECTED.value: 5,
        SourceVideoStatus.DOWNLOADING.value: 25,
        SourceVideoStatus.TRANSCRIBING.value: 50,
        SourceVideoStatus.ANALYZING.value: 70,
        SourceVideoStatus.SEGMENTS_SELECTED.value: 85,
        SourceVideoStatus.DONE.value: 100,
        "COMPLETED": 100,
        SourceVideoStatus.FAILED.value: 100,
    }

    stage_label_map = {
        SourceVideoStatus.DETECTED.value: "Detected in Feed",
        SourceVideoStatus.DOWNLOADING.value: "Downloading Video & Audio",
        SourceVideoStatus.TRANSCRIBING.value: "Transcribing with Whisper",
        SourceVideoStatus.ANALYZING.value: "Analyzing Virality & Hooks",
        SourceVideoStatus.SEGMENTS_SELECTED.value: "Selecting & Cropping Clips",
        SourceVideoStatus.DONE.value: "Pipeline Complete",
        "COMPLETED": "Pipeline Complete",
        SourceVideoStatus.FAILED.value: "Pipeline Error",
    }

    result = []
    now = datetime.utcnow()

    for v in recent_videos_raw:
        cands = candidates_by_vid.get(v.id, [])
        total_clips = len(cands)
        waiting_clips = sum(1 for c in cands if c.clip and c.clip.status == ClipStatus.WAITING_APPROVAL)
        approved_clips = sum(1 for c in cands if c.clip and c.clip.status == ClipStatus.APPROVED)
        published_clips = sum(1 for c in cands if c.clip and c.clip.status == ClipStatus.PUBLISHED)
        failed_clips = sum(1 for c in cands if c.clip and c.clip.status == ClipStatus.FAILED)

        # Check if skipped because already a Short
        err = v.error_message or ""
        is_skipped_short = err.startswith("Skipped: already a YouTube Short")

        # Elapsed / Turnaround time calculation
        turnaround_text = "—"
        is_stale_or_interrupted = False
        if v.status in (
            SourceVideoStatus.DOWNLOADING,
            SourceVideoStatus.TRANSCRIBING,
            SourceVideoStatus.ANALYZING,
            SourceVideoStatus.SEGMENTS_SELECTED,
        ):
            ref_time = v.updated_at or v.created_at
            age_sec = int(max(0, (now - ref_time).total_seconds())) if ref_time else 0
            if age_sec > 600:
                v_lock = get_video_lock(v.youtube_video_id)
                lock_acquired = v_lock.acquire(blocking=False)
                if lock_acquired:
                    try:
                        v_lock.release()
                    except Exception:
                        pass
                    is_stale_or_interrupted = True
                    turnaround_text = f"Interrupted ({age_sec // 60}m ago)"

            if not is_stale_or_interrupted:
                if v.created_at:
                    elapsed_sec = int(max(0, (now - v.created_at).total_seconds()))
                    if elapsed_sec < 60:
                        turnaround_text = f"{elapsed_sec}s running"
                    else:
                        turnaround_text = f"{elapsed_sec // 60}m {elapsed_sec % 60}s running"
        elif v.status == SourceVideoStatus.DONE:
            if is_skipped_short:
                turnaround_text = "Skipped (Already Short)"
            elif v.created_at and v.updated_at and v.updated_at > v.created_at:
                diff = int((v.updated_at - v.created_at).total_seconds())
                if diff < 60:
                    turnaround_text = f"Finished in {diff}s"
                else:
                    turnaround_text = f"Finished in {diff // 60}m {diff % 60}s"
            else:
                turnaround_text = "Completed"
        elif v.status == SourceVideoStatus.FAILED:
            turnaround_text = "Failed"

        # Origin attribution
        origin = "Watched Channel" if (v.channel and v.channel.watch_enabled) else "Manual Trigger"

        # Duration formatted (e.g. 24:12 or 45s)
        dur_sec = v.duration_sec or 0
        if dur_sec >= 60:
            dur_fmt = f"{dur_sec // 60}:{dur_sec % 60:02d}"
        elif dur_sec > 0:
            dur_fmt = f"{dur_sec}s"
        else:
            dur_fmt = "—"

        # Transcript & raw size telemetry
        word_count = 0
        lang = "en"
        if v.transcript:
            lang = v.transcript.language or "en"
            if v.transcript.word_level_json:
                try:
                    wdata = json.loads(v.transcript.word_level_json)
                    word_count = len(wdata) if isinstance(wdata, list) else 0
                except Exception:
                    word_count = 0

        raw_size_mb = "—"
        if v.downloaded_path and os.path.exists(v.downloaded_path):
            try:
                sz = os.path.getsize(v.downloaded_path)
                raw_size_mb = f"{round(sz / (1024 * 1024), 1)} MB"
            except Exception:
                pass

        # Child candidate cards for the expandable drawer
        candidates_summary = []
        for c in cands:
            c_dur = round((c.end_ms - c.start_ms) / 1000.0, 1) if (c.end_ms and c.start_ms) else 0.0
            prov = parse_ai_provenance(c.reason)
            candidates_summary.append({
                "candidate_id": c.id,
                "clip_id": c.clip.id if c.clip else None,
                "hook_text": c.hook_text or "Clip candidate hook",
                "payoff_text": c.payoff_text or "",
                "score": round(c.score, 1) if c.score else 0.0,
                "reason": prov["clean_reason"],
                "ai_provider": prov["provider"],
                "ai_model": prov["model"],
                "ai_badge": prov["badge"],
                "is_heuristic": prov["is_heuristic"],
                "latency_ms": prov.get("latency_ms", 0),
                "is_failover": prov.get("is_failover", False),
                "status": c.clip.status.value if c.clip else "UNPROCESSED",
                "duration_sec": c_dur,
                "start_sec": round(c.start_ms / 1000.0, 1) if c.start_ms else 0.0,
                "end_sec": round(c.end_ms / 1000.0, 1) if c.end_ms else 0.0,
                "has_rendered": bool(c.clip and c.clip.rendered_path),
            })

        # Determine primary AI integration used for this video
        v_latency_ms = 0
        v_is_failover = False
        v_is_heuristic = False
        if candidates_summary:
            v_ai_badge = candidates_summary[0]["ai_badge"]
            v_ai_provider = candidates_summary[0]["ai_provider"]
            v_ai_model = candidates_summary[0]["ai_model"]
            v_latency_ms = candidates_summary[0].get("latency_ms", 0)
            v_is_failover = any(c.get("is_failover", False) for c in candidates_summary)
            v_is_heuristic = candidates_summary[0].get("is_heuristic", False)
        elif v.status == SourceVideoStatus.FAILED:
            v_ai_badge = "❌ Error"
            v_ai_provider = "error"
            v_ai_model = "failed"
        elif v.status in (SourceVideoStatus.DETECTED, SourceVideoStatus.DOWNLOADING, SourceVideoStatus.TRANSCRIBING, SourceVideoStatus.ANALYZING):
            ai_stat = get_ai_provider_status()
            v_ai_badge = f"{ai_stat['provider']} (Queued)"
            v_ai_provider = "pending"
            v_ai_model = ai_stat['model']
        else:
            v_ai_badge = "—"
            v_ai_provider = "unknown"
            v_ai_model = "unknown"

        # Check if job_progress_tracker has live metrics for this video (by youtube_video_id or v.id)
        live_prog = job_progress_tracker.get_progress(v.youtube_video_id) or job_progress_tracker.get_progress(v.id)

        stage_label = stage_label_map.get(v.status.value, v.status.value)
        progress_pct = stage_progress_map.get(v.status.value, 10)
        speed = None
        eta_seconds = None
        progress_detail = None

        if live_prog and v.status in (
            SourceVideoStatus.DOWNLOADING,
            SourceVideoStatus.TRANSCRIBING,
            SourceVideoStatus.ANALYZING,
            SourceVideoStatus.SEGMENTS_SELECTED,
        ):
            progress_pct = live_prog.get("percent", progress_pct)
            if live_prog.get("detail"):
                stage_label = f"{live_prog.get('stage', v.status.value)}: {live_prog.get('detail')}"
                progress_detail = live_prog.get("detail")
            speed = live_prog.get("speed")
            eta_seconds = live_prog.get("eta_seconds")

        pipeline_stages = compute_pipeline_stages(
            status_val=v.status.value,
            total_clips=total_clips,
            waiting_clips=waiting_clips,
            approved_clips=approved_clips,
            published_clips=published_clips,
            failed_clips=failed_clips,
            is_skipped=is_skipped_short,
        )

        result.append({
            "id": v.id,
            "youtube_video_id": v.youtube_video_id,
            "title": v.title or f"Processing Ingestion ({v.youtube_video_id})...",
            "thumbnail_url": f"/api/thumbnails/{v.youtube_video_id}",
            "channel_name": v.channel.name if v.channel else "Default Channel",
            "channel_yt_id": v.channel.youtube_channel_id if v.channel else None,
            "channel_id": v.channel_id,
            "origin": origin,
            "status": v.status.value,
            "stage_label": stage_label,
            "pipeline_stages": pipeline_stages,
            "progress_pct": progress_pct,
            "speed": speed,
            "eta_seconds": eta_seconds,
            "progress_detail": progress_detail,
            "duration_sec": dur_sec,
            "duration_formatted": dur_fmt,
            "turnaround_text": turnaround_text,
            "is_skipped_short": is_skipped_short,
            "total_clips": total_clips,
            "waiting_clips": waiting_clips,
            "approved_clips": approved_clips,
            "published_clips": published_clips,
            "failed_clips": failed_clips,
            "retry_count": v.retry_count,
            "error_message": v.error_message,
            "ai_badge": v_ai_badge,
            "ai_provider": v_ai_provider,
            "ai_model": v_ai_model,
            "latency_ms": v_latency_ms,
            "is_failover": v_is_failover,
            "is_heuristic": v_is_heuristic,
            "raw_size_mb": raw_size_mb,
            "transcript_words": word_count,
            "language": lang,
            "runtime_overrides": v.runtime_overrides,
            "has_tuning_overrides": bool(v.runtime_overrides),
            "updated_at": v.updated_at.strftime("%Y-%m-%d %H:%M") if v.updated_at else "—",
            "updated_at_raw": v.updated_at.isoformat() if v.updated_at else None,
            "can_cancel": (not is_stale_or_interrupted) and (v.status in (
                SourceVideoStatus.DETECTED,
                SourceVideoStatus.DOWNLOADING,
                SourceVideoStatus.TRANSCRIBING,
                SourceVideoStatus.ANALYZING,
                SourceVideoStatus.SEGMENTS_SELECTED,
            )),
            "can_retry": v.status == SourceVideoStatus.FAILED,
            "can_resume": (
                v.status == SourceVideoStatus.FAILED
                or is_stale_or_interrupted
            ) and (v.status != SourceVideoStatus.DONE),
            "is_interrupted": is_stale_or_interrupted,
            "is_running": (not is_stale_or_interrupted) and (v.status in (
                SourceVideoStatus.DETECTED,
                SourceVideoStatus.DOWNLOADING,
                SourceVideoStatus.TRANSCRIBING,
                SourceVideoStatus.ANALYZING,
                SourceVideoStatus.SEGMENTS_SELECTED,
            )),
            "can_delete": (is_stale_or_interrupted or v.status not in (
                SourceVideoStatus.DOWNLOADING,
                SourceVideoStatus.TRANSCRIBING,
                SourceVideoStatus.ANALYZING,
                SourceVideoStatus.SEGMENTS_SELECTED,
            )) and (v.status in (SourceVideoStatus.FAILED, SourceVideoStatus.DONE, SourceVideoStatus.DETECTED)),
            "candidates": candidates_summary,
        })

    return result


@router.get("/", response_class=HTMLResponse, summary="Pipeline status dashboard")
def dashboard_status(
    request: Request,
    date_filter: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    reload_runtime_settings()
    tracker = QuotaTracker()
    yt_quota = tracker.get_usage_metrics(db=db)
    api_summary = usage_tracker.get_summary(
        youtube_used=yt_quota["used_units"],
        youtube_limit=settings.DAILY_QUOTA_LIMIT,
    )
    api_key_metrics = usage_tracker.get_per_key_metrics()

    channels = db.query(Channel).order_by(Channel.name.asc()).all()
    recent_videos = build_recent_videos_payload(
        db,
        limit=50,
        date_filter=date_filter,
        start_date=start_date,
        end_date=end_date,
    )

    ctx = {
        "request": request,
        "current_user": current_user,
        "csrf_token": get_csrf_token(request),
        "channels": channels,
        "recent_videos": recent_videos,
        "current_date_filter": date_filter or "all",
        "current_start_date": start_date or "",
        "current_end_date": end_date or "",
        "total_videos": db.query(SourceVideo).count(),
        "in_progress": db.query(SourceVideo).filter(
            SourceVideo.status.in_([
                SourceVideoStatus.DOWNLOADING,
                SourceVideoStatus.TRANSCRIBING,
                SourceVideoStatus.ANALYZING,
                SourceVideoStatus.SEGMENTS_SELECTED,
            ])
        ).count(),
        "waiting_approval": db.query(Clip).filter_by(status=ClipStatus.WAITING_APPROVAL).count(),
        "published_today": db.query(Clip).filter_by(status=ClipStatus.PUBLISHED).count(),
        "failed_clips": db.query(Clip).filter_by(status=ClipStatus.FAILED).count(),
        "failed_videos": db.query(SourceVideo).filter_by(status=SourceVideoStatus.FAILED).count(),
        "quota_used": api_summary["used_units"],
        "quota_limit": api_summary["limit_units"],
        "quota_pct": api_summary["pct"],
        "quota_warning": api_summary["warning"],
        "quota_subtext": api_summary["subtext"],
        "api_key_metrics": api_key_metrics,
        "storage_stats": get_storage_stats(),
        "ai_provider": get_ai_provider_status(),
        "msg": request.query_params.get("msg"),
        "msg_type": request.query_params.get("type", "success"),
        "settings": settings,
    }
    return templates.TemplateResponse(request=request, name="status.html", context=ctx)


@router.get("/api/estimate-clips", summary="Calculate optimal clip limits & model for video duration")
def api_estimate_clips(
    duration_sec: float,
    min_dur: float = 30.0,
    max_dur: float = 60.0,
):
    from shorts_engine.services.clip_selection.estimator import estimate_optimal_clip_count
    return estimate_optimal_clip_count(
        video_duration_sec=duration_sec,
        target_duration_min=min_dur,
        target_duration_max=max_dur,
    )


@router.post("/process-video", summary="Manually trigger pipeline on any YouTube URL or ID")
def manual_process_video(
    request: Request,
    video_input: str = Form(...),
    channel_id: Optional[str] = Form(default=None),
    caption_preset: Optional[str] = Form(default="hormozi_viral"),
    dry_run: Optional[str] = Form(default=None),
    force: Optional[str] = Form(default=None),
    register_channel: Optional[str] = Form(default=None),
    detected_channel_name: Optional[str] = Form(default=None),
    detected_channel_id: Optional[str] = Form(default=None),
    override_llm_provider: Optional[str] = Form(default=None),
    override_llm_model: Optional[str] = Form(default=None),
    override_llm_temperature: Optional[float] = Form(default=None),
    override_min_duration: Optional[int] = Form(default=None),
    override_max_duration: Optional[int] = Form(default=None),
    override_min_segments: Optional[int] = Form(default=None),
    override_max_segments: Optional[int] = Form(default=None),
    override_crf: Optional[int] = Form(default=None),
    override_preset: Optional[str] = Form(default=None),
    override_scale_algo: Optional[str] = Form(default=None),
    override_target_fps: Optional[int] = Form(default=None),
    override_audio_bitrate: Optional[str] = Form(default=None),
    override_enable_sfx: Optional[str] = Form(default=None),
    override_whisper_model: Optional[str] = Form(default=None),
    override_whisper_language: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    raw_input = video_input.strip()
    if not raw_input:
        raise HTTPException(status_code=422, detail="YouTube URL or Video ID cannot be empty")

    # Extract clean 11-char video ID or use raw input
    m = re.search(r"(?:v=|\/|youtu\.be\/|shorts\/)([0-9A-Za-z_-]{11})", raw_input)
    video_id = m.group(1) if m else raw_input

    # Check if video exists in DB or is a valid 11-char YouTube ID
    existing_in_db = db.query(SourceVideo).filter(
        (SourceVideo.id == video_id) | (SourceVideo.youtube_video_id == video_id) | (SourceVideo.youtube_video_id == raw_input)
    ).first()
    if not existing_in_db and not re.match(r"^[0-9A-Za-z_-]{11}$", video_id):
        if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
            return RedirectResponse(
                url="/?msg=Invalid+YouTube+URL+or+11-character+Video+ID&type=danger",
                status_code=303,
            )
        raise HTTPException(status_code=422, detail="Invalid YouTube URL or 11-character Video ID")

    # Handle automatic channel registration or auto-resolution if channel_id not provided
    clean_detected_name = detected_channel_name.strip() if detected_channel_name else None
    clean_detected_yt_id = (detected_channel_id or clean_detected_name).strip() if (detected_channel_id or clean_detected_name) else None

    # If no channel_id provided, attempt automatic resolution from YouTube oEmbed
    if not channel_id:
        if clean_detected_name:
            matched_ch = db.query(Channel).filter(
                (Channel.youtube_channel_id == clean_detected_yt_id) |
                (Channel.name.ilike(clean_detected_name)) |
                (Channel.youtube_channel_id == clean_detected_yt_id.lstrip("@") if clean_detected_yt_id else False)
            ).first()
            if not matched_ch:
                matched_ch = Channel(
                    name=clean_detected_name,
                    youtube_channel_id=clean_detected_yt_id or clean_detected_name,
                    watch_enabled=True,
                    human_review_required=True,
                )
                db.add(matched_ch)
                db.commit()
                db.refresh(matched_ch)
                logger.info("Auto-registered channel '%s' (%s) for video %s", clean_detected_name, clean_detected_yt_id, video_id)
            channel_id = matched_ch.id
        else:
            try:
                oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
                with httpx.Client(timeout=2.5) as client:
                    res = client.get(oembed_url)
                    if res.status_code == 200:
                        data = res.json()
                        author_name = (data.get("author_name") or "").strip()
                        author_url = data.get("author_url") or ""
                        chan_identifier = None
                        if author_url:
                            m_chan = re.search(r"/(?:channel/|c/|@)([0-9A-Za-z_.-]+)", author_url)
                            if m_chan:
                                matched = m_chan.group(1)
                                chan_identifier = matched if matched.startswith("UC") else f"@{matched}"
                        if author_name:
                            matched_ch = db.query(Channel).filter(
                                (Channel.youtube_channel_id == (chan_identifier or author_name)) |
                                (Channel.name.ilike(author_name)) |
                                (Channel.youtube_channel_id == (chan_identifier or author_name).lstrip("@") if chan_identifier else False)
                            ).first()
                            if not matched_ch:
                                matched_ch = Channel(
                                    name=author_name,
                                    youtube_channel_id=chan_identifier or author_name,
                                    watch_enabled=True,
                                    human_review_required=True,
                                )
                                db.add(matched_ch)
                                db.commit()
                                db.refresh(matched_ch)
                                logger.info("Auto-created channel '%s' from oEmbed for video %s", author_name, video_id)
                            channel_id = matched_ch.id
            except Exception as e:
                logger.debug("oEmbed channel resolution failed for video %s: %s", video_id, e)

    is_dry_run = dry_run is not None and dry_run.lower() in ("on", "true", "1", "yes")
    is_force = force is not None and force.lower() in ("on", "true", "1", "yes")

    # Guard against concurrent duplicate processing
    v_lock = get_video_lock(video_id)
    if v_lock.locked() and not is_force:
        msg = f"Video {video_id} is already actively being processed"
        logger.warning("Operator '%s' attempted duplicate trigger for actively running video %s", current_user.username, video_id)
        if request.headers.get("hx-request") == "true":
            from fastapi.responses import Response
            return Response(content=f'<div class="alert alert-warning py-2 mb-0">{msg}</div>', headers={"HX-Trigger": json.dumps({"showToast": {"message": msg, "type": "warning"}})})
        if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
            return RedirectResponse(
                url=f"/?msg=Video+{video_id}+is+already+actively+being+processed&type=warning",
                status_code=303,
            )
        return {"status": "already_running", "video_id": video_id, "message": msg}

    existing_v = db.query(SourceVideo).filter_by(youtube_video_id=video_id).first()
    if existing_v and existing_v.status in (
        SourceVideoStatus.DOWNLOADING,
        SourceVideoStatus.TRANSCRIBING,
        SourceVideoStatus.ANALYZING,
    ) and not is_force:
        stage_name = existing_v.status.value
        msg = f"Video {video_id} is already in progress ({stage_name})"
        logger.warning("Operator '%s' attempted duplicate trigger for in-progress video %s (%s)", current_user.username, video_id, stage_name)
        if request.headers.get("hx-request") == "true":
            from fastapi.responses import Response
            return Response(content=f'<div class="alert alert-warning py-2 mb-0">{msg}</div>', headers={"HX-Trigger": json.dumps({"showToast": {"message": msg, "type": "warning"}})})
        if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
            return RedirectResponse(
                url=f"/?msg=Video+{video_id}+is+already+in+stage+{stage_name}&type=warning",
                status_code=303,
            )
        return {"status": "already_running", "video_id": video_id, "message": msg}

    # Guard against re-ingesting already completed videos unless forced
    if existing_v and existing_v.status == SourceVideoStatus.DONE and not is_force:
        msg = f"Video {video_id} has already been completed and processed. Check 'Force Re-run' if you want to re-process it."
        logger.info("Operator '%s' attempted trigger for already completed video %s without force flag", current_user.username, video_id)
        if request.headers.get("hx-request") == "true":
            from fastapi.responses import Response
            return Response(content=f'<div class="alert alert-info py-2 mb-0">{msg}</div>', headers={"HX-Trigger": json.dumps({"showToast": {"message": msg, "type": "info"}})})
        if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
            return RedirectResponse(
                url=f"/?msg=Video+{video_id}+has+already+completed.+Check+Force+Re-run+to+reprocess.&type=warning",
                status_code=303,
            )
        return {"status": "already_completed", "video_id": video_id, "message": msg}

    if is_force and existing_v:
        release_video_lock(video_id)
        clear_cancellation(video_id)
        existing_v.status = SourceVideoStatus.DETECTED
        existing_v.error_message = None
        if channel_id:
            existing_v.channel_id = channel_id
        for cand in list(existing_v.candidates):
            if cand.clip:
                db.delete(cand.clip)
            db.delete(cand)
        db.commit()
        logger.info("Force re-run: reset video %s to DETECTED and purged prior candidates", video_id)

    # Assemble optional runtime tuning overrides (ignoring unset/empty values or Form parameter defaults)
    def _opt_str(val: Any) -> Optional[str]:
        return val.strip() if isinstance(val, str) and val.strip() else None

    def _opt_int(val: Any) -> Optional[int]:
        if isinstance(val, int) and val > 0:
            return val
        if isinstance(val, str) and val.strip().isdigit() and int(val.strip()) > 0:
            return int(val.strip())
        return None

    def _opt_float(val: Any) -> Optional[float]:
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str) and val.strip():
            try:
                return float(val.strip())
            except ValueError:
                return None
        return None

    runtime_overrides = {}
    if _opt_str(override_llm_provider):
        runtime_overrides["llm_provider"] = _opt_str(override_llm_provider).lower()
    if _opt_str(override_llm_model):
        runtime_overrides["llm_model_name"] = _opt_str(override_llm_model)
    if _opt_float(override_llm_temperature) is not None:
        runtime_overrides["llm_temperature"] = _opt_float(override_llm_temperature)
    if _opt_int(override_min_duration):
        runtime_overrides["target_duration_min_sec"] = _opt_int(override_min_duration)
    if _opt_int(override_max_duration):
        runtime_overrides["target_duration_max_sec"] = _opt_int(override_max_duration)
    if _opt_int(override_min_segments):
        runtime_overrides["min_segments_per_video"] = _opt_int(override_min_segments)
    if _opt_int(override_max_segments):
        runtime_overrides["max_segments_per_video"] = _opt_int(override_max_segments)
    if _opt_int(override_crf):
        runtime_overrides["video_encoding_crf"] = _opt_int(override_crf)
    if _opt_str(override_preset):
        runtime_overrides["video_encoding_preset"] = _opt_str(override_preset).lower()
    if _opt_str(override_scale_algo):
        runtime_overrides["video_scale_algorithm"] = _opt_str(override_scale_algo).lower()
    if _opt_int(override_target_fps):
        runtime_overrides["video_target_fps"] = _opt_int(override_target_fps)
    if _opt_str(override_audio_bitrate):
        runtime_overrides["audio_bitrate"] = _opt_str(override_audio_bitrate).lower()
    if _opt_str(override_enable_sfx):
        runtime_overrides["enable_sfx"] = _opt_str(override_enable_sfx).lower() in ("true", "1", "yes", "on")
    if _opt_str(override_whisper_model):
        runtime_overrides["whisper_model_size"] = _opt_str(override_whisper_model).lower()
    if _opt_str(override_whisper_language):
        runtime_overrides["whisper_language"] = _opt_str(override_whisper_language)

    logger.info(
        "Operator '%s' triggered manual video processing: '%s' (channel_id=%s, dry_run=%s, force=%s, caption_preset=%s, overrides=%s)",
        current_user.username,
        video_id,
        channel_id,
        is_dry_run,
        is_force,
        caption_preset,
        runtime_overrides,
    )

    # Spawn processing thread
    thread = threading.Thread(
        target=_run_pipeline_background,
        args=(video_id, channel_id, is_dry_run, caption_preset, runtime_overrides),
        daemon=True,
    )
    thread.start()

    success_msg = f"Started processing video {video_id}"
    if request.headers.get("hx-request") == "true":
        from fastapi.responses import Response
        return Response(
            content=f'<div class="alert alert-success py-2 mb-0">🚀 {success_msg}. Live telemetry will stream below.</div>',
            headers={"HX-Trigger": json.dumps({"showToast": {"message": success_msg, "type": "success"}, "clearInput": True})}
        )

    if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return RedirectResponse(
            url=f"/?msg=Started+processing+video+{video_id}&type=success",
            status_code=303,
        )

    return {"status": "started", "video_id": video_id, "dry_run": is_dry_run}


@router.post("/process-video/{video_id}/cancel", summary="Cancel active pipeline job for a video")
def cancel_video_processing(
    request: Request,
    video_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    """Cancels an in-progress video ingestion job, marks status as FAILED, and releases the process lock."""
    video = db.query(SourceVideo).filter(
        (SourceVideo.id == video_id) | (SourceVideo.youtube_video_id == video_id)
    ).first()
    if not video:
        raise HTTPException(status_code=404, detail="Source video not found")

    if video.status not in (
        SourceVideoStatus.DETECTED,
        SourceVideoStatus.DOWNLOADING,
        SourceVideoStatus.TRANSCRIBING,
        SourceVideoStatus.ANALYZING,
        SourceVideoStatus.SEGMENTS_SELECTED,
    ):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel video in status '{video.status.value}'. Only active in-progress jobs can be cancelled.",
        )

    prev_status = video.status.value
    video.status = SourceVideoStatus.FAILED
    video.error_message = f"Cancelled by operator {current_user.username}"
    release_video_lock(video.youtube_video_id)
    db.commit()

    logger.warning("Operator '%s' cancelled processing for video %s (%s, was in %s)", current_user.username, video.id, video.youtube_video_id, prev_status)

    if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return RedirectResponse(
            url=f"/?msg=Cancelled+processing+for+video+{video.youtube_video_id}&type=warning",
            status_code=303,
        )
    return {
        "status": "cancelled",
        "video_id": video.youtube_video_id,
        "previous_status": prev_status,
        "new_status": SourceVideoStatus.FAILED.value,
    }


@router.post("/process-video/{video_id}/retry", summary="1-Click retry ingestion for a failed video")
def retry_video_processing(
    request: Request,
    video_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    """Resets a failed video and immediately restarts background processing in 1 click."""
    video = db.query(SourceVideo).filter(
        (SourceVideo.id == video_id) | (SourceVideo.youtube_video_id == video_id)
    ).first()
    if not video:
        raise HTTPException(status_code=404, detail="Source video not found")

    if video.status != SourceVideoStatus.FAILED:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot retry video in status '{video.status.value}'. Only FAILED jobs can be retried.",
        )

    v_lock = get_video_lock(video.youtube_video_id)
    if v_lock.locked():
        raise HTTPException(
            status_code=409,
            detail=f"Video {video.youtube_video_id} is currently locked by another active process.",
        )

    # Reset failure markers and increment retry counter
    clear_cancellation(video.youtube_video_id)
    video.status = SourceVideoStatus.DETECTED
    video.error_message = None
    video.retry_count += 1
    db.commit()

    logger.info(
        "Operator '%s' initiated 1-click retry for failed video %s (Attempt #%d)",
        current_user.username,
        video.youtube_video_id,
        video.retry_count,
    )

    # Spawn processing thread
    thread = threading.Thread(
        target=_run_pipeline_background,
        args=(video.youtube_video_id, video.channel_id, False),
        daemon=True,
    )
    thread.start()

    msg = f"Retrying ingestion for {video.youtube_video_id} (Attempt #{video.retry_count})"
    if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return RedirectResponse(
            url=f"/?msg={urllib.parse.quote_plus(msg)}&type=success",
            status_code=303,
        )
    return {
        "status": "retrying",
        "video_id": video.youtube_video_id,
        "retry_count": video.retry_count,
        "message": msg,
    }


@router.post("/process-video/{video_id}/resume", summary="Resume interrupted/stalled video processing")
def resume_video_processing(
    request: Request,
    video_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    """
    Resumes processing an interrupted or stalled video from its last completed checkpoint.
    Preserves completed downloads, transcripts, candidates, and rendered clips.
    """
    video = db.query(SourceVideo).filter(
        (SourceVideo.id == video_id) | (SourceVideo.youtube_video_id == video_id)
    ).first()
    if not video:
        raise HTTPException(status_code=404, detail=f"Video {video_id} not found")

    reconcile_stale_claims(db)
    release_video_lock(video.youtube_video_id)
    clear_cancellation(video.youtube_video_id)

    thread = threading.Thread(
        target=_run_pipeline_background,
        args=(video.youtube_video_id, video.channel_id, False),
        daemon=True,
    )
    thread.start()

    logger.info("Operator '%s' resumed pipeline for video %s (%s)", current_user.username, video.id, video.youtube_video_id)
    msg = f"Resumed processing for {video.youtube_video_id}"
    if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return RedirectResponse(
            url=f"/?msg={urllib.parse.quote_plus(msg)}&type=success",
            status_code=303,
        )
    return {
        "status": "resumed",
        "video_id": video.youtube_video_id,
        "message": msg,
    }


@router.post("/process-video/{video_id}/clean-retry", summary="Clean re-ingest: purge fragments, reset, and restart")
def clean_retry_video_processing(
    request: Request,
    video_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    """
    Purges any stale or partially downloaded fragments (.part, .ytdl, etc.),
    unlinks existing audio/video files, resets state to DETECTED, and triggers runner.
    """
    video = db.query(SourceVideo).filter(
        (SourceVideo.id == video_id) | (SourceVideo.youtube_video_id == video_id)
    ).first()
    if not video:
        raise HTTPException(status_code=404, detail="Source video not found")

    yt_id = video.youtube_video_id
    v_lock = get_video_lock(yt_id)
    if v_lock.locked():
        raise HTTPException(
            status_code=409,
            detail=f"Video {yt_id} is currently locked by another active process.",
        )

    # 1. Sweep disk for stale files and fragments
    raw_dir = Path(settings.RAW_DIR)
    temp_dir = Path(settings.TEMP_DIR)
    for folder in (raw_dir, temp_dir):
        if folder.exists():
            for f in folder.glob(f"*{yt_id}*"):
                try:
                    if f.is_file():
                        f.unlink(missing_ok=True)
                        logger.info("Clean-retry purged file %s", f)
                    elif f.is_dir():
                        shutil.rmtree(f, ignore_errors=True)
                except Exception as e:
                    logger.warning("Clean-retry failed to unlink %s: %s", f, e)

    # 2. Reset database state
    clear_cancellation(yt_id)
    video.status = SourceVideoStatus.DETECTED
    video.error_message = None
    video.downloaded_path = None
    video.audio_path = None
    video.retry_count += 1
    db.commit()

    logger.info(
        "Operator '%s' initiated clean re-ingest for video %s (Attempt #%d)",
        current_user.username,
        yt_id,
        video.retry_count,
    )

    # 3. Spawn background runner
    thread = threading.Thread(
        target=_run_pipeline_background,
        args=(yt_id, video.channel_id, False),
        daemon=True,
    )
    thread.start()

    msg = f"Clean re-ingest started for {yt_id} (Purged disk fragments, Attempt #{video.retry_count})"
    if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return RedirectResponse(
            url=f"/?msg={urllib.parse.quote_plus(msg)}&type=success",
            status_code=303,
        )
    return {
        "status": "clean_retrying",
        "video_id": yt_id,
        "retry_count": video.retry_count,
        "message": msg,
    }


@router.post("/process-video/{video_id}/delete", summary="Dismiss or delete a failed/completed source video")
def delete_source_video(
    request: Request,
    video_id: str,
    delete_files: Optional[str] = Form(default="false"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    """
    Dismisses and deletes a source video record, cascading to candidates and clips.
    Supports DeletePolicy: keeps media files on disk if delete_files is False, or purges them if True.
    """
    video = db.query(SourceVideo).filter(
        (SourceVideo.id == video_id) | (SourceVideo.youtube_video_id == video_id)
    ).first()
    if not video:
        raise HTTPException(status_code=404, detail="Source video not found")

    yt_id = video.youtube_video_id
    release_video_lock(yt_id)
    clear_cancellation(yt_id)

    from shorts_engine.services.storage.path_manager import PathManager
    should_wipe_files = delete_files is not None and delete_files.lower() in ("true", "1", "yes", "on")

    if should_wipe_files:
        # Clean up source files on disk
        for p in [video.downloaded_path, video.audio_path]:
            if p:
                res_p = PathManager.resolve_download_path(p)
                if res_p and res_p.exists():
                    PathManager.safe_unlink(res_p)
                    logger.info("Unlinked source file %s for deleted video %s", res_p, yt_id)

        # Unlink rendered clip files and thumbnails
        for cand in video.candidates:
            if cand.clip and cand.clip.rendered_path:
                c_path = PathManager.resolve_clip_path(cand.clip)
                if c_path and c_path.exists():
                    PathManager.safe_unlink(c_path)
                    thumb = c_path.with_name(f"{c_path.stem}_thumb.jpg")
                    PathManager.safe_unlink(thumb)
                q_thumb = settings.STORAGE_DIR / "thumbnails" / f"{cand.clip.id}.jpg"
                PathManager.safe_unlink(q_thumb)
        logger.info("Operator '%s' deleted source video %s (%s) and purged media files from disk", current_user.username, video.id, yt_id)
    else:
        logger.info("Operator '%s' deleted source video %s (%s) record (preserved media files on disk)", current_user.username, video.id, yt_id)

    db.delete(video)
    db.commit()

    msg = f"Dismissed and deleted video {yt_id} ({'files removed' if should_wipe_files else 'files preserved'})."
    if request.headers.get("hx-request") == "true":
        from fastapi.responses import Response
        return Response(
            content="",
            headers={"HX-Trigger": json.dumps({"showToast": {"message": msg, "type": "info"}, "videoDeleted": yt_id})}
        )
    if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return RedirectResponse(
            url=f"/?msg={urllib.parse.quote_plus(msg)}&type=info",
            status_code=303,
        )
    return {"status": "deleted", "video_id": yt_id, "files_deleted": should_wipe_files}


def generate_fallback_svg(title_or_id: str, is_vertical: bool = False) -> str:
    """Generates an elegant dark SVG placeholder so broken gray boxes never appear."""
    clean_id = (title_or_id or "Video").strip()
    if len(clean_id) > 28:
        clean_id = clean_id[:25] + "..."
    width = 180 if is_vertical else 320
    height = 320 if is_vertical else 180
    cx = width // 2
    cy = (height // 2) - 15
    poly_y1 = cy - 14
    poly_y2 = cy
    poly_y3 = cy + 14
    text_y1 = cy + 45
    text_y2 = cy + 62

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" fill="none">
  <defs>
    <linearGradient id="thumb_grad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#14141e"/>
      <stop offset="100%" stop-color="#241e38"/>
    </linearGradient>
  </defs>
  <rect width="{width}" height="{height}" rx="6" fill="url(#thumb_grad)"/>
  <circle cx="{cx}" cy="{cy}" r="26" fill="#7c4dff" fill-opacity="0.25"/>
  <polygon points="{cx-8},{poly_y1} {cx+12},{poly_y2} {cx-8},{poly_y3}" fill="#a78bfa"/>
  <text x="{cx}" y="{text_y1}" fill="#e2e8f0" font-family="system-ui, -apple-system, sans-serif" font-size="11" font-weight="600" text-anchor="middle">{clean_id}</text>
  <text x="{cx}" y="{text_y2}" fill="#6b7280" font-family="system-ui, -apple-system, sans-serif" font-size="9" text-anchor="middle">SHORTS AUTOMATION</text>
</svg>"""


@router.get("/api/thumbnails/{video_or_clip_id}", summary="Robust thumbnail resolver with local cache and branded SVG fallback")
def get_thumbnail(
    video_or_clip_id: str,
    db: Session = Depends(get_db),
):
    """
    Returns high-speed cached thumbnail image (JPEG/PNG/SVG).
    Checks:
    1. storage/thumbnails/{id}.jpg (or .png, .webp)
    2. Hierarchical storage paths (storage/outputs/... or storage/downloads/...)
    3. SourceVideo / Clip record in DB -> dynamic FFmpeg frame extraction if video file exists
    4. YouTube CDN proxy/redirect (if 11-char YouTube ID)
    5. Branded, zero-cost SVG fallback (never broken gray box with '...')
    """
    clean_id = video_or_clip_id.strip()
    storage_thumb_dir = Path(settings.STORAGE_DIR) / "thumbnails"
    storage_thumb_dir.mkdir(parents=True, exist_ok=True)

    # 1. Direct match in storage/thumbnails/
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        thumb_file = storage_thumb_dir / f"{clean_id}{ext}"
        if thumb_file.exists() and thumb_file.stat().st_size > 0:
            media_type = "image/png" if ext == ".png" else "image/jpeg"
            return FileResponse(thumb_file, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})

    # 2. Check if it is a Clip
    clip = db.query(Clip).filter_by(id=clean_id).first()
    if clip:
        from shorts_engine.services.storage.path_manager import PathManager
        clip_path = PathManager.resolve_clip_path(clip)
        if clip_path and clip_path.exists():
            # Check for adjacent thumbnail file
            adj_thumb = clip_path.with_name(f"{clip_path.stem}_thumb.jpg")
            if adj_thumb.exists() and adj_thumb.stat().st_size > 0:
                try:
                    shutil.copy2(str(adj_thumb), str(storage_thumb_dir / f"{clean_id}.jpg"))
                except Exception:
                    pass
                return FileResponse(adj_thumb, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})

            # Extract frame via ffmpeg
            out_thumb = storage_thumb_dir / f"{clean_id}.jpg"
            try:
                res = SubprocessWatchdog.run(
                    ["ffmpeg", "-y", "-ss", "00:00:00.500", "-i", str(clip_path), "-frames:v", "1", "-q:v", "2", str(out_thumb)],
                    timeout_sec=5.0,
                    check=False,
                )
                if res.exit_code == 0 and out_thumb.exists() and out_thumb.stat().st_size > 0:
                    return FileResponse(out_thumb, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
            except Exception as e:
                logger.warning("Could not extract frame for clip %s: %s", clean_id, e)
        return Response(content=generate_fallback_svg(clean_id[:8], is_vertical=True), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})

    # 3. Check if it is a SourceVideo (by id or youtube_video_id)
    video = db.query(SourceVideo).filter(
        (SourceVideo.id == clean_id) | (SourceVideo.youtube_video_id == clean_id)
    ).first()
    if video:
        yt_id = video.youtube_video_id
        # Check if downloaded_path exists
        if video.downloaded_path and os.path.exists(video.downloaded_path):
            out_thumb = storage_thumb_dir / f"{yt_id}.jpg"
            try:
                res = SubprocessWatchdog.run(
                    ["ffmpeg", "-y", "-ss", "00:00:02.000", "-i", str(video.downloaded_path), "-frames:v", "1", "-q:v", "2", str(out_thumb)],
                    timeout_sec=5.0,
                    check=False,
                )
                if res.exit_code == 0 and out_thumb.exists() and out_thumb.stat().st_size > 0:
                    return FileResponse(out_thumb, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
            except Exception as e:
                logger.warning("Could not extract frame for video %s: %s", yt_id, e)

        # If valid 11-char YouTube ID, redirect to YouTube thumbnail
        if re.match(r"^[0-9A-Za-z_-]{11}$", yt_id):
            return RedirectResponse(
                url=f"https://img.youtube.com/vi/{yt_id}/mqdefault.jpg",
                status_code=307,
                headers={"Cache-Control": "public, max-age=86400"}
            )
        return Response(content=generate_fallback_svg(video.title or yt_id, is_vertical=False), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})

    # 4. If 11-character YouTube video ID format
    if re.match(r"^[0-9A-Za-z_-]{11}$", clean_id):
        return RedirectResponse(
            url=f"https://img.youtube.com/vi/{clean_id}/mqdefault.jpg",
            status_code=307,
            headers={"Cache-Control": "public, max-age=86400"}
        )

    # 5. Default SVG fallback
    return Response(content=generate_fallback_svg(clean_id, is_vertical=False), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/events/dashboard", summary="Real-time Server-Sent Events stream for pipeline status")
async def dashboard_events(
    request: Request,
    max_ticks: Optional[int] = None,
    current_user: User = Depends(require_viewer),
):
    """
    Streams live status snapshots every 2 seconds over SSE.
    Uses short-lived scoped database sessions (with SessionLocal() as db:) to prevent pool starvation.
    """
    async def event_generator():
        ticks = 0

        while True:
            if await request.is_disconnected():
                break

            ticks += 1

            # Scoped short-lived DB session: released back to pool immediately after reading
            with SessionLocal() as db:
                tracker = QuotaTracker()
                yt_quota = tracker.get_usage_metrics(db=db)
                api_summary = usage_tracker.get_summary(
                    youtube_used=yt_quota["used_units"],
                    youtube_limit=settings.DAILY_QUOTA_LIMIT,
                )
                api_key_metrics = usage_tracker.get_per_key_metrics()

                recent_videos = build_recent_videos_payload(db, limit=25)

                total_videos = db.query(SourceVideo).count()
                in_progress = db.query(SourceVideo).filter(
                    SourceVideo.status.in_([
                        SourceVideoStatus.DOWNLOADING,
                        SourceVideoStatus.TRANSCRIBING,
                        SourceVideoStatus.ANALYZING,
                        SourceVideoStatus.SEGMENTS_SELECTED,
                    ])
                ).count()
                waiting_approval = db.query(Clip).filter_by(status=ClipStatus.WAITING_APPROVAL).count()
                published_today = db.query(Clip).filter_by(status=ClipStatus.PUBLISHED).count()
                failed_clips_count = db.query(Clip).filter_by(status=ClipStatus.FAILED).count()
                failed_videos_count = db.query(SourceVideo).filter_by(status=SourceVideoStatus.FAILED).count()

            payload = {
                "counters": {
                    "total_videos": total_videos,
                    "in_progress": in_progress,
                    "waiting_approval": waiting_approval,
                    "published_today": published_today,
                    "failed_total": failed_clips_count + failed_videos_count,
                    "failed_clips": failed_clips_count,
                    "failed_videos": failed_videos_count,
                    "quota_used": api_summary["used_units"],
                    "quota_limit": api_summary["limit_units"],
                    "quota_pct": api_summary["pct"],
                    "quota_warning": api_summary["warning"],
                    "quota_subtext": api_summary["subtext"],
                },
                "api_key_metrics": api_key_metrics,
                "storage_stats": get_storage_stats(),
                "ai_provider": get_ai_provider_status(),
                "recent_videos": recent_videos,
                "live_model_activity": usage_tracker.get_live_telemetry(),
            }

            yield f"data: {json.dumps(payload)}\n\n"
            if max_ticks and ticks >= max_ticks:
                break
            await asyncio.sleep(2.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/video-status/{video_id:path}", summary="Pre-flight check if video is already processed or in progress")
def check_video_status(
    video_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    """Instant pre-flight check returning full provenance, clips yield, and YouTube validation."""
    raw = video_id.strip()
    m = re.search(r"(?:v=|\/|youtu\.be\/|shorts\/)([0-9A-Za-z_-]{11})", raw)
    clean_id = m.group(1) if m else raw

    v = db.query(SourceVideo).filter(
        (SourceVideo.id == clean_id) | (SourceVideo.youtube_video_id == clean_id) | (SourceVideo.youtube_video_id == raw)
    ).first()

    # Strict syntax check for YouTube 11-character ID if not already in DB
    if not v and not re.match(r"^[0-9A-Za-z_-]{11}$", clean_id):
        return {
            "is_valid": False,
            "exists": False,
            "video_id": clean_id,
            "error": "Invalid YouTube URL or ID format. Expected an 11-character video ID.",
        }

    if not v:
        # Pre-flight check via YouTube public oEmbed endpoint
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={clean_id}&format=json"
        try:
            with httpx.Client(timeout=2.5) as client:
                res = client.get(oembed_url)
                if res.status_code == 200:
                    data = res.json()
                    author_name = data.get("author_name") or ""
                    author_url = data.get("author_url") or ""
                    chan_identifier = None
                    if author_url:
                        m_chan = re.search(r"/(?:channel/|c/|@)([0-9A-Za-z_.-]+)", author_url)
                        if m_chan:
                            matched = m_chan.group(1)
                            chan_identifier = matched if matched.startswith("UC") else f"@{matched}"

                    existing_chan = None
                    if chan_identifier or author_name:
                        filters = []
                        if chan_identifier:
                            filters.append(Channel.youtube_channel_id == chan_identifier)
                            filters.append(Channel.youtube_channel_id == chan_identifier.lstrip("@"))
                        if author_name:
                            filters.append(Channel.name.ilike(author_name.strip()))
                        existing_chan = db.query(Channel).filter(or_(*filters)).first()

                    chan_payload = {
                        "detected_name": author_name,
                        "detected_id": chan_identifier or author_name,
                        "exists": bool(existing_chan),
                        "channel_id": existing_chan.id if existing_chan else None,
                        "channel_name": existing_chan.name if existing_chan else author_name,
                    }

                    return {
                        "is_valid": True,
                        "exists": False,
                        "video_id": clean_id,
                        "title": data.get("title"),
                        "author": author_name,
                        "channel": chan_payload,
                        "thumbnail_url": f"https://img.youtube.com/vi/{clean_id}/mqdefault.jpg",
                        "status": "NEW",
                        "message": f"Valid YouTube video: \"{data.get('title')}\"",
                    }
                elif res.status_code in (400, 404):
                    return {
                        "is_valid": False,
                        "exists": False,
                        "video_id": clean_id,
                        "status": "NOT_FOUND",
                        "error": "Video not found on YouTube (video may be private, removed, or ID is incorrect).",
                    }
        except Exception as e:
            logger.debug("YouTube oEmbed check skipped/failed: %s", e)

        return {
            "is_valid": True,
            "exists": False,
            "video_id": clean_id,
            "status": "NEW",
            "title": None,
            "channel": {
                "detected_name": None,
                "detected_id": None,
                "exists": False,
                "channel_id": None,
                "channel_name": None,
            },
            "message": "Valid video ID format ready for ingestion",
        }

    cands = db.query(ClipCandidate).filter_by(source_video_id=v.id).all()
    clips_count = len(cands)
    waiting_clips = sum(1 for c in cands if c.clip and c.clip.status == ClipStatus.WAITING_APPROVAL)
    approved_clips = sum(1 for c in cands if c.clip and c.clip.status == ClipStatus.APPROVED)
    published_clips = sum(1 for c in cands if c.clip and c.clip.status == ClipStatus.PUBLISHED)
    failed_clips = sum(1 for c in cands if c.clip and c.clip.status == ClipStatus.FAILED)

    is_done = v.status in (SourceVideoStatus.DONE, "COMPLETED")
    is_in_progress = v.status in (
        SourceVideoStatus.DETECTED,
        SourceVideoStatus.DOWNLOADING,
        SourceVideoStatus.TRANSCRIBING,
        SourceVideoStatus.ANALYZING,
        SourceVideoStatus.SEGMENTS_SELECTED,
    )
    is_failed = v.status == SourceVideoStatus.FAILED

    dur_sec = v.duration_sec or 0
    estimated_tokens = int(dur_sec * 2.5 * 1.33) if dur_sec > 0 else None
    recommended_tier = None
    if dur_sec > 0:
        if estimated_tokens and estimated_tokens >= settings.GROQ_TOKEN_THRESHOLD:
            recommended_tier = "🤖 Tier 2: Gemini 1M Window (Long Video >15m)"
        else:
            recommended_tier = "⚡ Tier 1: Groq Ultra-Fast (<15m)"

    from shorts_engine.services.clip_selection.estimator import estimate_optimal_clip_count
    clip_estimate = estimate_optimal_clip_count(dur_sec) if dur_sec > 0 else None

    return {
        "is_valid": True,
        "exists": True,
        "video_id": clean_id,
        "clip_estimate": clip_estimate,
        "title": v.title or f"YouTube Video ({clean_id})",
        "channel_name": v.channel.name if v.channel else "Default Channel",
        "channel": {
            "detected_name": v.channel.name if v.channel else "Default Channel",
            "detected_id": v.channel.youtube_channel_id if v.channel else None,
            "exists": bool(v.channel),
            "channel_id": v.channel.id if v.channel else None,
            "channel_name": v.channel.name if v.channel else "Default Channel",
        },
        "status": v.status.value,
        "is_done": is_done,
        "is_in_progress": is_in_progress,
        "is_failed": is_failed,
        "clips_count": clips_count,
        "waiting_clips": waiting_clips,
        "approved_clips": approved_clips,
        "published_clips": published_clips,
        "failed_clips": failed_clips,
        "duration_sec": dur_sec,
        "estimated_tokens": estimated_tokens,
        "recommended_tier": recommended_tier,
        "updated_at": v.updated_at.strftime("%Y-%m-%d %H:%M") if v.updated_at else "—",
        "queue_url": f"/queue?tab=active&source_video_id={clean_id}",
        "error_message": v.error_message,
    }


@router.get("/api/status", summary="Status JSON API")
def api_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    """JSON endpoint for programmatic status polling."""
    tracker = QuotaTracker()
    quota_metrics = tracker.get_usage_metrics(db=db)
    api_summary = usage_tracker.get_summary(
        youtube_used=quota_metrics["used_units"],
        youtube_limit=settings.DAILY_QUOTA_LIMIT,
    )
    return {
        "total_videos": db.query(SourceVideo).count(),
        "waiting_approval": db.query(Clip).filter_by(status=ClipStatus.WAITING_APPROVAL).count(),
        "published": db.query(Clip).filter_by(status=ClipStatus.PUBLISHED).count(),
        "failed_clips": db.query(Clip).filter_by(status=ClipStatus.FAILED).count(),
        "quota_remaining": api_summary["limit_units"] - api_summary["used_units"],
        "quota_limit": api_summary["limit_units"],
        "quota_used": api_summary["used_units"],
        "quota_pct": api_summary["pct"],
        "quota_subtext": api_summary["subtext"],
        "api_key_metrics": usage_tracker.get_per_key_metrics(),
    }


@router.get("/api/status/summary", summary="Status summary JSON endpoint for React SPA")
def api_status_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    storage_stats = get_storage_stats()
    per_key = usage_tracker.get_per_key_metrics()
    api_keys = []
    for k in per_key:
        api_keys.append({
            "provider": k.get("provider", "LLM"),
            "model": k.get("model", ""),
            "rpm_used": k.get("rpm_used", 0),
            "rpm_limit": k.get("rpm_limit", 15),
            "tpm_used": k.get("tpm_used", 0),
            "tpm_limit": k.get("tpm_limit", 1000000),
            "latency_ms": k.get("latency_ms", 0),
            "status": "HEALTHY" if k.get("is_active", True) else "DEGRADED",
        })

    recent_vids = (
        db.query(SourceVideo)
        .order_by(SourceVideo.created_at.desc())
        .limit(50)
        .all()
    )
    pipelines = []
    for v in recent_vids:
        pipelines.append({
            "id": v.id,
            "youtube_video_id": v.youtube_video_id,
            "title": v.title,
            "status": v.status.value,
            "created_at": v.created_at.isoformat() if v.created_at else "",
            "updated_at": v.updated_at.isoformat() if v.updated_at else None,
        })

    return {
        "api_keys": api_keys,
        "pipelines": pipelines,
        "disk_used_gb": storage_stats.get("used_gb", 0.0),
        "disk_total_gb": storage_stats.get("total_gb", 100.0),
        "clip_count": db.query(Clip).count(),
        "published_count": db.query(Clip).filter_by(status=ClipStatus.PUBLISHED).count(),
        "pending_count": db.query(Clip).filter_by(status=ClipStatus.WAITING_APPROVAL).count(),
    }


@router.post("/api/pipeline/{video_id}/cancel", summary="Cancel pipeline from React SPA")
def api_cancel_pipeline(
    video_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    from shorts_engine.orchestration.runner import signal_cancellation
    v = db.query(SourceVideo).filter((SourceVideo.id == video_id) | (SourceVideo.youtube_video_id == video_id)).first()
    if v:
        signal_cancellation(v.youtube_video_id)
        signal_cancellation(v.id)
    return {"status": "cancelled", "video_id": video_id}


@router.post("/api/ingest", summary="Ingest video from React SPA")
def api_ingest_video(
    request: Request,
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    url = (payload.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=422, detail="Video URL/path cannot be empty")
    runtime_overrides = {}
    if "override_min_segments" in payload:
        runtime_overrides["min_segments"] = payload["override_min_segments"]
    if "override_max_segments" in payload:
        runtime_overrides["max_segments"] = payload["override_max_segments"]

    t = threading.Thread(
        target=_run_pipeline_background,
        args=(url, payload.get("channel_id"), False, "hormozi_viral", runtime_overrides or None),
        daemon=True,
    )
    t.start()
    return {"status": "started", "video": url}


@router.post("/api/analytics/sync", summary="Synchronize YouTube analytics for all published clips")
def sync_youtube_analytics(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    from shorts_engine.services.monitoring.analytics_sync import YouTubeAnalyticsSync
    from shorts_engine.services.analytics.predictor import viral_predictor

    syncer = YouTubeAnalyticsSync(db)
    res = syncer.sync_all_published_clips()

    # Automatically retrain the ML virality predictor on the freshly ingested metrics
    train_res = viral_predictor.train_from_db(db)
    ml_msg = " & retrained ML Virality Predictor" if train_res.get("trained") else ""

    msg = f"Synced analytics for {res['synced_count']} clips ({res['total_views']:,} views total){ml_msg}!"
    if request.headers.get("hx-request") == "true":
        return Response(
            content="",
            headers={"HX-Trigger": json.dumps({"showToast": {"message": msg, "type": "success"}})}
        )
    res["ml_training"] = train_res
    return res

