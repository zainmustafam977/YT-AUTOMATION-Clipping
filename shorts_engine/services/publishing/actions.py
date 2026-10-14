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
    PipelineJob,
    PipelineJobStatus,
    PublishLog,
    AuditLog,
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
    Atomically rejects a clip, cancelling any pending publish job via atomic CAS,
    releasing/cancelling the bound publish slot (preserving ownership history), and
    persisting rejection metadata and AuditLog (I3).
    Rejects rejection of actively running or retrying upload attempts with CONFLICT.
    """
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        return {
            "clip_id": clip_id,
            "ok": False,
            "error": "Clip not found",
            "error_type": "NOT_FOUND",
        }

    if clip.status in (ClipStatus.PUBLISHING, ClipStatus.PUBLISHED):
        return {
            "clip_id": clip_id,
            "ok": False,
            "status": clip.status.value,
            "error": f"Clip is in status {clip.status.value}, cannot reject an in-flight or published clip",
            "error_type": "CONFLICT",
        }

    # 1. Inspect any active publish PipelineJob
    job = (
        db.query(PipelineJob)
        .filter(
            PipelineJob.entity_id == clip.id,
            PipelineJob.stage == "publish",
        )
        .first()
    )

    if job:
        # Reject cancellation of unresolved attempted uploads (I3)
        has_session = False
        if job.payload_json:
            try:
                payload_dict = json.loads(job.payload_json)
                has_session = bool(payload_dict.get("upload_session_uri"))
            except Exception:
                pass
        if has_session or job.attempt_count > 0:
            return {
                "clip_id": clip_id,
                "ok": False,
                "status": clip.status.value,
                "error": "Cannot reject clip with an active or retrying upload attempt. Await completion or trigger explicit reconciliation.",
                "error_type": "CONFLICT",
            }

        if job.status == PipelineJobStatus.RUNNING:
            return {
                "clip_id": clip_id,
                "ok": False,
                "status": clip.status.value,
                "error": "Publication is currently RUNNING and cannot be rejected.",
                "error_type": "CONFLICT",
            }

        if job.status == PipelineJobStatus.PENDING:
            # Atomic CAS update: ensure status was still PENDING at moment of reject
            rows_updated = (
                db.query(PipelineJob)
                .filter(
                    PipelineJob.id == job.id,
                    PipelineJob.status == PipelineJobStatus.PENDING,
                )
                .update(
                    {
                        PipelineJob.status: PipelineJobStatus.CANCELLED,
                        PipelineJob.completed_at: datetime.utcnow(),
                        PipelineJob.locked_by: None,
                        PipelineJob.locked_at: None,
                    },
                    synchronize_session=False,
                )
            )
            if rows_updated == 0:
                db.rollback()
                return {
                    "clip_id": clip_id,
                    "ok": False,
                    "status": clip.status.value,
                    "error": "Publication was concurrently claimed by worker and is currently RUNNING; cannot reject.",
                    "error_type": "CONFLICT",
                }

    # 2. Cancel bound PublishSlot while preserving ownership history (I8)
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

    if slot:
        slot.status = PublishSlotStatus.CANCELLED.value
        # slot.clip_id retained for historical audit trail

    code = reason_code or "MANUAL_REJECTION"
    clip.status = ClipStatus.REJECTED
    clip.scheduled_publish_at = None
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

    # Clean up any non-SUCCESS PublishLog (mark CANCELLED), write 0 new rows
    p_log = db.query(PublishLog).filter_by(clip_id=clip.id).first()
    if p_log and p_log.status != "SUCCESS":
        p_log.status = "CANCELLED"

    audit = AuditLog(
        actor=actor,
        event="clip.rejected",
        channel_id=slot.channel_id if slot else None,
        job_id=job.id if job else None,
        details=json.dumps({
            "clip_id": clip.id,
            "reason_code": code,
            "note": note,
            "slot_id": slot.id if slot else None,
        }),
    )
    db.add(audit)

    db.commit()
    logger.info("Actor '%s' rejected clip %s (code: %s, note: %s)", actor, clip_id, code, note)
    return {
        "clip_id": clip_id,
        "ok": True,
        "status": ClipStatus.REJECTED.value,
        "reason_code": code,
        "note": note,
    }


def recover_publish_job(
    job_id: str,
    db: Session,
    reason: str = "Operator manual recovery",
    actor: str = "operator",
) -> Dict[str, Any]:
    """
    Explicitly recovers a failed, quarantined, dead-lettered, or cancelled publish job (I4).
    
    1. Verifies the target clip exists and has not already been published.
    2. Verifies any bound slot is valid and RESERVED; allocates a new valid slot if
       the prior slot expired or was cancelled.
    3. Preserves prior attempt history in AuditLog while resetting attempt_count to 0
       so the job gets a fresh budget.
    4. Preserves any existing upload_session_uri or idempotency evidence in payload_json.
    5. Transitions job back to PENDING and returns success payload.
    """
    job = (
        db.query(PipelineJob)
        .filter(PipelineJob.id == job_id, PipelineJob.stage == "publish")
        .first()
    )
    if not job:
        return {
            "ok": False,
            "error": f"Publish job {job_id} not found",
            "error_type": "NOT_FOUND",
        }

    clip = db.query(Clip).filter_by(id=job.entity_id).first()
    if not clip:
        return {
            "ok": False,
            "error": f"Target clip {job.entity_id} not found",
            "error_type": "NOT_FOUND",
        }

    # Verify clip has not already been successfully published
    if clip.status == ClipStatus.PUBLISHED or getattr(clip, "youtube_video_id", None):
        return {
            "ok": False,
            "error": f"Clip {clip.id} has already been published",
            "error_type": "ALREADY_PUBLISHED",
        }

    p_log = db.query(PublishLog).filter(PublishLog.clip_id == clip.id, PublishLog.status == "SUCCESS").first()
    if p_log:
        return {
            "ok": False,
            "error": f"Clip {clip.id} already has a successful PublishLog ({p_log.youtube_video_id})",
            "error_type": "ALREADY_PUBLISHED",
        }

    if job.status == PipelineJobStatus.RUNNING:
        return {
            "ok": False,
            "error": f"Job {job_id} is currently RUNNING; cannot recover in-flight job",
            "error_type": "CONFLICT",
        }

    # Parse and preserve existing payload (including upload_session_uri)
    try:
        payload = json.loads(job.payload_json or "{}")
    except Exception:
        payload = {}

    # Validate or re-allocate PublishSlot
    slot = None
    slot_id = payload.get("slot_id")
    if slot_id:
        slot = db.query(PublishSlot).filter_by(id=slot_id).first()

    now = datetime.utcnow()
    slot_is_valid = (
        slot is not None
        and slot.status == PublishSlotStatus.RESERVED.value
        and slot.slot_start > now
    )

    if not slot_is_valid:
        # Prior slot expired or was cancelled: allocate a new slot
        cand = clip.candidate
        channel = (
            cand.source_video.channel
            if (cand and cand.source_video and cand.source_video.channel)
            else db.query(Channel).first()
        )
        if not channel:
            return {
                "ok": False,
                "error": "No YouTube channel configured for slot allocation.",
                "error_type": "NO_CHANNEL",
            }

        slot = publish_slot_service.allocate_slot(
            channel_id=channel.id,
            clip_id=clip.id,
            db=db,
            commit=False,
        )
        payload["slot_id"] = slot.id
        payload["scheduled_at"] = slot.slot_start.isoformat()

    slot.status = PublishSlotStatus.RESERVED.value
    clip.status = ClipStatus.APPROVED
    clip.scheduled_publish_at = slot.slot_start

    # Record prior attempt history in AuditLog
    prior_status = job.status.value if hasattr(job.status, "value") else str(job.status)
    audit = AuditLog(
        actor=actor,
        event="job.recovered",
        channel_id=slot.channel_id if slot else None,
        job_id=job.id,
        details=json.dumps({
            "reason": reason,
            "previous_status": prior_status,
            "previous_attempt_count": job.attempt_count,
            "slot_id": slot.id,
            "has_upload_session": bool(payload.get("upload_session_uri")),
        }),
    )
    db.add(audit)

    # Reset job fields to PENDING with attempt_count = 0
    job.attempt_count = 0
    job.status = PipelineJobStatus.PENDING
    job.next_run_at = slot.slot_start
    job.error_message = None
    job.completed_at = None
    job.locked_by = None
    job.locked_at = None
    job.payload_json = json.dumps(payload)

    db.commit()
    logger.info("Recovered publish job %s for clip %s (slot %s, scheduled %s)", job.id, clip.id, slot.id, slot.slot_start)

    return {
        "ok": True,
        "job_id": job.id,
        "clip_id": clip.id,
        "slot_id": slot.id,
        "slot_start": slot.slot_start.isoformat(),
        "status": "PENDING",
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
