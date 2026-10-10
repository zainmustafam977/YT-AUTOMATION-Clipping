"""
Dedicated Video Publishing Studio Routes (/publish and /api/publish/*).
Provides unified video upload studio, YouTube API quota telemetry, channel switcher,
AI title generator, and publishing outbox history.
"""

import json
import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from shorts_engine.config.settings import settings
from shorts_engine.dashboard.auth import require_operator, require_viewer
from shorts_engine.dashboard.routes.queue import get_db
from shorts_engine.db.models import (
    Clip, ClipStatus, ClipCandidate, SourceVideo, Channel, User, PublishLog,
    PublishJob
)
from shorts_engine.services.publishing.client import YouTubePublisher
from shorts_engine.services.publishing.metadata import ShortsMetadata
from shorts_engine.services.publishing.quota import QuotaTracker
from shorts_engine.services.publishing.oauth_manager import ChannelOAuthManager
from shorts_engine.services.publishing.orchestrator import PublishOrchestrator
from shorts_engine.services.execution.watchdog import SubprocessWatchdog

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
oauth_manager = ChannelOAuthManager()


class PublishExecutePayload(BaseModel):
    clip_id: str
    channel_id: Optional[str] = None
    publish_mode: str = "instant"  # instant or schedule
    scheduled_at: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[Union[str, List[str]]] = None
    category_id: str = "22"
    privacy_status: str = "unlisted"  # unlisted, public, private
    made_for_kids: bool = False
    dry_run: Optional[bool] = None


