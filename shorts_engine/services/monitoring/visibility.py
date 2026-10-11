"""
System Visibility & Telemetry Aggregation Service.

Derives all operational health indicators directly from existing database tables
(pipeline_jobs, event_logs, quota_ledger, channels, channel_credentials, publish_logs, clips)
and system storage headroom without introducing new instrumentation:

1. Queue depth per stage, and age of the oldest pending job.
2. Active running jobs with worker lock and heartbeat metrics.
3. Quarantined jobs with their respective failure reasons (last_error).
4. Last successful publish per channel + channel token health indicators.
5. Quota units remaining today from the quota ledger (Pacific time aware).
6. Disk usage and headroom of the media root storage directory.
7. Jobs succeeded vs failed/quarantined in the last 24 hours.
8. Automated webhook alert evaluations for disk (>80%) and quota (<20%).
"""

import shutil
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

from shorts_engine.config.settings import settings
from shorts_engine.db.models import (
    PipelineJob,
    EventLog,
    Channel,
    ChannelCredential,
    PublishLog,
    Clip,
    ClipCandidate,
    ClipStatus,
    SourceVideo,
)
from shorts_engine.services.publishing.quota import QuotaTracker
from shorts_engine.services.monitoring.alerter import pipeline_alerter

logger = logging.getLogger(__name__)


def format_duration_human(seconds: float) -> str:
    """Formats seconds into human-readable duration string (e.g. '1h 23m 45s')."""
    if seconds <= 0:
        return "0s"
    total_sec = int(seconds)
    days = total_sec // 86400
    hours = (total_sec % 86400) // 3600
    minutes = (total_sec % 3600) // 60
    secs = total_sec % 60

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


# ==============================================================================
# Metric 1: Queue Depth Per Stage & Oldest Pending Job Age
# ==============================================================================


def get_queue_depth_and_oldest_age(db: Session) -> Dict[str, Any]:
    """
    Computes pending queue depth per stage and the elapsed age of the oldest pending job.
    """
    now = datetime.utcnow()
    pending_jobs = (
        db.query(PipelineJob)
        .filter(PipelineJob.status == "PENDING")
        .order_by(PipelineJob.created_at.asc())
        .all()
    )

    stage_depths: Dict[str, Dict[str, Any]] = {}
    for job in pending_jobs:
        st = (job.stage or "unknown").lower()
        if st not in stage_depths:
            stage_depths[st] = {
                "depth": 0,
                "oldest_created_at": job.created_at.isoformat() if job.created_at else None,
                "oldest_age_seconds": round((now - job.created_at).total_seconds(), 1) if job.created_at else 0.0,
                "oldest_age_human": format_duration_human((now - job.created_at).total_seconds()) if job.created_at else "0s",
                "oldest_job_id": job.id,
            }
        stage_depths[st]["depth"] += 1

    overall_oldest = pending_jobs[0] if pending_jobs else None
    overall_oldest_sec = round((now - overall_oldest.created_at).total_seconds(), 1) if overall_oldest and overall_oldest.created_at else 0.0

    return {
        "total_pending": len(pending_jobs),
        "stages": stage_depths,
        "overall_oldest_age_seconds": overall_oldest_sec,
        "overall_oldest_age_human": format_duration_human(overall_oldest_sec),
        "overall_oldest_job_id": overall_oldest.id if overall_oldest else None,
        "overall_oldest_stage": overall_oldest.stage if overall_oldest else None,
        "overall_oldest_created_at": overall_oldest.created_at.isoformat() if overall_oldest and overall_oldest.created_at else None,
    }


# ==============================================================================
# Metric 2: Active Running Jobs
# ==============================================================================


