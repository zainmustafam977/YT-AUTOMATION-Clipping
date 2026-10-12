"""
Automated Storage Retention Policy & Disk Capacity Management Engine.
Enforces:
1. Raw source video deletion N days after ALL its clips are published (default 14).
2. Intermediate asset deletion (reframed crop plans, subtitles, temp chunks)
   after final render is published (default 3 days).
3. Final rendered clips kept permanently (never auto-deleted by default).
4. Strict active job protection: Never delete any artifact referenced by a pending or running job.
5. PathManager integration updating DB rows to 'PURGED' status (zero orphaned references).
6. Media root disk usage monitoring and threshold alerts at 80% capacity.
"""

import json
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Set
from sqlalchemy.orm import Session

from shorts_engine.config.settings import settings
from shorts_engine.db.models import (
    SourceVideo,
    SourceVideoStatus,
    ClipCandidate,
    Clip,
    ClipStatus,
    PublishLog,
    PipelineJob,
    PipelineJobStatus,
)
from shorts_engine.services.storage.path_manager import PathManager
from shorts_engine.services.monitoring.alerter import pipeline_alerter
from shorts_engine.services.monitoring.visibility import get_media_root_disk_usage

logger = logging.getLogger(__name__)


class RetentionPolicyService:
    """
    Automated retention policy engine enforcing storage ceilings and pruning aged assets.
    """

    @staticmethod
    def get_active_protected_entity_ids(db: Session) -> Set[str]:
        """
        Discovers all entity IDs (video_id, clip_id, source_video_id) currently
        referenced by any active (PENDING or RUNNING) pipeline jobs.
        Protected entities are completely immune to retention pruning.
        """
        protected: Set[str] = set()

        active_statuses = [
            PipelineJobStatus.PENDING,
            PipelineJobStatus.RUNNING,
            "PENDING",
            "RUNNING",
        ]
        active_jobs = (
            db.query(PipelineJob)
            .filter(PipelineJob.status.in_(active_statuses))
            .all()
        )

        for job in active_jobs:
            if job.entity_id:
                protected.add(str(job.entity_id))

            # Inspect job payload JSON for nested entity identifiers
            payload_raw = getattr(job, "payload_json", None)
            if payload_raw:
                try:
                    payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
                    if isinstance(payload, dict):
                        for key in ("video_id", "source_video_id", "clip_id", "entity_id"):
                            val = payload.get(key)
                            if val:
                                protected.add(str(val))
                except Exception:
                    pass

        return protected

    @classmethod
    def purge_eligible_raw_sources(
        cls,
        db: Session,
        dry_run: bool = False,
        source_retention_days: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Scans for source videos whose clips have ALL been published for at least
        source_retention_days (default 14 days), unlinks raw files via PathManager,
        and sets DB status to 'PURGED'.
        """
        days = source_retention_days if source_retention_days is not None else getattr(settings, "RETENTION_RAW_SOURCE_DAYS", 14)
        cutoff = datetime.utcnow() - timedelta(days=days)
        protected_ids = cls.get_active_protected_entity_ids(db)

        purged_videos: List[Dict[str, Any]] = []

        # Find non-purged source videos
        videos = (
            db.query(SourceVideo)
            .filter(
                SourceVideo.status != SourceVideoStatus.PURGED,
                SourceVideo.status != "PURGED",
            )
            .all()
        )

        for video in videos:
            # 1. Job protection check
            if video.id in protected_ids or video.youtube_video_id in protected_ids:
                logger.debug("SourceVideo %s is protected by an active job; skipping.", video.id)
                continue

            # 2. Check all clips for this source video
            clips = (
                db.query(Clip)
                .join(ClipCandidate)
                .filter(ClipCandidate.source_video_id == video.id)
                .all()
            )

            # If video has no clips and hasn't finished ingestion/analysis, skip
            if not clips:
                if video.status not in (SourceVideoStatus.DONE, "DONE", SourceVideoStatus.FAILED, "FAILED"):
                    continue
                # If finished/failed long ago with zero clips:
                last_time = video.updated_at or video.created_at
                if not last_time or last_time > cutoff:
                    continue
                latest_publish_time = last_time
            else:
                # ALL clips must be published
                all_published = all(
                    c.status in (ClipStatus.PUBLISHED, "PUBLISHED")
                    for c in clips
                )
                if not all_published:
                    continue

                # Are any associated clip IDs protected by active jobs?
                if any(c.id in protected_ids for c in clips):
                    logger.debug("SourceVideo %s has a clip in active job; skipping.", video.id)
                    continue

                # The latest clip's publish time must be >= N days ago
                latest_publish_time = None
                for c in clips:
                    p_time = None
                    if c.publish_log and c.publish_log.published_at:
                        p_time = c.publish_log.published_at
                    elif c.updated_at:
                        p_time = c.updated_at
                    if p_time and (latest_publish_time is None or p_time > latest_publish_time):
                        latest_publish_time = p_time

                if latest_publish_time is None or latest_publish_time > cutoff:
                    # Still within retention window
                    continue

            # 3. Eligible for raw source purge
            report = {
                "source_video_id": video.id,
                "youtube_video_id": video.youtube_video_id,
                "title": video.title,
                "all_clips_published_at": latest_publish_time.isoformat() if latest_publish_time else None,
                "retention_days": days,
                "dry_run": dry_run,
            }

            if not dry_run:
                # Execute deletion through PathManager
                purge_res = PathManager.purge_source_video_files(video)
                report["freed_bytes"] = purge_res["freed_bytes"]
                report["deleted_files"] = purge_res["deleted_files"]

                # Update database row to 'PURGED' status (no orphaned paths)
                video.status = SourceVideoStatus.PURGED
                video.downloaded_path = None
                video.audio_path = None
                db.commit()
                logger.info(
                    "Purged raw source files for video %s (%s). Freed %d bytes.",
                    video.id, video.youtube_video_id, purge_res["freed_bytes"],
                )
            else:
                report["freed_bytes"] = 0
                report["deleted_files"] = []

            purged_videos.append(report)

        return purged_videos

    @classmethod
    def purge_eligible_intermediates(
        cls,
        db: Session,
        dry_run: bool = False,
        intermediate_retention_days: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Scans for published clips whose intermediate assets (crop plan JSON,
        subtitle .ass, temp chunks) have exceeded intermediate_retention_days (default 3 days),
        and safely unlinks them while preserving the final rendered clip.
        """
        days = (
            intermediate_retention_days
            if intermediate_retention_days is not None
            else getattr(settings, "RETENTION_INTERMEDIATES_DAYS", 3)
        )
        cutoff = datetime.utcnow() - timedelta(days=days)
        protected_ids = cls.get_active_protected_entity_ids(db)

        purged_intermediates: List[Dict[str, Any]] = []

        published_clips = (
            db.query(Clip)
            .filter(Clip.status.in_([ClipStatus.PUBLISHED, "PUBLISHED"]))
            .all()
        )

        for clip in published_clips:
            if clip.id in protected_ids:
                continue

            # Check publish timestamp
            pub_time = None
            if clip.publish_log and clip.publish_log.published_at:
                pub_time = clip.publish_log.published_at
            elif clip.updated_at:
                pub_time = clip.updated_at

            if pub_time is None or pub_time > cutoff:
                continue

            video_id = None
            if clip.candidate and clip.candidate.source_video:
                video_id = clip.candidate.source_video.youtube_video_id

            report = {
                "clip_id": clip.id,
                "video_id": video_id,
                "published_at": pub_time.isoformat() if pub_time else None,
                "retention_days": days,
                "dry_run": dry_run,
            }

            if not dry_run:
                purge_res = PathManager.purge_clip_intermediates(clip, video_id=video_id)
                report["freed_bytes"] = purge_res["freed_bytes"]
                report["deleted_files"] = purge_res["deleted_files"]
                if purge_res["freed_bytes"] > 0 or purge_res["deleted_files"]:
                    logger.info(
                        "Purged intermediate assets for clip %s. Freed %d bytes.",
                        clip.id, purge_res["freed_bytes"],
                    )
            else:
                report["freed_bytes"] = 0
                report["deleted_files"] = []

            purged_intermediates.append(report)

        return purged_intermediates

    @classmethod
    def check_disk_and_alert(cls, db: Optional[Session] = None) -> Dict[str, Any]:
        """
        Measures disk capacity of the media root storage directory and triggers
        an automated alert via pipeline_alerter if usage meets or exceeds the 80% threshold.
        """
        disk_metric = get_media_root_disk_usage()
        threshold = getattr(settings, "DISK_ALERT_THRESHOLD_PCT", 80.0)

        if disk_metric.get("percent_used", 0.0) >= threshold:
            logger.warning(
                "Disk usage alert: %.1f%% used (threshold %.1f%%). Triggering pipeline alert.",
                disk_metric["percent_used"], threshold,
            )
            pipeline_alerter.alert_disk_space(
                percent_used=disk_metric["percent_used"],
                free_gb=disk_metric["free_gb"],
                total_gb=disk_metric["total_gb"],
                path=disk_metric["storage_path"],
                wait=True,
            )

        return disk_metric

    @classmethod
    def run_cleanup(cls, db: Session, dry_run: bool = False) -> Dict[str, Any]:
        """
        Orchestrates full daily retention maintenance:
        1. Purges eligible aged raw sources (>14 days post all clips published).
        2. Purges eligible aged intermediates (>3 days post clip published).
        3. Measures disk capacity and emits threshold alert at >=80%.
        4. Emits platform event for observability.
        """
        logger.info("Executing daily retention policy cleanup (dry_run=%s)...", dry_run)

        # 1. Purge raw source videos
        raw_reports = cls.purge_eligible_raw_sources(db, dry_run=dry_run)

        # 2. Purge intermediate assets
        intermediate_reports = cls.purge_eligible_intermediates(db, dry_run=dry_run)

        # 3. Disk capacity check & alert
        disk_status = cls.check_disk_and_alert(db=db)

        total_freed = sum(r.get("freed_bytes", 0) for r in raw_reports) + sum(
            r.get("freed_bytes", 0) for r in intermediate_reports
        )

        summary = {
            "timestamp": datetime.utcnow().isoformat(),
            "dry_run": dry_run,
            "raw_sources_purged": len(raw_reports),
            "intermediates_purged": len(intermediate_reports),
            "total_freed_bytes": total_freed,
            "total_freed_mb": round(total_freed / (1024 * 1024), 2),
            "disk_percent_used": disk_status.get("percent_used", 0.0),
            "disk_free_gb": disk_status.get("free_gb", 0.0),
            "raw_reports": raw_reports,
            "intermediate_reports": intermediate_reports,
        }

        # 4. Emit platform event
        try:
            from shorts_engine.services.events.taxonomy import emit_platform_event
            emit_platform_event(
                event_name="storage.cleanup",
                payload=summary,
                entity_id="system_maintenance",
                entity_type="system",
                level="INFO",
                title="Daily Storage Cleanup Completed",
                message=(
                    f"Purged {len(raw_reports)} raw videos and {len(intermediate_reports)} intermediates. "
                    f"Freed {summary['total_freed_mb']} MB. Disk usage at {summary['disk_percent_used']}%."
                ),
                db=db,
            )
        except Exception as e:
            logger.debug("Failed emitting storage.cleanup platform event: %s", e)

        return summary


retention_service = RetentionPolicyService()
