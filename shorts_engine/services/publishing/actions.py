"""
Isolated Approval and Rejection Actions for Shorts Engine.
Single point-of-truth service functions governing human review transitions,
atomic publish slot allocation, and bulk review operations.
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session

from shorts_engine.db.models import (
    Clip,
    ClipStatus,
    Channel,
    PublishSlot,
    PublishSlotStatus,
)
from shorts_engine.services.publishing.slots import publish_slot_service
from shorts_engine.services.queue.durable_queue import durable_job_queue
from shorts_engine.services.events.taxonomy import emit_platform_event, CLIP_APPROVED

logger = logging.getLogger(__name__)


def approve_clip_for_publishing(
    clip_id: str,
    db: Session,
    actor: str = "operator",
    publish_inline: bool = True,
) -> Dict[str, Any]:
    """
    Atomically approves a clip for publishing.
    
    Guarantees:
    - Status-guarded update prevents double-approval across concurrent requests.
    - Idempotent and race-safe.
    - Channel resolved and vacant slot allocated atomically.
    - If publish_inline=True: executes native publishAt upload. On failure, releases
      the slot and marks clip FAILED_NEEDS_REVIEW.
    - If publish_inline=False: schedules clip, allocates slot, and enqueues to durable queue
      without blocking the HTTP thread with synchronous upload calls.
    - Emits CLIP_APPROVED platform telemetry event on all success paths.
    """
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        return {
            "clip_id": clip_id,
            "ok": False,
            "error": "Clip not found",
            "error_type": "NOT_FOUND",
        }

    # 1. Status-guarded atomic transition
    allowed_statuses = [
        ClipStatus.WAITING_APPROVAL,
        ClipStatus.FAILED,
        ClipStatus.FAILED_NEEDS_REVIEW,
    ]
    if clip.status not in allowed_statuses:
        return {
            "clip_id": clip_id,
            "ok": False,
            "status": clip.status.value,
            "error": f"Clip is in status {clip.status.value}, not reviewable",
            "error_type": "CONFLICT",
        }

    row_count = (
        db.query(Clip)
        .filter(
            Clip.id == clip_id,
            Clip.status.in_(allowed_statuses),
        )
        .update({Clip.status: ClipStatus.APPROVED}, synchronize_session=False)
    )
    if row_count == 0:
        db.refresh(clip)
        return {
            "clip_id": clip_id,
            "ok": False,
            "status": clip.status.value,
            "error": "Clip status changed concurrently",
            "error_type": "CONFLICT",
        }
    db.commit()
    db.refresh(clip)

    # 2. Resolve Channel
    cand = clip.candidate
    channel = (
        cand.source_video.channel
        if (cand and cand.source_video and cand.source_video.channel)
        else db.query(Channel).first()
    )
    if not channel:
        # Revert status
        db.query(Clip).filter_by(id=clip_id).update(
            {Clip.status: ClipStatus.WAITING_APPROVAL}, synchronize_session=False
        )
        db.commit()
        return {
            "clip_id": clip_id,
            "ok": False,
            "error": "No YouTube channel configured for clip approval.",
            "error_type": "NO_CHANNEL",
        }

    # 3. Atomic Slot Allocation
    try:
        slot = publish_slot_service.allocate_slot(
            channel_id=channel.id,
            clip_id=clip.id,
            db=db,
        )
    except Exception as slot_err:
        logger.warning("Slot allocation failed for clip %s: %s", clip_id, slot_err)
        db.query(Clip).filter_by(id=clip_id).update(
            {Clip.status: ClipStatus.WAITING_APPROVAL}, synchronize_session=False
        )
        db.commit()
        return {
            "clip_id": clip_id,
            "ok": False,
            "error": f"Slot allocation failed: {slot_err}",
            "error_type": "SLOT_ALLOCATION_FAILED",
        }

    # 4. Publication Path
    if publish_inline:
        # Single approve path: immediate native publishAt upload
        pub_res = publish_slot_service.publish_slot_now(slot=slot, clip=clip, db=db)

        if not pub_res.get("success"):
            # On publish failure: release the slot and move clip to FAILED_NEEDS_REVIEW
            logger.warning(
                "Inline publishing failed for clip %s (%s). Releasing slot and marking FAILED_NEEDS_REVIEW.",
                clip_id,
                pub_res.get("reason"),
            )
            slot.status = PublishSlotStatus.CANCELLED.value
            clip.status = ClipStatus.FAILED_NEEDS_REVIEW
            db.commit()
            return {
                "clip_id": clip_id,
                "ok": False,
                "status": ClipStatus.FAILED_NEEDS_REVIEW.value,
                "error": pub_res.get("reason") or "Publishing failed",
                "error_type": pub_res.get("error_type", "PUBLISH_FAILED"),
                "slot_id": slot.id,
            }

        # Success inline
        clip.scheduled_publish_at = slot.slot_start
        db.commit()

        _emit_approval_event(clip, slot, actor, pub_res, db)
        return {
            "clip_id": clip_id,
            "ok": True,
            "status": clip.status.value,
            "slot_id": slot.id,
            "slot_start": slot.slot_start.isoformat(),
            "youtube_video_id": pub_res.get("youtube_video_id"),
            "pub_result": pub_res,
        }

    else:
        # Bulk approve path: do NOT publish synchronously inline.
        # Mark scheduled and hand off to durable queue for worker processing.
        clip.scheduled_publish_at = slot.slot_start
        clip.status = ClipStatus.APPROVED
        slot.status = PublishSlotStatus.RESERVED.value

        job = durable_job_queue.enqueue(
            job_type="publish",
            entity_id=clip.id,
            entity_type="clip",
            stage="publish",
            next_run_at=slot.slot_start,
            payload={
                "clip_id": clip.id,
                "slot_id": slot.id,
                "scheduled_at": slot.slot_start.isoformat(),
            },
            db=db,
        )
        db.commit()

        _emit_approval_event(
            clip,
            slot,
            actor,
            {"status": "QUEUED", "job_id": job.id if job else None},
            db,
        )
        return {
            "clip_id": clip_id,
            "ok": True,
            "status": clip.status.value,
            "slot_id": slot.id,
            "slot_start": slot.slot_start.isoformat(),
            "enqueued": True,
            "job_id": job.id if job else None,
        }


def reject_clip_action(
    clip_id: str,
    db: Session,
    actor: str = "operator",
    reason_code: Optional[str] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Atomically rejects a clip and persists reason code, note, and rejected_at timestamp.
    Dual-writes to quality_check_result JSON for backward compatibility.
    """
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        return {
            "clip_id": clip_id,
            "ok": False,
            "error": "Clip not found",
            "error_type": "NOT_FOUND",
        }

    if clip.status != ClipStatus.WAITING_APPROVAL:
        return {
            "clip_id": clip_id,
            "ok": False,
            "status": clip.status.value,
            "error": f"Clip is in status {clip.status.value}, not WAITING_APPROVAL",
            "error_type": "CONFLICT",
        }

    code = reason_code or "MANUAL_REJECTION"
    clip.status = ClipStatus.REJECTED
    clip.rejection_reason_code = code
    clip.rejection_note = note
    clip.rejected_at = datetime.utcnow()

    # Dual-write into quality_check_result
    try:
        existing = json.loads(clip.quality_check_result or "{}")
    except Exception:
        existing = {}
    existing["rejection_reason"] = note if note else code
    existing["rejection_reason_code"] = code
    existing["rejection_note"] = note
    clip.quality_check_result = json.dumps(existing)

    db.commit()
    logger.info("Actor '%s' rejected clip %s (code: %s, note: %s)", actor, clip_id, code, note)
    return {
        "clip_id": clip_id,
        "ok": True,
        "status": ClipStatus.REJECTED.value,
        "reason_code": code,
        "note": note,
    }