@router.get("/publish", summary="Dedicated Publishing Studio Hub (Temporary 307 Redirect)")
def publish_page(
    request: Request,
    channel_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    """307 Temporary Redirect to the docked Publishing Studio inside Review Queue."""
    q_str = str(request.url.query)
    target = f"/queue?{q_str}" if q_str else "/queue?studio=open"
    return RedirectResponse(url=target, status_code=307)


@router.get("/app/publish", summary="React SPA Publishing Studio (Temporary 307 Redirect)")
def app_publish_redirect(request: Request):
    """307 Temporary Redirect to the React Review Queue."""
    q_str = str(request.url.query)
    target = f"/app/queue?{q_str}" if q_str else "/app/queue?studio=open"
    return RedirectResponse(url=target, status_code=307)


@router.get("/api/publish/overview", summary="Publishing Hub telemetry and assets overview")
def get_publish_overview(
    channel_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    """JSON API endpoint returning channels, clips, quota telemetry, and publishing outbox."""
    return _get_publishing_overview_data(db=db, selected_channel_id=channel_id)


def _get_publishing_overview_data(db: Session, selected_channel_id: Optional[str] = None) -> Dict[str, Any]:
    channels_db = db.query(Channel).order_by(Channel.name.asc()).all()
    channel_list = []
    selected_ch = None

    for ch in channels_db:
        has_token = bool(ch.encrypted_oauth_token)
        is_auth = False
        if has_token:
            try:
                from shorts_engine.db.security import decrypt_secret
                dec = decrypt_secret(ch.encrypted_oauth_token)
                tok = json.loads(dec)
                is_auth = bool(tok.get("refresh_token"))
            except Exception:
                is_auth = False

        ch_data = {
            "id": ch.id,
            "name": ch.name,
            "youtube_channel_id": ch.youtube_channel_id,
            "is_authorized": is_auth,
            "status_badge": "Authorized 🟢" if is_auth else "Needs Auth 🔴",
            "has_raw_secret": has_token and not is_auth,
            "requires_auth": not is_auth,
        }
        channel_list.append(ch_data)
        if selected_channel_id and (ch.id == selected_channel_id or ch.youtube_channel_id == selected_channel_id):
            selected_ch = ch_data

    if not selected_ch and channel_list:
        # Default to Debateks or first authorized channel or first channel
        for c in channel_list:
            if c["youtube_channel_id"] == "UCBWVd_s5Gb6silTdeYaMqvA":
                selected_ch = c
                break
        if not selected_ch:
            selected_ch = channel_list[0]

    # Quota telemetry
    tracker = QuotaTracker()
    quota_metrics = tracker.get_usage_metrics(db=db)
    remaining_units = quota_metrics.get("remaining_units", 10000)
    cost_per_upload = 1600
    remaining_uploads = max(0, remaining_units // cost_per_upload)
    quota_data = {
        **quota_metrics,
        "cost_per_upload": cost_per_upload,
        "remaining_uploads": remaining_uploads,
    }

    # Clips ready for publishing: APPROVED, WAITING_APPROVAL, or clips with valid rendered_path
    clips_q = (
        db.query(Clip)
        .outerjoin(ClipCandidate, Clip.clip_candidate_id == ClipCandidate.id)
        .outerjoin(SourceVideo, ClipCandidate.source_video_id == SourceVideo.id)
        .filter(Clip.status.in_([ClipStatus.APPROVED, ClipStatus.WAITING_APPROVAL, ClipStatus.FAILED]))
        .order_by(Clip.created_at.desc())
        .limit(50)
        .all()
    )

    clips_list = []
    for c in clips_q:
        cand = c.candidate
        sv = cand.source_video if cand else None
        ch = sv.channel if sv else None
        start_sec = (cand.start_ms or 0) / 1000.0 if cand else 0.0
        end_sec = (cand.end_ms or 0) / 1000.0 if cand else 0.0
        dur_sec = round(end_sec - start_sec, 1) if (cand and cand.end_ms) else 0.0

        file_exists = bool(c.rendered_path and Path(c.rendered_path).exists() and Path(c.rendered_path).stat().st_size > 0)
        clips_list.append({
            "id": c.id,
            "status": c.status.value,
            "title": c.generated_title or (cand.hook_text if cand else f"Clip {c.id[:8]}"),
            "hook_text": cand.hook_text if cand else "",
            "payoff_text": cand.payoff_text if cand else "",
            "description": c.generated_description or "",
            "tags": json.loads(c.generated_tags) if c.generated_tags else ["#Shorts", "#Viral"],
            "duration_sec": dur_sec,
            "score": cand.score if cand else None,
            "rendered_path": c.rendered_path,
            "file_exists": file_exists,
            "channel_id": ch.id if ch else None,
            "channel_name": ch.name if ch else "Default Channel",
            "source_title": sv.title if sv else "Direct Clip",
            "video_url": f"/api/video/{c.id}",
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "scheduled_publish_at": c.scheduled_publish_at.isoformat() if c.scheduled_publish_at else None,
        })

    # Publishing History
    logs = db.query(PublishLog).order_by(PublishLog.published_at.desc()).limit(30).all()
    history_list = []
    for log in logs:
        clip = log.clip
        cand = clip.candidate if clip else None
        sv = cand.source_video if cand else None
        ch = sv.channel if sv else None

        yt_id = log.youtube_video_id
        watch_url = f"https://youtu.be/{yt_id}" if yt_id and not yt_id.startswith("dry_run_") else None
        history_list.append({
            "id": log.id,
            "clip_id": log.clip_id,
            "title": log.title,
            "youtube_video_id": yt_id,
            "watch_url": watch_url,
            "status": log.status,
            "error_message": log.error_message,
            "published_at": log.published_at.isoformat() if log.published_at else None,
            "channel_name": ch.name if ch else "Unknown Channel",
            "is_dry_run": yt_id.startswith("dry_run_") if yt_id else False,
        })

    # Scheduled queue
    scheduled_clips = (
        db.query(Clip)
        .filter(Clip.scheduled_publish_at.isnot(None), Clip.status != ClipStatus.PUBLISHED)
        .order_by(Clip.scheduled_publish_at.asc())
        .limit(20)
        .all()
    )
    schedule_list = [
        {
            "id": sc.id,
            "title": sc.generated_title or f"Clip {sc.id[:8]}",
            "scheduled_at": sc.scheduled_publish_at.isoformat() if sc.scheduled_publish_at else None,
            "status": sc.status.value,
        }
        for sc in scheduled_clips
    ]

    return {
        "channels": channel_list,
        "selected_channel": selected_ch,
        "clips": clips_list,
        "quota": quota_data,
        "history": history_list,
        "schedule": schedule_list,
    }


@router.post("/api/publish/direct-upload", summary="Upload external video file for publishing")
async def direct_upload_video(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    channel_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    """
    Accepts an external video file (.mp4, .mov, .mkv), stores it in storage/renders,
    and registers an APPROVED clip record ready for publishing.
    """
    filename = file.filename or "uploaded_video.mp4"
    ext = Path(filename).suffix.lower()
    if ext not in (".mp4", ".mov", ".mkv", ".webm"):
        raise HTTPException(status_code=400, detail="Only video files (.mp4, .mov, .mkv, .webm) are allowed")

    renders_dir = settings.STORAGE_DIR / "renders"
    renders_dir.mkdir(parents=True, exist_ok=True)
    clean_stem = "".join(c for c in Path(filename).stem if c.isalnum() or c in ("-", "_"))[:30]
    out_filename = f"upload_{int(time.time())}_{clean_stem}{ext}"
    target_path = renders_dir / out_filename

    try:
        with open(target_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        logger.exception("Failed to write uploaded video file: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to save video file: {e}")

    # Probe duration using ffprobe
    duration_sec = 30.0
    try:
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(target_path),
        ]
        res = SubprocessWatchdog.run(probe_cmd, timeout_sec=15.0, check=False)
        if res.returncode == 0 and res.stdout.strip():
            duration_sec = float(res.stdout.strip())
    except Exception as e:
        logger.warning("Could not probe uploaded video duration: %s", e)

    # Resolve target channel
    channel = None
    if channel_id:
        channel = db.query(Channel).filter_by(id=channel_id).first()
    if not channel:
        channel = db.query(Channel).filter_by(youtube_channel_id="UCBWVd_s5Gb6silTdeYaMqvA").first() or db.query(Channel).first()

    # Create dummy source video if needed
    source_vid = SourceVideo(
        youtube_video_id=f"direct_{int(time.time())}",
        title=title or Path(filename).stem.replace("_", " ").title(),
        channel_id=channel.id if channel else None,
        duration_sec=duration_sec,
    )
    db.add(source_vid)
    db.flush()

    # Create Candidate
    candidate = ClipCandidate(
        source_video_id=source_vid.id,
        start_ms=0,
        end_ms=int(duration_sec * 1000),
        hook_text=title or Path(filename).stem.replace("_", " ").title(),
        payoff_text="Watch until the end!",
        score=9.0,
        reason="Direct operator upload",
    )
    db.add(candidate)
    db.flush()

    # Create Clip
    clip = Clip(
        clip_candidate_id=candidate.id,
        status=ClipStatus.APPROVED,
        rendered_path=str(target_path),
        generated_title=(title or Path(filename).stem.replace("_", " ").title())[:100],
        generated_description="Check out this Short! #Shorts #Viral",
        generated_tags=json.dumps(["#Shorts", "#Viral", "#DirectUpload"]),
    )
    db.add(clip)
    db.commit()
    db.refresh(clip)

    logger.info("Directly ingested external video %s as clip %s", filename, clip.id)
    return {
        "status": "success",
        "clip_id": clip.id,
        "title": clip.generated_title,
        "duration_sec": round(duration_sec, 1),
        "rendered_path": str(target_path),
        "channel_name": channel.name if channel else "Default Channel",
    }


@router.post("/api/publish/execute", summary="Execute instant or scheduled YouTube publish")
def execute_publish(
    payload: PublishExecutePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    """Executes live YouTube Shorts publish or schedules release time with custom SEO metadata."""
    clip = db.query(Clip).filter_by(id=payload.clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    # Update metadata
    if payload.title:
        clip.generated_title = payload.title.strip()[:100]
    if payload.description:
        clip.generated_description = payload.description.strip()[:5000]

    tags_list = []
    if isinstance(payload.tags, str):
        tags_list = [t.strip() for t in payload.tags.split(",") if t.strip()]
    elif isinstance(payload.tags, list):
        tags_list = payload.tags
    if tags_list:
        clip.generated_tags = json.dumps(tags_list)
    else:
        tags_list = ["#Shorts", "#Viral"]

    # Rebind channel if requested
    if payload.channel_id and clip.candidate and clip.candidate.source_video:
        target_ch = db.query(Channel).filter_by(id=payload.channel_id).first()
        if not target_ch:
            target_ch = db.query(Channel).filter_by(youtube_channel_id=payload.channel_id).first()
        if target_ch:
            clip.candidate.source_video.channel_id = target_ch.id
            db.commit()

    # Schedule Mode
    if payload.publish_mode == "schedule":
        if not payload.scheduled_at:
            raise HTTPException(status_code=422, detail="scheduled_at datetime is required for schedule mode")
        try:
            dt = datetime.fromisoformat(payload.scheduled_at.replace("Z", "+00:00"))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid ISO datetime: {e}")

        clip.scheduled_publish_at = dt
        clip.status = ClipStatus.APPROVED
        db.commit()
        logger.info("Operator '%s' scheduled clip %s for %s", current_user.username, clip.id, dt.isoformat())
        return {
            "status": "scheduled",
            "clip_id": clip.id,
            "scheduled_publish_at": dt.isoformat(),
            "title": clip.generated_title,
        }

    # Instant Publish Mode
    meta = ShortsMetadata(
        title=clip.generated_title or "Viral YouTube Short #Shorts",
        description=clip.generated_description or "Check out this Short! #Shorts",
        tags=tags_list,
        privacy_status=payload.privacy_status,
        category_id=payload.category_id,
        made_for_kids=payload.made_for_kids,
    )

    publisher = YouTubePublisher(db_session=db)
    try:
        result = publisher.publish_clip(
            clip=clip,
            metadata=meta,
            dry_run=payload.dry_run,
        )
        if result.get("success"):
            vid = result.get("youtube_video_id")
            watch_url = f"https://youtu.be/{vid}" if vid and not vid.startswith("dry_run_") else None
            return {
                "status": "published",
                "clip_id": clip.id,
                "youtube_video_id": vid,
                "watch_url": watch_url,
                "is_dry_run": result.get("dry_run", False),
            }
        else:
            return {
                "status": "error",
                "message": result.get("reason", "Publishing failed"),
                "result": result,
            }
    except Exception as e:
        logger.exception("Publishing execution failed for clip %s: %s", clip.id, e)
        return {
            "status": "error",
            "message": str(e),
        }


class PublishDraftPayload(BaseModel):
    draft_version: Optional[int] = None
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[Union[str, List[str]]] = None
    privacy_status: Optional[str] = None
    category_id: Optional[str] = None
    made_for_kids: Optional[bool] = None
    license: Optional[str] = None
    embeddable: Optional[bool] = None


@router.get("/api/clips/{clip_id}/publish-draft", summary="Get clip draft publishing metadata")
def get_publish_draft(
    clip_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    tags_val = None
    if clip.draft_tags:
        try:
            tags_val = json.loads(clip.draft_tags)
        except Exception:
            tags_val = clip.draft_tags
    elif clip.generated_tags:
        try:
            tags_val = json.loads(clip.generated_tags)
        except Exception:
            tags_val = clip.generated_tags

    return {
        "clip_id": clip.id,
        "title": clip.draft_title or clip.generated_title or "",
        "description": clip.draft_description or clip.generated_description or "",
        "tags": tags_val or [],
        "privacy_status": clip.draft_privacy_status or "unlisted",
        "category_id": clip.draft_category_id or "22",
        "made_for_kids": clip.draft_made_for_kids,
        "draft_version": clip.draft_version or 1,
        "target_channel_id": clip.draft_target_channel_id,
        "scheduled_at": clip.draft_scheduled_at.isoformat() if clip.draft_scheduled_at else None,
    }


def validate_publish_metadata(
    title: str,
    description: str,
    tags: Optional[Union[str, List[str]]],
    made_for_kids: Optional[bool],
):
    """
    Validates publishing metadata against YouTube platform limits:
    - Title <= 100 characters, no angle brackets (< >)
    - Description <= 5,000 bytes UTF-8, no angle brackets (< >)
    - Tags <= 500 characters total
    - Made-for-kids declaration required
    """
    if not title or not title.strip():
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    if len(title) > 100:
        raise HTTPException(status_code=400, detail=f"Title exceeds 100 characters limit ({len(title)}/100)")
    if "<" in title or ">" in title:
        raise HTTPException(status_code=400, detail="Angle brackets (< and >) are disallowed in title")

    if description:
        desc_bytes = len(description.encode("utf-8"))
        if desc_bytes > 5000:
            raise HTTPException(status_code=400, detail=f"Description exceeds 5,000 UTF-8 bytes limit ({desc_bytes}/5000)")
        if "<" in description or ">" in description:
            raise HTTPException(status_code=400, detail="Angle brackets (< and >) are disallowed in description")

    if tags:
        if isinstance(tags, str):
            tag_str = tags
        else:
            tag_str = ",".join(tags)
        if len(tag_str) > 500:
            raise HTTPException(status_code=400, detail=f"Tags total character length exceeds 500 characters ({len(tag_str)}/500)")

    if made_for_kids is None:
        raise HTTPException(status_code=400, detail="Made-for-kids declaration is required before publishing")


@router.patch("/api/clips/{clip_id}/publish-draft", summary="Update clip draft publishing metadata with optimistic concurrency")
def update_publish_draft(
    clip_id: str,
    payload: PublishDraftPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    if payload.draft_version is not None and clip.draft_version is not None:
        if clip.draft_version != payload.draft_version:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Draft version conflict: the draft has been modified by another process.",
                    "current_version": clip.draft_version,
                },
            )

    if payload.title is not None:
        clean_title = payload.title.strip()[:100]
        clip.draft_title = clean_title
        clip.generated_title = clean_title
    if payload.description is not None:
        clean_desc = payload.description.strip()[:5000]
        clip.draft_description = clean_desc
        clip.generated_description = clean_desc
    if payload.tags is not None:
        if isinstance(payload.tags, str):
            tags_list = [t.strip() for t in payload.tags.split(",") if t.strip()]
        else:
            tags_list = payload.tags
        clip.draft_tags = json.dumps(tags_list)
        clip.generated_tags = json.dumps(tags_list)
    if payload.privacy_status is not None:
        clip.draft_privacy_status = payload.privacy_status
    if payload.category_id is not None:
        clip.draft_category_id = payload.category_id
    if payload.made_for_kids is not None:
        clip.draft_made_for_kids = payload.made_for_kids

    clip.draft_version = (clip.draft_version or 0) + 1
    db.commit()
    db.refresh(clip)

    return {
        "status": "saved",
        "clip_id": clip.id,
        "draft_version": clip.draft_version,
        "title": clip.generated_title,
    }


class ScheduleClipPayload(BaseModel):
    scheduled_at: str
    channel_id: Optional[str] = None
    schedule_mode: str = "native"  # native or local


@router.post("/api/clips/{clip_id}/schedule", summary="Schedule clip publication with daily cap validation")
def schedule_clip_publish(
    clip_id: str,
    payload: ScheduleClipPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    try:
        dt = datetime.fromisoformat(payload.scheduled_at.replace("Z", "+00:00"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid ISO 8601 datetime: {e}")

    now_utc = datetime.now(timezone.utc)
    target_dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if target_dt <= now_utc:
        raise HTTPException(status_code=400, detail="scheduled_at must be in the future")

    tracker = QuotaTracker()
    can_pub, remaining, reason = tracker.can_publish(db=db)
    if not can_pub and "cap reached" in reason:
        raise HTTPException(status_code=429, detail=reason)

    channel = None
    if payload.channel_id:
        channel = db.query(Channel).filter_by(id=payload.channel_id).first()
        if not channel:
            channel = db.query(Channel).filter_by(youtube_channel_id=payload.channel_id).first()
    if not channel and clip.candidate and clip.candidate.source_video:
        channel = clip.candidate.source_video.channel
    if not channel:
        channel = db.query(Channel).filter_by(youtube_channel_id="UCBWVd_s5Gb6silTdeYaMqvA").first() or db.query(Channel).first()

    eff_title = clip.draft_title or clip.generated_title or f"Short {clip.id[:8]}"
    eff_desc = clip.draft_description or clip.generated_description or ""
    eff_tags = json.loads(clip.draft_tags) if clip.draft_tags else ["#Shorts", "#Viral"]
    validate_publish_metadata(
        title=eff_title,
        description=eff_desc,
        tags=eff_tags,
        made_for_kids=clip.draft_made_for_kids,
    )

    quota_tracker = QuotaTracker()
    allowed, rem, reason = quota_tracker.can_schedule_upload(target_datetime=dt, db=db)
    if not allowed:
        raise HTTPException(status_code=429, detail=f"Publishing cap exceeded: {reason}")

    orchestrator = PublishOrchestrator()
    job = orchestrator.create_or_update_job(
        clip=clip,
        channel=channel,
        title=eff_title,
        description=eff_desc,
        tags=eff_tags,
        category_id=clip.draft_category_id or "22",
        privacy_status=clip.draft_privacy_status or "unlisted",
        made_for_kids=clip.draft_made_for_kids if clip.draft_made_for_kids is not None else False,
        schedule_mode=payload.schedule_mode,
        scheduled_at=dt,
        db=db,
    )

    clip.scheduled_publish_at = dt
    clip.status = ClipStatus.APPROVED
    db.commit()

    return {
        "status": "scheduled",
        "job_id": job.id,
        "clip_id": clip.id,
        "scheduled_publish_at": dt.isoformat(),
        "channel_name": channel.name if channel else "Default Channel",
    }


class InstantPublishPayload(BaseModel):
    channel_id: Optional[str] = None
    dry_run: Optional[bool] = None
    privacy_status: Optional[str] = None


@router.post("/api/clips/{clip_id}/publish", summary="Trigger PublishOrchestrator to publish clip")
def publish_clip_endpoint(
    clip_id: str,
    payload: InstantPublishPayload = InstantPublishPayload(),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    channel = None
    if payload.channel_id:
        channel = db.query(Channel).filter_by(id=payload.channel_id).first()
        if not channel:
            channel = db.query(Channel).filter_by(youtube_channel_id=payload.channel_id).first()
    if not channel and clip.candidate and clip.candidate.source_video:
        channel = clip.candidate.source_video.channel
    if not channel:
        channel = db.query(Channel).filter_by(youtube_channel_id="UCBWVd_s5Gb6silTdeYaMqvA").first() or db.query(Channel).first()

    eff_title = clip.draft_title or clip.generated_title or f"Short {clip.id[:8]}"
    eff_desc = clip.draft_description or clip.generated_description or ""
    eff_tags = json.loads(clip.draft_tags) if clip.draft_tags else ["#Shorts", "#Viral"]
    validate_publish_metadata(
        title=eff_title,
        description=eff_desc,
        tags=eff_tags,
        made_for_kids=clip.draft_made_for_kids,
    )

    orchestrator = PublishOrchestrator()
    job = orchestrator.create_or_update_job(
        clip=clip,
        channel=channel,
        title=eff_title,
        description=eff_desc,
        tags=eff_tags,
        category_id=clip.draft_category_id or "22",
        privacy_status=payload.privacy_status or clip.draft_privacy_status or "unlisted",
        made_for_kids=clip.draft_made_for_kids if clip.draft_made_for_kids is not None else False,
        schedule_mode="instant",
        scheduled_at=None,
        db=db,
    )

    res = orchestrator.execute_publish_job(job_id=job.id, db=db, dry_run=payload.dry_run)
    return res


@router.get("/api/publish/jobs", summary="List recent publishing jobs and outbox status")
def list_publish_jobs(
    limit: int = 50,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    q = db.query(PublishJob)
    if status:
        q = q.filter_by(status=status)
    jobs = q.order_by(PublishJob.created_at.desc()).limit(limit).all()
    results = []
    for j in jobs:
        results.append({
            "id": j.id,
            "clip_id": j.clip_id,
            "target_youtube_channel_id": j.target_youtube_channel_id,
            "title": j.title,
            "status": j.status,
            "privacy_status": j.privacy_status,
            "schedule_mode": j.schedule_mode,
            "scheduled_at": j.scheduled_at.isoformat() if j.scheduled_at else None,
            "attempt_count": j.attempts,
            "last_error": j.last_error,
            "youtube_video_id": j.youtube_video_id,
            "watch_url": f"https://youtu.be/{j.youtube_video_id}" if j.youtube_video_id and not j.youtube_video_id.startswith("dry_run_") else None,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "updated_at": j.updated_at.isoformat() if j.updated_at else None,
            "completed_at": None,
        })
    return results


@router.post("/api/publish/jobs/{job_id}/retry", summary="Retry a failed or paused publishing job")
def retry_publish_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    orchestrator = PublishOrchestrator()
    try:
        return orchestrator.retry_job(job_id=job_id, db=db)
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        logger.exception("Failed to retry publish job %s: %s", job_id, e)
        raise HTTPException(status_code=500, detail=str(e))


class CalibrateQuotaPayload(BaseModel):
    used_units_target: int


@router.get("/api/quota", summary="Get YouTube API quota telemetry metrics")
def get_quota_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    tracker = QuotaTracker()
    return tracker.get_usage_metrics(db=db)


@router.post("/api/quota/calibrate", summary="Calibrate quota tracker against Google Cloud Console value")
def calibrate_quota_endpoint(
    payload: CalibrateQuotaPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    tracker = QuotaTracker()
    return tracker.calibrate(used_units_target=payload.used_units_target, db=db)