def get_running_jobs(db: Session) -> List[Dict[str, Any]]:
    """
    Lists currently active running jobs with their lock holder, duration, and progress.
    """
    now = datetime.utcnow()
    running = (
        db.query(PipelineJob)
        .filter(PipelineJob.status == "RUNNING")
        .order_by(PipelineJob.locked_at.asc())
        .all()
    )

    items = []
    for job in running:
        locked_time = job.locked_at or job.created_at or now
        runtime_sec = round((now - locked_time).total_seconds(), 1)
        items.append({
            "id": job.id,
            "stage": job.stage,
            "job_type": job.job_type,
            "entity_id": job.entity_id,
            "entity_type": job.entity_type,
            "progress_pct": job.progress_pct or 0.0,
            "progress_detail": job.progress_detail,
            "locked_by": job.locked_by,
            "locked_at": job.locked_at.isoformat() if job.locked_at else None,
            "heartbeat_at": job.heartbeat_at.isoformat() if job.heartbeat_at else None,
            "runtime_seconds": runtime_sec,
            "runtime_human": format_duration_human(runtime_sec),
        })
    return items


# ==============================================================================
# Metric 3: Quarantined Jobs with Failure Reasons
# ==============================================================================


def get_quarantined_jobs(db: Session, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Returns quarantined dead-letter jobs with their last error message and retry count.
    """
    quarantined = (
        db.query(PipelineJob)
        .filter(PipelineJob.status == "QUARANTINED")
        .order_by(PipelineJob.updated_at.desc())
        .limit(limit)
        .all()
    )

    items = []
    for job in quarantined:
        items.append({
            "id": job.id,
            "stage": job.stage,
            "job_type": job.job_type,
            "entity_id": job.entity_id,
            "entity_type": job.entity_type,
            "last_error": job.error_message or "Unknown quarantine error",
            "attempt_count": job.attempt_count or job.retry_count or 0,
            "quarantined_at": (job.completed_at or job.updated_at).isoformat() if (job.completed_at or job.updated_at) else None,
        })
    return items


# ==============================================================================
# Metric 4: Channel Token Health & Last Successful Publish
# ==============================================================================


def get_channels_visibility(db: Session) -> List[Dict[str, Any]]:
    """
    Aggregates per-channel token health status and last successful video publication.
    """
    channels = db.query(Channel).order_by(Channel.name.asc()).all()
    result = []

    for chan in channels:
        cred = db.query(ChannelCredential).filter_by(channel_id=chan.id).first()

        # Token status determination
        if not cred or not cred.encrypted_refresh_token:
            token_status = "NO_CREDENTIALS"
            is_healthy = False
            last_error = "No OAuth credentials configured"
        elif cred.status == "needs_reauth":
            token_status = "NEEDS_REAUTH"
            is_healthy = False
            last_error = cred.last_error or "Token invalidated (needs_reauth)"
        else:
            token_status = "HEALTHY"
            is_healthy = True
            last_error = cred.last_error

        # Query latest publish: check PublishLog first, then Clip
        latest_publish = (
            db.query(PublishLog)
            .join(Clip, PublishLog.clip_id == Clip.id)
            .join(ClipCandidate, Clip.clip_candidate_id == ClipCandidate.id)
            .join(SourceVideo, ClipCandidate.source_video_id == SourceVideo.id)
            .filter(
                PublishLog.status == "SUCCESS",
                SourceVideo.channel_id == chan.id,
            )
            .order_by(PublishLog.published_at.desc())
            .first()
        )

        # Fallback to direct clip query for published clips
        last_pub_info: Optional[Dict[str, Any]] = None
        if latest_publish:
            last_pub_info = {
                "published_at": latest_publish.published_at.isoformat() if latest_publish.published_at else None,
                "title": latest_publish.title,
                "youtube_video_id": latest_publish.youtube_video_id,
                "url": f"https://youtube.com/shorts/{latest_publish.youtube_video_id}" if latest_publish.youtube_video_id else None,
            }
        else:
            # Check EventLog for clip.published events for this channel
            event_row = (
                db.query(EventLog)
                .filter(EventLog.event_name == "clip.published")
                .order_by(EventLog.created_at.desc())
                .first()
            )
            if event_row:
                p_payload = event_row.payload
                if p_payload.get("channel_id") == chan.youtube_channel_id:
                    last_pub_info = {
                        "published_at": event_row.created_at.isoformat() if event_row.created_at else None,
                        "title": p_payload.get("title"),
                        "youtube_video_id": p_payload.get("youtube_video_id"),
                        "url": p_payload.get("youtube_url") or p_payload.get("url"),
                    }

        result.append({
            "channel_id": chan.id,
            "youtube_channel_id": chan.youtube_channel_id,
            "title": chan.title or chan.name,
            "name": chan.name,
            "token_status": token_status,
            "token_healthy": is_healthy,
            "token_last_error": last_error,
            "last_verified_at": (getattr(cred, "verified_at", None) or getattr(cred, "last_verified_at", None)).isoformat() if cred and (getattr(cred, "verified_at", None) or getattr(cred, "last_verified_at", None)) else None,
            "last_publish": last_pub_info,
        })

    return result


# ==============================================================================
# Metric 5: Daily Quota Remaining from Quota Ledger
# ==============================================================================


def get_quota_visibility(db: Session) -> Dict[str, Any]:
    """
    Computes daily quota units remaining today from the quota ledger.
    """
    tracker = QuotaTracker()
    metrics = tracker.get_usage_metrics(db=db)
    spent = metrics.get("used_units", 0)
    limit = metrics.get("daily_limit", settings.DAILY_QUOTA_LIMIT)
    remaining = max(0, limit - spent)
    remaining_pct = round((remaining / limit) * 100, 1) if limit > 0 else 0.0

    return {
        "daily_limit": limit,
        "spent_units": spent,
        "remaining_units": remaining,
        "remaining_pct": remaining_pct,
        "used_pct": round((spent / limit) * 100, 1) if limit > 0 else 0.0,
        "reset_iso": metrics.get("reset_time_iso"),
        "is_low": remaining_pct <= getattr(settings, "QUOTA_REMAINING_ALERT_THRESHOLD_PCT", 20.0),
    }


# ==============================================================================
# Metric 6: Media Root Disk Usage
# ==============================================================================


def get_media_root_disk_usage() -> Dict[str, Any]:
    """
    Measures disk capacity, consumption, and headroom for media root storage.
    """
    storage_path = Path(settings.STORAGE_DIR).resolve()
    storage_path.mkdir(parents=True, exist_ok=True)

    try:
        total, used, free = shutil.disk_usage(storage_path)
    except Exception as e:
        logger.warning("Could not read disk usage for %s: %s", storage_path, e)
        total, used, free = 1, 0, 1

    pct_used = round((used / total) * 100, 1) if total > 0 else 0.0
    threshold = getattr(settings, "DISK_ALERT_THRESHOLD_PCT", 80.0)

    return {
        "storage_path": str(storage_path),
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "total_gb": round(total / (1024 ** 3), 2),
        "used_gb": round(used / (1024 ** 3), 2),
        "free_gb": round(free / (1024 ** 3), 2),
        "percent_used": pct_used,
        "percent_free": round(100.0 - pct_used, 1),
        "is_critical": pct_used >= threshold,
    }


# ==============================================================================
# Metric 7: Jobs Succeeded / Failed in Last 24 Hours
# ==============================================================================


def get_jobs_summary_24h(db: Session) -> Dict[str, Any]:
    """
    Derives job success and failure statistics in the rolling 24-hour window
    from pipeline_jobs and event_logs.
    """
    cutoff = datetime.utcnow() - timedelta(hours=24)

    # 1. Direct PipelineJob status breakdown
    succeeded_jobs = (
        db.query(PipelineJob)
        .filter(
            PipelineJob.status == "DONE",
            (PipelineJob.completed_at >= cutoff) | (PipelineJob.updated_at >= cutoff),
        )
        .count()
    )

    failed_jobs = (
        db.query(PipelineJob)
        .filter(
            PipelineJob.status.in_(["FAILED", "QUARANTINED"]),
            PipelineJob.updated_at >= cutoff,
        )
        .count()
    )

    # 2. Stage Telemetry Event breakdown from event_logs
    stage_finished_events = (
        db.query(EventLog)
        .filter(EventLog.event_name == "stage.finished", EventLog.created_at >= cutoff)
        .count()
    )

    stage_failed_events = (
        db.query(EventLog)
        .filter(EventLog.event_name.in_(["stage.failed", "stage.quarantined"]), EventLog.created_at >= cutoff)
        .count()
    )

    total_jobs = succeeded_jobs + failed_jobs
    success_rate = round((succeeded_jobs / total_jobs) * 100, 1) if total_jobs > 0 else 100.0

    return {
        "since_iso": cutoff.isoformat(),
        "succeeded_jobs": succeeded_jobs,
        "failed_jobs": failed_jobs,
        "total_completed": total_jobs,
        "success_rate_pct": success_rate,
        "stage_events_finished": stage_finished_events,
        "stage_events_failed": stage_failed_events,
    }


# ==============================================================================
# Consolidated Snapshot & Webhook Evaluation
# ==============================================================================


def get_full_visibility_snapshot(db: Session) -> Dict[str, Any]:
    """
    Assembles the complete system visibility overview snapshot.
    """
    queue_data = get_queue_depth_and_oldest_age(db)
    running_data = get_running_jobs(db)
    quarantined_data = get_quarantined_jobs(db)
    channels_data = get_channels_visibility(db)
    quota_data = get_quota_visibility(db)
    disk_data = get_media_root_disk_usage()
    jobs_24h_data = get_jobs_summary_24h(db)

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "queue": queue_data,
        "running_jobs": running_data,
        "running_count": len(running_data),
        "quarantined_jobs": quarantined_data,
        "quarantined_count": len(quarantined_data),
        "channels": channels_data,
        "quota": quota_data,
        "disk": disk_data,
        "jobs_24h": jobs_24h_data,
    }


def evaluate_system_thresholds_and_alert(db: Session, wait: bool = False) -> Dict[str, Any]:
    """
    Evaluates system operational limits and fires webhook alerts:
    - Disk usage >= 80%
    - Quota remaining <= 20%
    - Channels marked needs_reauth
    """
    alerts_fired: List[str] = []

    # 1. Disk Headroom Check
    disk = get_media_root_disk_usage()
    if disk["is_critical"]:
        sent = pipeline_alerter.alert_disk_space(
            percent_used=disk["percent_used"],
            free_gb=disk["free_gb"],
            total_gb=disk["total_gb"],
            path=disk["storage_path"],
            wait=wait,
        )
        if sent:
            alerts_fired.append("disk_space_critical")

    # 2. Quota Availability Check
    quota = get_quota_visibility(db)
    if quota["is_low"]:
        sent = pipeline_alerter.alert_quota_low(
            remaining_units=quota["remaining_units"],
            spent_units=quota["spent_units"],
            limit=quota["daily_limit"],
            wait=wait,
        )
        if sent:
            alerts_fired.append("quota_exhaustion_warning")

    # 3. Channel Credential Needs-Reauth Check
    channels = get_channels_visibility(db)
    for ch in channels:
        if ch["token_status"] == "NEEDS_REAUTH":
            sent = pipeline_alerter.alert_channel_needs_reauth(
                channel_id=ch["channel_id"],
                channel_name=ch["title"],
                error=ch["token_last_error"] or "needs_reauth",
                wait=wait,
            )
            if sent:
                alerts_fired.append(f"channel_needs_reauth:{ch['channel_id']}")

    return {
        "alerts_checked_at": datetime.utcnow().isoformat(),
        "alerts_fired": alerts_fired,
        "count": len(alerts_fired),
    }
