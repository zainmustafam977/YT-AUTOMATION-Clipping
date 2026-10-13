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
    publish_inline: bool = False,
) -> Dict[str, Any]:
    """
    Atomically approves a clip for publishing in an enqueue-only workflow (W3-2).
    
    Scheduling Semantics (A2):
    Moves from upload-now-private-publishAt to upload-at-slot-time.
    Rationale:
    1. Quota pacing across days: YouTube Data API v3 imposes daily write quota (1,600 units/insert).
       Uploading at slot time paces API consumption across multiple daily quota resets rather than
       exhausting quota in burst approval sessions.
    2. Spreads bulk approvals cleanly across calendar slots without holding HTTP connections open.
    3. Guarantees zero in-request WAN uploads, eliminating HTTP timeout vulnerabilities.
    
    Transaction Atomicity (A3):
    Clip status update + slot allocation + publish job creation are committed in a
    SINGLE atomic database transaction. Any exception before final commit rolls back all
    mutations, leaving the clip in its initial status (no slot without job, no APPROVED without slot).
    """
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        return {
            "clip_id": clip_id,
            "ok": False,
            "error": "Clip not found",
            "error_type": "NOT_FOUND",
        }

    # 1. Status-guarded atomic check
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

    # 2. Resolve Channel
    cand = clip.candidate
    channel = (
        cand.source_video.channel
        if (cand and cand.source_video and cand.source_video.channel)
        else db.query(Channel).first()
    )
    if not channel:
        return {
            "clip_id": clip_id,
            "ok": False,
            "error": "No YouTube channel configured for clip approval.",
            "error_type": "NO_CHANNEL",
        }

    # 3. Single Atomic Transaction: Status update + Slot allocation + Publish job enqueue
    try:
        # Atomic slot allocation (staged, uncommitted)
        slot = publish_slot_service.allocate_slot(
            channel_id=channel.id,
            clip_id=clip.id,
            db=db,
            commit=False,
        )

        # Mutate clip and slot state
        clip.status = ClipStatus.APPROVED
        clip.scheduled_publish_at = slot.slot_start
        slot.status = PublishSlotStatus.RESERVED.value

        # Enqueue durable publish job bound to slot (staged, uncommitted)
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
            commit=False,
        )

        # Single atomic commit for clip + slot + job
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("Atomic approval transaction failed for clip %s: %s", clip_id, exc)
        return {
            "clip_id": clip_id,
            "ok": False,
            "error": f"Slot allocation failed: {exc}",
            "error_type": "SLOT_ALLOCATION_FAILED",
        }

    # 4. Telemetry event emission
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
        "job_id": job.id if job else None,
        "enqueued": True,
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
