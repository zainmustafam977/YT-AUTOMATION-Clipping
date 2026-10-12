"""
Content Calendar Routes:
Provides a visual interactive calendar for viewing, scheduling, and drag-and-drop
rescheduling of YouTube Shorts.
"""

import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from shorts_engine.dashboard.auth import require_operator, require_viewer, verify_csrf
from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import Clip, ClipStatus, User, PublishLog

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/calendar", response_class=HTMLResponse, summary="Content publication calendar")
def calendar_view(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    """Renders the FullCalendar interactive content calendar."""
    scheduled_count = (
        db.query(Clip)
        .filter(Clip.status == ClipStatus.APPROVED, Clip.scheduled_publish_at.isnot(None))
        .count()
    )
    published_count = db.query(Clip).filter_by(status=ClipStatus.PUBLISHED).count()

    return templates.TemplateResponse(
        request=request,
        name="calendar.html",
        context={
            "current_user": current_user,
            "current_page": "calendar",
            "scheduled_count": scheduled_count,
            "published_count": published_count,
        },
    )



@router.get("/api/calendar/events", summary="Calendar events feed for FullCalendar")
def calendar_events_feed(
    start: Optional[str] = None,
    end: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    """
    Returns JSON event array for FullCalendar.
    Includes scheduled approved clips and published clips.
    """
    events: List[Dict[str, Any]] = []

    # 1. Scheduled clips
    sched_clips = (
        db.query(Clip)
        .filter(
            Clip.status.in_([ClipStatus.APPROVED, ClipStatus.WAITING_APPROVAL, ClipStatus.SCHEDULED]),
            Clip.scheduled_publish_at.isnot(None),
        )
        .all()
    )

    for c in sched_clips:
        cand = c.candidate
        sv = cand.source_video if cand else None
        ch = sv.channel if sv else None

        title = c.generated_title or (cand.hook_text if cand else "Scheduled Short")
        thumb_url = f"/api/thumbnails/{sv.youtube_video_id}" if (sv and sv.youtube_video_id) else "/static/img/placeholder.png"

        events.append({
            "id": c.id,
            "title": f"⏱️ {title[:40]}...",
            "start": c.scheduled_publish_at.isoformat(),
            "backgroundColor": "#0dcaf0",  # Cyan for scheduled
            "borderColor": "#0aa2c0",
            "textColor": "#000000",
            "extendedProps": {
                "clip_id": c.id,
                "full_title": title,
                "status": c.status.value,
                "hook_text": cand.hook_text if cand else "",
                "score": cand.score if cand else None,
                "channel_name": ch.name if ch else "Unknown",
                "thumbnail_url": thumb_url,
                "is_published": False,
                "video_id": sv.youtube_video_id if sv else "",
            },
        })

    # 2. Published clips
    pub_logs = (
        db.query(PublishLog)
        .join(Clip, PublishLog.clip_id == Clip.id)
        .order_by(PublishLog.published_at.desc())
        .limit(100)
        .all()
    )

    for p in pub_logs:
        c = p.clip
        cand = c.candidate if c else None
        sv = cand.source_video if cand else None
        ch = sv.channel if sv else None

        thumb_url = f"/api/thumbnails/{p.youtube_video_id}" if p.youtube_video_id else "/static/img/placeholder.png"

        events.append({
            "id": f"pub_{p.id}",
            "title": f"✓ {p.title[:40]}...",
            "start": p.published_at.isoformat(),
            "backgroundColor": "#198754",  # Green for published
            "borderColor": "#146c43",
            "textColor": "#ffffff",
            "editable": False,  # Published clips cannot be dragged
            "extendedProps": {
                "clip_id": p.clip_id,
                "full_title": p.title,
                "status": "PUBLISHED",
                "youtube_video_id": p.youtube_video_id,
                "channel_name": ch.name if ch else "YouTube",
                "thumbnail_url": thumb_url,
                "is_published": True,
            },
        })

    return JSONResponse(content=events)


@router.get("/api/schedule/slots", summary="Get upcoming publish slots schedule")
def upcoming_schedule_slots(
    channel_id: Optional[str] = None,
    platform: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    """Returns upcoming publish slots and their scheduled release timestamps."""
    from shorts_engine.services.publishing.slots import publish_slot_service
    slots = publish_slot_service.get_upcoming_schedule(
        db=db, channel_id=channel_id, platform=platform, limit=50,
    )
    return {"schedule": slots, "count": len(slots)}


@router.post("/api/calendar/reschedule/{clip_id}", summary="Drag-and-drop reschedule clip")
def calendar_reschedule_clip(
    clip_id: str,
    scheduled_at: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    """Updates the scheduled publication datetime for a clip via calendar interaction."""
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    try:
        # FullCalendar sends ISO format, e.g. 2026-09-21T15:00:00Z or without Z
        clean_str = scheduled_at.rstrip("Z").split("+")[0]
        dt = datetime.fromisoformat(clean_str)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {e}")

    from shorts_engine.services.publishing.slots import publish_slot_service
    res = publish_slot_service.reschedule_clip(clip_id=clip.id, new_slot_time=dt, db=db)

    logger.info("Operator '%s' rescheduled clip %s to %s via Calendar", current_user.username, clip_id, dt.isoformat())
    return {
        "status": "rescheduled",
        "clip_id": clip_id,
        "scheduled_publish_at": dt.isoformat(),
        "display": dt.strftime("%b %d, %Y at %H:%M"),
        "updated_on_youtube": res.get("updated_on_youtube", False),
    }
