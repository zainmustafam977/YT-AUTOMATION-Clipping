"""
Publish Slot Management & Native Scheduling Service.

Replaces the legacy polling-based DripFeedDaemon with:
1. Collision-free, atomic publish_slots table allocation (unique on channel_id + slot_start).
2. Native YouTube publishAt scheduling (uploaded as PRIVATE with publishAt=<slot_start ISO>).
3. Post-upload in-place management:
   - Un-approve: deletes video on YouTube via videos().delete (50 units).
   - Reschedule: updates publishAt on YouTube via videos().update (50 units).
   - Metadata edits: updates title/snippet on YouTube via videos().update (50 units).
   - Only changed video files require a re-upload.
4. Immediate operator surface in Review Queue for auth, quota, or upload errors:
   - Auth errors: channel marked needs_reauth, clip marked FAILED_NEEDS_REVIEW.
   - Quota exceeded: deferred to Pacific midnight via durable queue.
   - Transient errors: queued in durable queue for retry with exponential backoff.
5. Migration for legacy drip-feed clips with scheduled_publish_at so zero clips are orphaned.
6. Platform-agnostic interface supporting deferred jobs for other syndication platforms.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from shorts_engine.config.settings import settings
from shorts_engine.db.models import (
    Clip,
    ClipStatus,
    Channel,
    ChannelCredential,
    PublishSlot,
    PublishSlotStatus,
    PublishLog,
    PipelineJob,
    PipelineJobStatus,
)
from shorts_engine.services.publishing.worker import YouTubePublishWorker
from shorts_engine.services.publishing.quota import (
    QuotaTracker,
    get_next_pacific_midnight,
    VIDEOS_INSERT_COST_UNITS,
)
from shorts_engine.services.queue.durable_queue import durable_job_queue
from shorts_engine.services.events.taxonomy import emit_platform_event, CLIP_APPROVED, CLIP_PUBLISHED

logger = logging.getLogger(__name__)


class PublishSlotService:
    """
    Central service for collision-free publish slot scheduling,
    immediate native YouTube upload execution, and post-upload in-place management.
    """

    def __init__(self, worker: Optional[YouTubePublishWorker] = None):
        self.worker = worker or YouTubePublishWorker()
        self.quota_tracker = self.worker.quota_tracker or QuotaTracker()

    # ==========================================================================
    # Slot Calculation & Atomic Allocation
    # ==========================================================================

    @staticmethod
    def get_channel_timezone(channel: Optional[Channel]) -> ZoneInfo:
        """Resolves channel timezone with fallback to configured default."""
        tz_str = getattr(settings, "PUBLISH_DEFAULT_TIMEZONE", "America/New_York")
        if channel and hasattr(channel, "upload_defaults"):
            channel_tz = channel.upload_defaults.get("timezone")
            if channel_tz:
                tz_str = str(channel_tz)
        try:
            return ZoneInfo(tz_str)
        except Exception:
            return ZoneInfo("UTC")

    @staticmethod
    def get_channel_slot_times(channel: Optional[Channel]) -> List[str]:
        """Resolves daily slot times (e.g. ['10:00', '18:00'])."""
        default_times = getattr(settings, "PUBLISH_SLOT_TIMES_LOCAL", ["10:00", "18:00"])
        if channel and hasattr(channel, "upload_defaults"):
            custom = channel.upload_defaults.get("slot_times")
            if isinstance(custom, list) and custom:
                return sorted([str(t).strip() for t in custom if str(t).strip()])
        return sorted(list(default_times))

    def calculate_next_available_slot(
        self,
        channel: Channel,
        db: Session,
        start_from: Optional[datetime] = None,
        platform: str = "youtube",
    ) -> datetime:
        """
        Calculates the earliest vacant slot in the channel's local timezone (DST-aware).
        Enforces min_lead_minutes (default 15m) ahead of current time.
        """
        tz = self.get_channel_timezone(channel)
        slot_times_str = self.get_channel_slot_times(channel)
        lead_minutes = getattr(settings, "PUBLISH_MIN_SLOT_LEAD_MINUTES", 15)

        now_local = datetime.now(tz)
        earliest_local = now_local + timedelta(minutes=lead_minutes)

        if start_from:
            if start_from.tzinfo is None:
                start_local = start_from.replace(tzinfo=timezone.utc).astimezone(tz)
            else:
                start_local = start_from.astimezone(tz)
            if start_local > earliest_local:
                earliest_local = start_local

        current_date = earliest_local.date()

        # Search up to 60 days ahead for the next vacant slot
        for day_offset in range(60):
            target_date = current_date + timedelta(days=day_offset)
            for time_str in slot_times_str:
                try:
                    parts = time_str.split(":")
                    hour = int(parts[0])
                    minute = int(parts[1]) if len(parts) > 1 else 0
                except (ValueError, IndexError):
                    continue

                slot_local = datetime(
                    target_date.year,
                    target_date.month,
                    target_date.day,
                    hour,
                    minute,
                    0,
                    tzinfo=tz,
                )

                if slot_local < earliest_local:
                    continue

                # Convert to UTC naive datetime for DB comparison
                slot_utc = slot_local.astimezone(timezone.utc).replace(tzinfo=None)

                # Check if slot is occupied
                existing = (
                    db.query(PublishSlot)
                    .filter(
                        PublishSlot.channel_id == channel.id,
                        PublishSlot.slot_start == slot_utc,
                        PublishSlot.status != PublishSlotStatus.CANCELLED.value,
                    )
                    .first()
                )

                if not existing:
                    return slot_utc

        # Fallback to tomorrow if somehow all 60 days full
        return (datetime.utcnow() + timedelta(days=1)).replace(microsecond=0)

    def allocate_slot(
        self,
        channel_id: str,
        clip_id: str,
        db: Session,
        platform: str = "youtube",
        preferred_time: Optional[datetime] = None,
        commit: bool = True,
    ) -> PublishSlot:
        """
        Atomically allocates the next collision-free publish slot for a clip.
        Handles race conditions gracefully via SQLite / Postgres savepoints and retry loop.
        Supports commit=False for enclosing callers managing multi-operation atomic transactions.
        """
        channel = db.query(Channel).filter_by(id=channel_id).first()
        if not channel:
            raise ValueError(f"Channel {channel_id} not found for slot allocation.")

        clip = db.query(Clip).filter_by(id=clip_id).first()
        if not clip:
            raise ValueError(f"Clip {clip_id} not found for slot allocation.")

        # Check if this clip already has an active slot
        existing_active = (
            db.query(PublishSlot)
            .filter(
                PublishSlot.channel_id == channel.id,
                PublishSlot.clip_id == clip.id,
                PublishSlot.status.in_([
                    PublishSlotStatus.RESERVED.value,
                    PublishSlotStatus.SCHEDULED.value,
                ]),
            )
            .first()
        )
        if existing_active:
            logger.info("Clip %s already occupies slot %s; returning existing slot.", clip_id, existing_active.slot_start)
            return existing_active

        search_start = preferred_time
        max_attempts = 30

        for attempt in range(max_attempts):
            slot_utc = self.calculate_next_available_slot(
                channel=channel,
                db=db,
                start_from=search_start,
                platform=platform,
            )

            try:
                slot = PublishSlot(
                    channel_id=channel.id,
                    clip_id=clip.id,
                    slot_start=slot_utc,
                    platform=platform,
                    status=PublishSlotStatus.RESERVED.value,
                )
                db.add(slot)
                if commit:
                    db.commit()
                    db.refresh(slot)
                else:
                    db.flush()
                    db.refresh(slot)
                logger.info(
                    "Allocated slot %s UTC for clip %s on channel %s (%s)",
                    slot_utc.isoformat(), clip_id, channel.name, platform,
                )
                return slot
            except IntegrityError:
                # Collision: another thread/worker grabbed this slot simultaneously
                db.rollback()
                logger.warning(
                    "Slot collision on %s UTC for channel %s; retrying with next slot (attempt %d/%d)...",
                    slot_utc.isoformat(), channel_id, attempt + 1, max_attempts,
                )
                # Advance search time past collided slot
                search_start = slot_utc + timedelta(minutes=1)

        raise RuntimeError(f"Failed to allocate a collision-free slot after {max_attempts} attempts.")

    # ==========================================================================
    # Immediate Upload & Operator Surfacing
    # ==========================================================================

    def publish_slot_now(
        self,
        slot: PublishSlot,
        clip: Clip,
        db: Session,
    ) -> Dict[str, Any]:
        """
        DEPRECATED (W3-2): Synchronous in-request publishing is retired.
        Production approvals schedule and enqueue publish jobs for WorkerDaemon
        execution at slot time. This method is retained for legacy unit tests only.
        """
        channel = db.query(Channel).filter_by(id=slot.channel_id).first()
        if not channel:
            channel = clip.candidate.source_video.channel if (clip.candidate and clip.candidate.source_video) else None

        if slot.platform.lower() != "youtube":
            # Other syndication platforms (TikTok, Instagram) use durable deferred queue
            logger.info("Platform %s does not use native publishAt; enqueuing deferred job.", slot.platform)
            job = durable_job_queue.enqueue(
                job_type=f"PUBLISH_{slot.platform.upper()}",
                entity_id=clip.id,
                entity_type="clip",
                stage="publish",
                next_run_at=slot.slot_start,
                payload={"clip_id": clip.id, "platform": slot.platform, "slot_id": slot.id},
                db=db,
            )
            slot.status = PublishSlotStatus.RESERVED.value
            clip.scheduled_publish_at = slot.slot_start
            clip.status = ClipStatus.APPROVED
            db.commit()
            return {
                "success": True,
                "status": "DEFERRED_JOB_QUEUED",
                "slot_id": slot.id,
                "slot_start": slot.slot_start.isoformat(),
                "job_id": job.id,
            }

        # --- YouTube Native publishAt Upload ---
        # 1. Pre-flight credential check
        cred = db.query(ChannelCredential).filter_by(channel_id=channel.id).first() if channel else None
        if cred and cred.status == "needs_reauth":
            err_msg = "Channel OAuth token expired or revoked. Channel is marked 'needs_reauth'."
            logger.warning("Upload refused for clip %s: %s", clip.id, err_msg)
            clip.status = ClipStatus.FAILED_NEEDS_REVIEW
            slot.status = PublishSlotStatus.CANCELLED.value
            db.commit()
            return {
                "success": False,
                "status": "FAILED_NEEDS_REVIEW",
                "error_type": "AUTH_ERROR",
                "reason": err_msg,
            }

        # 2. Pre-flight quota check (Dec 2025 non-hardcoded insert cost)
        can_pub, remaining_quota, quota_reason = self.quota_tracker.can_publish(
            db=db, cost=VIDEOS_INSERT_COST_UNITS
        )
        if not can_pub:
            # Quota exhausted: defer upload past Pacific midnight via durable queue
            next_midnight_utc, midnight_iso = get_next_pacific_midnight()
            logger.warning(
                "YouTube quota limit reached for clip %s: %s (deferring to %s)",
                clip.id, quota_reason, midnight_iso,
            )
            job = durable_job_queue.enqueue(
                job_type="publish",
                entity_id=clip.id,
                entity_type="clip",
                stage="publish",
                next_run_at=next_midnight_utc,
                payload={
                    "clip_id": clip.id,
                    "slot_id": slot.id,
                    "scheduled_at": slot.slot_start.isoformat(),
                },
                db=db,
            )
            clip.status = ClipStatus.APPROVED
            clip.scheduled_publish_at = slot.slot_start
            slot.status = PublishSlotStatus.RESERVED.value
            db.commit()
            return {
                "success": False,
                "status": "QUOTA_DEFERRED",
                "error_type": "QUOTA_EXCEEDED",
                "reason": quota_reason,
                "deferred_until": midnight_iso,
                "job_id": job.id,
            }

        # 3. Execute immediate video upload with native publishAt
        try:
            payload = {
                "scheduled_at": slot.slot_start,
                "dry_run": settings.DRY_RUN_PUBLISHING,
            }
            res = self.worker.execute_publish_intent(
                clip_id=clip.id,
                payload=payload,
                db=db,
            )

            if not res.get("success"):
                err_status = res.get("status", "FAILED")
                err_msg = res.get("error", "Upload failed")
                if err_status == "DEFERRED":
                    return {
                        "success": False,
                        "status": "QUOTA_DEFERRED",
                        "error_type": "QUOTA_EXCEEDED",
                        "reason": err_msg,
                    }
                elif err_status == "QUARANTINED":
                    clip.status = ClipStatus.FAILED_NEEDS_REVIEW
                    slot.status = PublishSlotStatus.CANCELLED.value
                    db.commit()
                    return {
                        "success": False,
                        "status": "FAILED_NEEDS_REVIEW",
                        "error_type": "AUTH_ERROR",
                        "reason": err_msg,
                    }
                else:
                    return {
                        "success": False,
                        "status": "RETRY_QUEUED",
                        "error_type": "UPLOAD_FAILED",
                        "reason": err_msg,
                    }

            yt_id = res.get("youtube_video_id")
            slot.youtube_video_id = yt_id
            slot.status = PublishSlotStatus.SCHEDULED.value
            clip.status = ClipStatus.SCHEDULED
            clip.scheduled_publish_at = slot.slot_start
            db.commit()

            logger.info(
                "Successfully uploaded clip %s to YouTube (ID: %s) scheduled for %s UTC.",
                clip.id, yt_id, slot.slot_start.isoformat(),
            )
            return {
                "success": True,
                "status": "SCHEDULED",
                "youtube_video_id": yt_id,
                "slot_id": slot.id,
                "slot_start": slot.slot_start.isoformat(),
                "dry_run": res.get("dry_run", False),
            }

        except Exception as upload_err:
            logger.error("Upload error for clip %s: %s", clip.id, upload_err, exc_info=True)
            # Enqueue for retry in durable queue
            job = durable_job_queue.enqueue(
                job_type="publish",
                entity_id=clip.id,
                entity_type="clip",
                stage="publish",
                payload={"clip_id": clip.id, "slot_id": slot.id, "scheduled_at": slot.slot_start.isoformat()},
                db=db,
            )
            db.commit()
            return {
                "success": False,
                "status": "RETRY_QUEUED",
                "error_type": "UPLOAD_FAILED",
                "reason": str(upload_err),
                "job_id": job.id,
            }

    # ==========================================================================
    # In-Place Post-Upload Management (Un-Approve, Reschedule, Metadata)
    # ==========================================================================

    def unapprove_clip(self, clip_id: str, db: Session) -> Dict[str, Any]:
        """
        Un-approves a clip:
        If already uploaded to YouTube, calls videos().delete (50 units).
        Releases or cancels the publish_slot and resets clip to WAITING_APPROVAL.
        """
        clip = db.query(Clip).filter_by(id=clip_id).first()
        if not clip:
            raise ValueError(f"Clip {clip_id} not found")

        slot = (
            db.query(PublishSlot)
            .filter(
                PublishSlot.clip_id == clip.id,
                PublishSlot.status.in_([
                    PublishSlotStatus.RESERVED.value,
                    PublishSlotStatus.SCHEDULED.value,
                ]),
            )
            .first()
        )

        deleted_on_youtube = False
        yt_id = slot.youtube_video_id if slot else None

        if yt_id:
            logger.info("Un-approving clip %s: deleting video %s on YouTube...", clip_id, yt_id)
            del_res = self.worker.execute_delete_intent(
                youtube_video_id=yt_id,
                channel_id=slot.channel_id if slot else None,
                db=db,
            )
            deleted_on_youtube = del_res.get("success", False)

        if slot:
            slot.status = PublishSlotStatus.CANCELLED.value
            slot.clip_id = None

        clip.status = ClipStatus.WAITING_APPROVAL
        clip.scheduled_publish_at = None

        # Clean up any pending PublishLog
        p_log = db.query(PublishLog).filter_by(clip_id=clip.id).first()
        if p_log:
            p_log.status = "CANCELLED"

        db.commit()
        logger.info("Successfully un-approved clip %s (deleted_on_youtube=%s)", clip_id, deleted_on_youtube)
        return {
            "success": True,
            "clip_id": clip_id,
            "status": ClipStatus.WAITING_APPROVAL.value,
            "deleted_on_youtube": deleted_on_youtube,
            "youtube_video_id": yt_id,
        }

    def reschedule_clip(
        self,
        clip_id: str,
        new_slot_time: datetime,
        db: Session,
    ) -> Dict[str, Any]:
        """
        Reschedules an approved/scheduled clip:
        If already uploaded to YouTube, calls videos().update with new publishAt (50 units).
        Updates publish_slot and clip timestamp with zero re-upload.
        """
        clip = db.query(Clip).filter_by(id=clip_id).first()
        if not clip:
            raise ValueError(f"Clip {clip_id} not found")

        if clip.status == ClipStatus.WAITING_APPROVAL:
            raise ValueError("Clip is awaiting approval. Approve clip before rescheduling.")

        # Convert to UTC naive datetime for DB
        dt_utc = new_slot_time
        if dt_utc.tzinfo is not None:
            dt_utc = dt_utc.astimezone(timezone.utc).replace(tzinfo=None)
        dt_utc = dt_utc.replace(microsecond=0)

        slot = (
            db.query(PublishSlot)
            .filter(
                PublishSlot.clip_id == clip.id,
                PublishSlot.status.in_([
                    PublishSlotStatus.RESERVED.value,
                    PublishSlotStatus.SCHEDULED.value,
                ]),
            )
            .first()
        )

        updated_on_youtube = False
        yt_id = slot.youtube_video_id if slot else None

        if yt_id:
            logger.info(
                "Rescheduling clip %s (YouTube ID: %s) to %s UTC without re-upload...",
                clip_id, yt_id, dt_utc.isoformat(),
            )
            upd_res = self.worker.execute_reschedule_intent(
                youtube_video_id=yt_id,
                new_slot_time=dt_utc,
                channel_id=slot.channel_id if slot else None,
                db=db,
            )
            updated_on_youtube = upd_res.get("success", False)

        if slot:
            slot.slot_start = dt_utc
        else:
            # Create a slot if not already exists
            cand = clip.candidate
            channel = cand.source_video.channel if (cand and cand.source_video) else db.query(Channel).first()
            if channel:
                slot = PublishSlot(
                    channel_id=channel.id,
                    clip_id=clip.id,
                    slot_start=dt_utc,
                    platform="youtube",
                    status=PublishSlotStatus.SCHEDULED.value if yt_id else PublishSlotStatus.RESERVED.value,
                    youtube_video_id=yt_id,
                )
                db.add(slot)

        clip.scheduled_publish_at = dt_utc
        db.commit()

        logger.info("Successfully rescheduled clip %s to %s UTC", clip_id, dt_utc.isoformat())
        return {
            "success": True,
            "clip_id": clip_id,
            "scheduled_publish_at": dt_utc.isoformat(),
            "updated_on_youtube": updated_on_youtube,
            "youtube_video_id": yt_id,
        }

    def update_clip_metadata(
        self,
        clip_id: str,
        db: Session,
        title: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Updates metadata for a clip:
        If already uploaded to YouTube, calls videos().update (50 units).
        Only a changed video file requires a re-upload.
        """
        clip = db.query(Clip).filter_by(id=clip_id).first()
        if not clip:
            raise ValueError(f"Clip {clip_id} not found")

        slot = (
            db.query(PublishSlot)
            .filter(
                PublishSlot.clip_id == clip.id,
                PublishSlot.status.in_([
                    PublishSlotStatus.RESERVED.value,
                    PublishSlotStatus.SCHEDULED.value,
                ]),
            )
            .first()
        )

        yt_id = slot.youtube_video_id if slot else None
        updated_on_youtube = False

        if yt_id:
            upd_res = self.worker.execute_metadata_update_intent(
                clip_id=clip.id,
                payload={"title": title, "youtube_video_id": yt_id},
                db=db,
            )
            updated_on_youtube = upd_res.get("success", False)

        if title:
            clip.generated_title = title
            clip.draft_title = title
        if description:
            clip.generated_description = description
            clip.draft_description = description
        if tags:
            clip.generated_tags = json.dumps(tags)

        db.commit()
        return {
            "success": True,
            "clip_id": clip_id,
            "updated_on_youtube": updated_on_youtube,
            "title": title,
        }

    # ==========================================================================
    # Migration & Dashboard Visibility
    # ==========================================================================

    @classmethod
    def migrate_unorphaned_drip_feed_clips(cls, db: Session) -> int:
        """
        Migrates legacy clips that already have scheduled_publish_at but are not yet published.
        Allocates publish_slots and triggers native publishAt upload so zero clips are orphaned.
        """
        service = cls()
        now = datetime.utcnow()

        legacy_clips = (
            db.query(Clip)
            .filter(
                Clip.status.in_([ClipStatus.APPROVED, ClipStatus.WAITING_APPROVAL]),
                Clip.scheduled_publish_at.isnot(None),
            )
            .all()
        )

        migrated_count = 0
        for clip in legacy_clips:
            # Check if clip already has a valid publish_slot
            existing_slot = (
                db.query(PublishSlot)
                .filter_by(clip_id=clip.id)
                .first()
            )
            if existing_slot and existing_slot.status != PublishSlotStatus.CANCELLED.value:
                continue

            cand = clip.candidate
            channel = cand.source_video.channel if (cand and cand.source_video) else None
            if not channel:
                channel = db.query(Channel).first()
            if not channel:
                continue

            target_slot_time = clip.scheduled_publish_at
            if target_slot_time <= now:
                # Slot has passed; allocate next upcoming slot
                target_slot_time = service.calculate_next_available_slot(channel, db)

            try:
                slot = service.allocate_slot(
                    channel_id=channel.id,
                    clip_id=clip.id,
                    db=db,
                    preferred_time=target_slot_time,
                )
                service.publish_slot_now(slot=slot, clip=clip, db=db)
                migrated_count += 1
                logger.info("Migrated legacy scheduled clip %s to slot %s", clip.id, slot.slot_start)
            except Exception as mig_err:
                logger.error("Failed to migrate legacy clip %s: %s", clip.id, mig_err)

        db.commit()
        logger.info("Completed drip-feed migration: %d clips successfully migrated to slots.", migrated_count)
        return migrated_count

    @staticmethod
    def get_upcoming_schedule(
        db: Session,
        channel_id: Optional[str] = None,
        platform: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Returns the upcoming publication schedule across all allocated slots."""
        query = (
            db.query(PublishSlot)
            .filter(
                PublishSlot.status.in_([
                    PublishSlotStatus.RESERVED.value,
                    PublishSlotStatus.SCHEDULED.value,
                ]),
                PublishSlot.slot_start >= (datetime.utcnow() - timedelta(hours=2)),
            )
        )
        if channel_id:
            query = query.filter(PublishSlot.channel_id == channel_id)
        if platform:
            query = query.filter(PublishSlot.platform == platform)

        slots = query.order_by(PublishSlot.slot_start.asc()).limit(limit).all()
        results = []

        for s in slots:
            clip = s.clip
            cand = clip.candidate if clip else None
            title = clip.generated_title if clip else "Untitled Short"
            ch = s.channel
            thumb_url = f"/api/thumbnails/clip_{clip.id}.jpg" if clip else None

            results.append({
                "slot_id": s.id,
                "clip_id": s.clip_id,
                "channel_id": s.channel_id,
                "channel_name": ch.name if ch else "Channel",
                "title": title,
                "slot_start": s.slot_start.isoformat(),
                "slot_display": s.slot_start.strftime("%b %d, %Y at %H:%M UTC"),
                "platform": s.platform,
                "status": s.status,
                "youtube_video_id": s.youtube_video_id,
                "thumbnail_url": thumb_url,
            })

        return results


# Global singleton instance
publish_slot_service = PublishSlotService()