def bulk_approve_clips(
    clip_ids: List[str],
    db: Session,
    actor: str = "operator",
) -> Dict[str, Any]:
    """
    Bulk approves a batch of clips.
    Uses publish_inline=False to schedule and enqueue without blocking the HTTP request.
    Per-clip results are returned; one failure does not abort the batch.
    """
    seen = set()
    unique_ids = [cid for cid in clip_ids if cid and not (cid in seen or seen.add(cid))]

    results = []
    for cid in unique_ids:
        res = approve_clip_for_publishing(
            clip_id=cid,
            db=db,
            actor=actor,
            publish_inline=False,
        )
        results.append(res)

    succeeded = sum(1 for r in results if r.get("ok"))
    failed = len(results) - succeeded
    logger.info("Actor '%s' bulk-approved %d clips (%d succeeded, %d failed)", actor, len(results), succeeded, failed)

    return {
        "total": len(results),
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    }


def bulk_reject_clips(
    clip_ids: List[str],
    db: Session,
    actor: str = "operator",
    reason_code: Optional[str] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Bulk rejects a batch of clips with optional shared reason code and note.
    Per-clip results are returned; one failure does not abort the batch.
    """
    seen = set()
    unique_ids = [cid for cid in clip_ids if cid and not (cid in seen or seen.add(cid))]

    results = []
    for cid in unique_ids:
        res = reject_clip_action(
            clip_id=cid,
            db=db,
            actor=actor,
            reason_code=reason_code,
            note=note,
        )
        results.append(res)

    succeeded = sum(1 for r in results if r.get("ok"))
    failed = len(results) - succeeded
    logger.info("Actor '%s' bulk-rejected %d clips (%d succeeded, %d failed)", actor, len(results), succeeded, failed)

    return {
        "total": len(results),
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    }


def _emit_approval_event(
    clip: Clip,
    slot: PublishSlot,
    actor: str,
    pub_res: Dict[str, Any],
    db: Session,
) -> None:
    """Emits consistent CLIP_APPROVED telemetry across all paths."""
    try:
        cand = clip.candidate
        emit_platform_event(
            event_name=CLIP_APPROVED,
            payload={
                "clip_id": clip.id,
                "video_id": cand.source_video_id if cand else None,
                "approved_by": actor,
                "title": clip.generated_title,
                "slot_start": slot.slot_start.isoformat(),
                "pub_status": pub_res.get("status"),
                "youtube_video_id": pub_res.get("youtube_video_id"),
            },
            entity_id=clip.id,
            entity_type="clip",
            level="SUCCESS" if pub_res.get("success", True) else "WARNING",
            title="Clip Approved",
            message=f"Clip '{clip.generated_title or clip.id[:8]}' approved by {actor}.",
            db=db,
        )
    except Exception as ev_err:
        logger.warning("Failed to emit clip.approved telemetry event: %s", ev_err)
