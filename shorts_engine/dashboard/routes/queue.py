import csv
import io
import json
import time
import logging
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict
from collections import OrderedDict
from fastapi import APIRouter, Depends, HTTPException, Request, Form, UploadFile, File, Query
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import or_

from shorts_engine.config.settings import settings
from shorts_engine.dashboard.auth import require_operator, require_viewer, verify_csrf, get_csrf_token
from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import Clip, ClipStatus, ClipCandidate, SourceVideo, SourceVideoStatus, Channel, User, PublishJob
from shorts_engine.services.clip_selection.selector import parse_ai_provenance
from shorts_engine.services.analytics.predictor import viral_predictor
from shorts_engine.services.captioning.templates import list_caption_templates
from shorts_engine.services.publishing.quota import QuotaTracker
from shorts_engine.services.publishing.actions import (
    approve_clip_for_publishing,
    reject_clip_action,
    bulk_approve_clips,
    bulk_reject_clips,
)

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _make_queue_redirect(
    request: Request,
    default_msg: Optional[str] = None,
    msg_type: str = "success",
    return_query: Optional[str] = None,
    extra_params: Optional[Dict[str, str]] = None,
) -> RedirectResponse:
    query_dict: Dict[str, str] = {}
    if return_query:
        query_dict = dict(urllib.parse.parse_qsl(return_query.lstrip("?")))
    elif request.query_params:
        query_dict = dict(request.query_params)
        query_dict.pop("msg", None)
        query_dict.pop("type", None)

    if default_msg:
        query_dict["msg"] = default_msg
        query_dict["type"] = msg_type
    if extra_params:
        query_dict.update(extra_params)

    qs = urllib.parse.urlencode(query_dict)
    target = f"/queue?{qs}" if qs else "/queue"
    return RedirectResponse(url=target, status_code=303)


@router.get("/queue", response_class=HTMLResponse, summary="Review queue")
def queue_list(
    request: Request,
    tab: str = "active",
    page: int = 1,
    limit: int = 20,
    sort_by: str = "date",
    order: str = "desc",
    q: Optional[str] = None,
    channel_id: Optional[str] = None,
    source_video_id: Optional[str] = None,
    ai_provider: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    try:
        active_count = int(db.query(Clip).filter_by(status=ClipStatus.WAITING_APPROVAL).count())
    except Exception:
        active_count = 0
    try:
        rejected_count = int(db.query(Clip).filter_by(status=ClipStatus.REJECTED).count())
    except Exception:
        rejected_count = 0
    try:
        needs_review_count = int(db.query(Clip).filter_by(status=ClipStatus.FAILED_NEEDS_REVIEW).count())
    except Exception:
        needs_review_count = 0

    # Global oldest-item age across all WAITING_APPROVAL clips (independent of active filters)
    oldest_age_display = "—"
    try:
        oldest_waiting_clip = (
            db.query(Clip)
            .filter_by(status=ClipStatus.WAITING_APPROVAL)
            .order_by(
                Clip.queued_at.asc().nullslast() if hasattr(Clip, "queued_at") else Clip.created_at.asc(),
                Clip.created_at.asc(),
            )
            .first()
        )
        if oldest_waiting_clip:
            queued_time = getattr(oldest_waiting_clip, "queued_at", None) or oldest_waiting_clip.created_at
            if queued_time:
                delta = datetime.utcnow() - queued_time
                total_secs = max(0, int(delta.total_seconds()))
                days = total_secs // 86400
                hours = (total_secs % 86400) // 3600
                minutes = (total_secs % 3600) // 60
                if days > 0:
                    oldest_age_display = f"{days}d {hours}h"
                elif hours > 0:
                    oldest_age_display = f"{hours}h {minutes}m"
                else:
                    oldest_age_display = f"{max(1, minutes)}m"
    except Exception as age_err:
        logger.warning("Could not calculate oldest waiting clip age: %s", age_err)

    # Distinct source videos for filter dropdown
    try:
        source_videos = (
            db.query(SourceVideo)
            .join(ClipCandidate, ClipCandidate.source_video_id == SourceVideo.id)
            .join(Clip, Clip.clip_candidate_id == ClipCandidate.id)
            .filter(Clip.status == ClipStatus.WAITING_APPROVAL)
            .distinct()
            .order_by(SourceVideo.title.asc())
            .all()
        )
        if not source_videos:
            source_videos = db.query(SourceVideo).order_by(SourceVideo.title.asc()).limit(50).all()
    except Exception:
        source_videos = []

    if tab == "rejected":
        current_tab = "rejected"
        target_status = ClipStatus.REJECTED
    elif tab == "needs_review":
        current_tab = "needs_review"
        target_status = ClipStatus.FAILED_NEEDS_REVIEW
    else:
        current_tab = "active"
        target_status = ClipStatus.WAITING_APPROVAL


    # Base query joining candidates and source videos
    base_q = (
        db.query(Clip)
        .outerjoin(ClipCandidate, Clip.clip_candidate_id == ClipCandidate.id)
        .outerjoin(SourceVideo, ClipCandidate.source_video_id == SourceVideo.id)
        .filter(Clip.status == target_status)
    )

    # Keyword text search across hook, payoff, and video title/ID
    if q and q.strip():
        search_term = f"%{q.strip()}%"
        base_q = base_q.filter(
            or_(
                ClipCandidate.hook_text.ilike(search_term),
                ClipCandidate.payoff_text.ilike(search_term),
                SourceVideo.title.ilike(search_term),
                SourceVideo.youtube_video_id.ilike(search_term),
            )
        )

    # Channel filter
    if channel_id and channel_id.strip():
        base_q = base_q.filter(SourceVideo.channel_id == channel_id.strip())

    # Source video filter
    if source_video_id and source_video_id.strip():
        sv_id = source_video_id.strip()
        base_q = base_q.filter(
            or_(SourceVideo.id == sv_id, SourceVideo.youtube_video_id == sv_id)
        )

    # AI Model / Provider filter
    if ai_provider and ai_provider.strip():
        ap = ai_provider.strip().lower()
        if ap == "failover":
            base_q = base_q.filter(ClipCandidate.reason.ilike("%:failover]%"))
        elif ap == "heuristic":
            base_q = base_q.filter(
                or_(
                    ClipCandidate.reason.ilike("[%heuristic%"),
                    ClipCandidate.reason.is_(None),
                    ~ClipCandidate.reason.ilike("[%:%"),
                )
            )
        else:
            base_q = base_q.filter(ClipCandidate.reason.ilike(f"[%{ap}:%"))

    # Sorting
    sort_by_clean = (sort_by or "date").lower()
    order_clean = (order or "desc").lower()
    if sort_by_clean == "score":
        base_q = base_q.order_by(ClipCandidate.score.asc() if order_clean == "asc" else ClipCandidate.score.desc())
    elif sort_by_clean == "duration":
        dur_expr = ClipCandidate.end_ms - ClipCandidate.start_ms
        base_q = base_q.order_by(dur_expr.asc() if order_clean == "asc" else dur_expr.desc())
    else:  # default "date"
        base_q = base_q.order_by(Clip.created_at.asc() if order_clean == "asc" else Clip.created_at.desc())

    # Pagination calculation
    try:
        total_items = int(base_q.count())
    except Exception:
        total_items = 0
    limit = max(1, min(limit, 100))
    total_pages = max(1, (total_items + limit - 1) // limit)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * limit

    try:
        raw_clips = base_q.offset(offset).limit(limit).all()
        clips = raw_clips if isinstance(raw_clips, (list, tuple)) else []
    except Exception:
        clips = []

    has_prev = page > 1
    has_next = page < total_pages
    prev_page = page - 1
    next_page = page + 1
    start_item = (page - 1) * limit + 1 if total_items > 0 else 0
    end_item = min(page * limit, total_items)

    try:
        raw_channels = db.query(Channel).order_by(Channel.name.asc()).all()
        channels = raw_channels if isinstance(raw_channels, (list, tuple)) else []
    except Exception:
        channels = []

    for clip in clips:
        cand = clip.candidate
        prov = parse_ai_provenance(cand.reason if cand else None)
        clip.ai_provider = prov["provider"]
        clip.ai_model = prov["model"]
        clip.ai_badge = prov["badge"]
        clip.clean_reason = prov["clean_reason"]
        clip.is_heuristic = prov["is_heuristic"]
        clip.latency_ms = prov.get("latency_ms")
        clip.is_failover = prov.get("is_failover", False)

        if cand:
            start_sec = (cand.start_ms or 0) / 1000.0
            end_sec = (cand.end_ms or 0) / 1000.0
            dur_sec = round(end_sec - start_sec, 1)
            start_fmt = f"{int(start_sec // 60):02d}:{int(start_sec % 60):02d}"
            end_fmt = f"{int(end_sec // 60):02d}:{int(end_sec % 60):02d}"
            clip.interval_display = f"{start_fmt} – {end_fmt} ({dur_sec}s)"
            clip.hook_preview = cand.hook_text
            clip.virality_score = round(cand.score, 1) if cand.score is not None else None
            clip.ml_virality_score = viral_predictor.predict_virality(
                hook_text=cand.hook_text or "",
                duration_sec=dur_sec,
                payoff_text=cand.payoff_text,
            )
            clip.source_title = cand.source_video.title if cand.source_video else "Unknown Video"
            clip.source_vid = cand.source_video.youtube_video_id if cand.source_video else None
            clip.channel_name = cand.source_video.channel.name if cand.source_video and cand.source_video.channel else None
            clip.thumbnail_url = f"https://img.youtube.com/vi/{cand.source_video.youtube_video_id}/mqdefault.jpg" if (cand.source_video and cand.source_video.youtube_video_id) else None
        else:
            clip.interval_display = "—"
            clip.hook_preview = None
            clip.virality_score = None
            clip.ml_virality_score = None
            clip.source_title = "Unknown Video"
            clip.source_vid = None
            clip.channel_name = None
            clip.thumbnail_url = None

        # Parse quality report
        qr = {}
        if clip.quality_check_result:
            try:
                qr = json.loads(clip.quality_check_result)
            except Exception:
                qr = {}
        clip.quality_report = qr

        # Explicit quality gate breakdown
        tech = qr.get("technical_check")
        if tech is None:
            clip.tech_gate = ("pending", "Technical Pending", "Check has not run yet")
        elif tech.get("all_passed") or tech.get("passed"):
            clip.tech_gate = ("pass", "Technical Pass", "Resolution, duration, and audio valid")
        else:
            err = tech.get("error")
            if not err and "checks" in tech:
                failed = [k.replace("_", " ") for k, v in tech["checks"].items() if not v]
                err = ", ".join(failed) if failed else "Check failed"
            clip.tech_gate = ("fail", "Technical Fail", err or "Failed technical check")

        framing = qr.get("render_inspection")
        if framing is None:
            clip.framing_gate = ("pending", "Framing Pending", "Check has not run yet")
        elif framing.get("all_passed") or framing.get("passed"):
            clip.framing_gate = ("pass", "Framing Pass", "Subject centered and caption UI clear")
        else:
            err = framing.get("error")
            if not err:
                err = f"Violations: framing={framing.get('subject_framing_violations', 0)}, caption={framing.get('caption_collision_violations', 0)}"
            clip.framing_gate = ("fail", "Framing Fail", err)

        sem = qr.get("semantic_check")
        if sem is None:
            clip.semantic_gate = ("pending", "Semantic Pending", "Check has not run yet")
        elif sem.get("pass_decision") or sem.get("passed"):
            adv = sem.get("semantic_advisory")
            if adv:
                clip.semantic_gate = ("advisory", "Review Advisory", adv)
            else:
                clip.semantic_gate = ("pass", "Semantic Pass", "Autonomous with clear hook & payoff")
        else:
            err = sem.get("critique_reason") or sem.get("error") or "Failed autonomy or narrative check"
            clip.semantic_gate = ("fail", "Semantic Fail", err)

        clip.manual_rejection_reason = qr.get("rejection_reason")
        clip.advisories = qr.get("advisories", [])
        clip.has_advisories = len(clip.advisories) > 0
        clip.variations_list = clip.variations or generate_deterministic_variations(clip.hook_preview, clip.source_title)

    # Group clips automatically by source video and date
    groups_dict = OrderedDict()
    for clip in clips:
        cand = clip.candidate
        sv = cand.source_video if (cand and cand.source_video) else None
        sv_id = sv.id if sv else "unknown"
        date_obj = (sv.created_at if sv and sv.created_at else clip.created_at) or datetime.utcnow()
        date_str = date_obj.strftime("%Y-%m-%d")
        group_key = f"{sv_id}_{date_str}"

        if group_key not in groups_dict:
            groups_dict[group_key] = {
                "group_id": group_key,
                "source_video_id": sv_id,
                "youtube_video_id": sv.youtube_video_id if sv else None,
                "source_title": sv.title if sv else "Unknown Source Video",
                "channel_name": (sv.channel.name if sv and sv.channel else "Default Channel"),
                "channel_id": sv.channel_id if sv else None,
                "date_formatted": date_obj.strftime("%b %d, %Y"),
                "thumbnail_url": f"/api/thumbnails/{sv.youtube_video_id}" if (sv and sv.youtube_video_id) else None,
                "ai_badge": clip.ai_badge,
                "ai_provider": clip.ai_provider,
                "ai_model": clip.ai_model,
                "clips": [],
                "scores": [],
            }
        groups_dict[group_key]["clips"].append(clip)
        if clip.virality_score is not None:
            groups_dict[group_key]["scores"].append(clip.virality_score)

    for g in groups_dict.values():
        g["clips_count"] = len(g["clips"])
        g["avg_score"] = round(sum(g["scores"]) / len(g["scores"]), 1) if g["scores"] else 0.0

    grouped_sources = list(groups_dict.values())

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

    selected_ch = None
    if channel_id:
        selected_ch = next((c for c in channels if getattr(c, "id", None) == channel_id or getattr(c, "youtube_channel_id", None) == channel_id), None)
    if not selected_ch and channels:
        selected_ch = next((c for c in channels if getattr(c, "youtube_channel_id", None) == "UCBWVd_s5Gb6silTdeYaMqvA" or getattr(c, "name", None) == "Debateks"), channels[0])

    selected_clip_id = request.query_params.get("clip")
    studio_open = request.query_params.get("studio") == "open" or bool(selected_clip_id)

    try:
        recent_jobs = db.query(PublishJob).order_by(PublishJob.created_at.desc()).limit(20).all()
        if not isinstance(recent_jobs, (list, tuple)):
            recent_jobs = []
    except Exception:
        recent_jobs = []

    return templates.TemplateResponse(
        request=request,
        name="queue.html",
        context={
            "clips": clips,
            "grouped_sources": grouped_sources,
            "current_user": current_user,
            "csrf_token": get_csrf_token(request),
            "current_tab": current_tab,
            "active_count": active_count,
            "rejected_count": rejected_count,
            "needs_review_count": needs_review_count,
            "queue_depth": active_count,
            "oldest_item_age": oldest_age_display,
            "source_videos": source_videos,
            "page": page,
            "limit": limit,
            "sort_by": sort_by_clean,
            "order": order_clean,
            "q": q or "",
            "channel_id": channel_id or "",
            "source_video_id": source_video_id or "",
            "ai_provider": ai_provider or "",
            "channels": channels,
            "selected_channel": selected_ch,
            "quota": quota_data,
            "studio_open": studio_open,
            "selected_clip_id": selected_clip_id,
            "publish_jobs": recent_jobs,
            "caption_templates": list_caption_templates(db=db),
            "total_items": total_items,
            "total_pages": total_pages,
            "has_prev": has_prev,
            "has_next": has_next,
            "prev_page": prev_page,
            "next_page": next_page,
            "start_item": start_item,
            "end_item": end_item,
            "current_query": request.url.query,
            "rejected_id": request.query_params.get("rejected_id"),
            "msg": request.query_params.get("msg"),
            "msg_type": request.query_params.get("type", "success"),
        },
    )


@router.get("/queue/export", summary="Export approved clips metadata as CSV or JSON")
def export_queue_clips(
    format: str = "csv",
    status: str = "APPROVED",
    channel_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    """Downloads approved or filtered clips metadata for YouTube Shorts scheduling and audits."""
    format_clean = (format or "csv").lower()

    query = (
        db.query(Clip)
        .outerjoin(ClipCandidate, Clip.clip_candidate_id == ClipCandidate.id)
        .outerjoin(SourceVideo, ClipCandidate.source_video_id == SourceVideo.id)
    )

    if status.upper() != "ALL":
        try:
            target_status = ClipStatus[status.upper()]
            query = query.filter(Clip.status == target_status)
        except KeyError:
            query = query.filter(Clip.status == ClipStatus.APPROVED)

    if channel_id and channel_id.strip():
        query = query.filter(SourceVideo.channel_id == channel_id.strip())

    clips = query.order_by(Clip.created_at.desc()).all()

    records = []
    for c in clips:
        cand = c.candidate
        sv = cand.source_video if cand else None
        ch = sv.channel if sv else None

        start_sec = (cand.start_ms or 0) / 1000.0 if cand else 0.0
        end_sec = (cand.end_ms or 0) / 1000.0 if cand else 0.0
        dur_sec = round(end_sec - start_sec, 2) if cand else 0.0

        records.append({
            "clip_id": c.id,
            "status": c.status.value,
            "source_video_id": sv.youtube_video_id if sv else "",
            "source_video_title": sv.title if sv else "",
            "channel_name": ch.name if ch else "",
            "channel_yt_id": ch.youtube_channel_id if ch else "",
            "start_sec": start_sec,
            "end_sec": end_sec,
            "duration_sec": dur_sec,
            "virality_score": cand.score if cand else "",
            "hook_text": cand.hook_text if cand else "",
            "payoff_text": cand.payoff_text if cand else "",
            "rendered_path": c.rendered_path or "",
            "created_at": c.created_at.isoformat() if c.created_at else "",
        })

    if format_clean == "json":
        return JSONResponse(
            content=records,
            headers={"Content-Disposition": f'attachment; filename="clips_{status.lower()}_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.json"'},
        )

    output = io.StringIO()
    if records:
        writer = csv.DictWriter(output, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)
    else:
        writer = csv.writer(output)
        writer.writerow(["clip_id", "status", "source_video_id", "source_video_title", "channel_name", "channel_yt_id", "start_sec", "end_sec", "duration_sec", "virality_score", "hook_text", "payoff_text", "rendered_path", "created_at"])

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="clips_{status.lower()}_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv"'},
    )


@router.get("/api/video/{clip_id}", summary="Stream or download rendered Short video")
def stream_clip_video(
    clip_id: str,
    download: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    from shorts_engine.services.storage.path_manager import PathManager, get_preview_proxy_path
    from shorts_engine.services.ingestion.local_loader import is_preview_proxy_needed, generate_preview_proxy

    file_path = PathManager.resolve_clip_path(clip)
    if not file_path or not file_path.is_file() or file_path.stat().st_size == 0:
        raise HTTPException(status_code=404, detail=f"Rendered video file not found on disk for clip {clip_id}")

    if download == 1:
        return FileResponse(
            path=str(file_path),
            media_type="video/mp4",
            filename=file_path.name,
            headers={"Content-Disposition": f'attachment; filename="{file_path.name}"'},
        )

    serve_path = file_path
    proxy_path = get_preview_proxy_path(file_path)
    if proxy_path.exists() and proxy_path.stat().st_size > 0:
        serve_path = proxy_path
    elif is_preview_proxy_needed(file_path):
        generated = generate_preview_proxy(file_path)
        if generated and generated.exists() and generated.stat().st_size > 0:
            serve_path = generated

    return FileResponse(
        path=str(serve_path),
        media_type="video/mp4",
        filename=serve_path.name,
    )


@router.post("/api/clips/{clip_id}/cover-frame", summary="Set custom YouTube Short cover frame")
@router.post("/api/clip/{clip_id}/cover-frame", summary="Set custom YouTube Short cover frame (alias)")
async def set_clip_cover_frame(
    request: Request,
    clip_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    from shorts_engine.services.storage.path_manager import PathManager, get_preview_proxy_path
    from shorts_engine.services.ingestion.local_loader import generate_preview_proxy
    from shorts_engine.services.rendering.compositor import VideoCompositor
    from shorts_engine.services.execution.watchdog import SubprocessWatchdog

    file_path = PathManager.resolve_clip_path(clip)
    if not file_path or not file_path.is_file() or file_path.stat().st_size == 0:
        raise HTTPException(status_code=404, detail=f"Rendered video file not found on disk for clip {clip_id}")

    # Parse timestamp_sec
    timestamp_sec = 0.0
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            timestamp_sec = float(body.get("timestamp_sec", 0.0))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")
    else:
        try:
            form = await request.form()
            timestamp_sec = float(form.get("timestamp_sec", 0.0))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid form payload")

    timestamp_sec = max(0.0, timestamp_sec)

    # Clamp timestamp to duration if known to prevent seeking past EOF
    max_duration = 0.0
    cand = getattr(clip, "candidate", None) or getattr(clip, "clip_candidate", None)
    if cand and getattr(cand, "end_ms", None) is not None and getattr(cand, "start_ms", None) is not None:
        max_duration = (cand.end_ms - cand.start_ms) / 1000.0
    elif getattr(clip, "duration_ms", None) is not None:
        max_duration = clip.duration_ms / 1000.0

    if max_duration > 0.0:
        timestamp_sec = min(timestamp_sec, max(0.0, max_duration - 0.05))

    # 1. Extract cover frame at timestamp_sec to thumbnails directory
    cover_dir = settings.STORAGE_DIR / "thumbnails"
    cover_dir.mkdir(parents=True, exist_ok=True)
    cover_img_path = cover_dir / f"{clip.id}_cover.jpg"

    extract_cmd = [
        "ffmpeg", "-y",
        "-ss", f"{timestamp_sec:.3f}",
        "-i", str(file_path),
        "-frames:v", "1",
        "-q:v", "2",
        str(cover_img_path),
    ]
    ext_res = SubprocessWatchdog.run(extract_cmd, timeout_sec=30.0, check=False)
    if ext_res.returncode != 0 or not cover_img_path.exists() or cover_img_path.stat().st_size == 0:
        raise HTTPException(status_code=500, detail="Failed to extract cover frame from video")

    # 2. Inject cover frame as frame 0 into video
    from shorts_engine.utils.fs import atomic_replace
    compositor = VideoCompositor()
    temp_target = file_path.parent / f"{file_path.stem}_injected_{int(time.time())}.mp4"
    try:
        compositor.inject_cover_frame(
            video_path=file_path,
            cover_image_path=cover_img_path,
            output_path=temp_target,
        )
        if temp_target.exists() and temp_target.stat().st_size > 0:
            atomic_replace(temp_target, file_path)
            clip.rendered_path = str(file_path)
    except Exception as e:
        if temp_target.exists():
            temp_target.unlink(missing_ok=True)
        logger.error("Failed to inject cover frame into video for clip %s: %s", clip_id, e)
        raise HTTPException(status_code=500, detail=f"Failed to inject cover frame into video: {e}")

    # 3. If preview proxy exists, regenerate it
    proxy_path = get_preview_proxy_path(file_path)
    if proxy_path.exists():
        generate_preview_proxy(file_path, force=True)

    # 4. Save metadata in quality_check_result
    qr = {}
    if clip.quality_check_result:
        try:
            qr = json.loads(clip.quality_check_result)
        except Exception:
            qr = {}
    qr["cover_frame_sec"] = timestamp_sec
    qr["cover_frame_path"] = str(cover_img_path)
    clip.quality_check_result = json.dumps(qr)
    db.commit()

    logger.info("Cover frame successfully set for clip %s at %.2fs", clip_id, timestamp_sec)

    return {
        "status": "success",
        "clip_id": clip_id,
        "timestamp_sec": timestamp_sec,
        "cover_frame_path": str(cover_img_path),
    }


@router.post("/queue/{clip_id}/approve", summary="Approve a clip for publishing")
def approve_clip(
    request: Request,
    clip_id: str,
    return_query: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    res = approve_clip_for_publishing(
        clip_id=clip_id,
        db=db,
        actor=current_user.username,
        publish_inline=True,
    )

    if not res.get("ok"):
        err_type = res.get("error_type")
        if err_type == "NOT_FOUND":
            raise HTTPException(status_code=404, detail="Clip not found")
        if err_type == "CONFLICT":
            raise HTTPException(status_code=409, detail=res.get("error"))
        
        # In-line publish error (moved to FAILED_NEEDS_REVIEW)
        toast_msg = f"Upload error: {res.get('error')}. Clip moved to Needs Review."
        msg_type = "danger"
    else:
        slot_iso = res.get("slot_start", "")
        yt_id = res.get("youtube_video_id") or "scheduled"
        toast_msg = f"Clip {clip_id[:8]} scheduled for {slot_iso[:16].replace('T', ' ')} UTC (YouTube ID: {yt_id})."
        msg_type = "success"

    if request.headers.get("hx-request") == "true":
        undo_html = f"{toast_msg} <button class='btn btn-sm btn-dark py-0 ms-2' hx-post='/queue/{clip_id}/undo' hx-swap='none'>Undo</button>"
        return Response(
            content="",
            headers={
                "HX-Trigger": json.dumps({
                    "showToast": {"message": undo_html, "type": msg_type},
                    "clipApproved": clip_id,
                })
            }
        )

    if return_query or request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return _make_queue_redirect(
            request,
            default_msg=toast_msg,
            msg_type=msg_type,
            return_query=return_query,
        )

    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error"))

    return {
        "clip_id": clip_id,
        "new_status": res.get("status", "APPROVED"),
        "slot_start": res.get("slot_start"),
        "pub_result": res.get("pub_result"),
    }


@router.post("/queue/{clip_id}/undo", summary="Undo an approval or rejection")
def undo_action(
    clip_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    from shorts_engine.services.publishing.slots import publish_slot_service
    unapp_res = publish_slot_service.unapprove_clip(clip_id=clip.id, db=db)

    logger.info("Operator '%s' undid action for clip %s (unapprove result: %s)", current_user.username, clip_id, unapp_res)
    deleted_note = " (deleted from YouTube)" if unapp_res.get("deleted_on_youtube") else ""
    return Response(
        content="",
        headers={
            "HX-Trigger": json.dumps({
                "showToast": {"message": f"Action undone for {clip_id[:8]}{deleted_note}. Clip restored to Review Queue.", "type": "info"},
                "clipRestored": clip_id
            })
        }
    )


@router.post("/queue/{clip_id}/schedule", summary="Schedule a clip for auto-publishing")
def schedule_clip(
    request: Request,
    clip_id: str,
    scheduled_at: str = Form(...),
    return_query: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    try:
        dt = datetime.fromisoformat(scheduled_at)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {e}")

    from shorts_engine.services.publishing.slots import publish_slot_service
    try:
        res = publish_slot_service.reschedule_clip(clip_id=clip.id, new_slot_time=dt, db=db)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    logger.info("Operator '%s' scheduled/rescheduled clip %s for %s", current_user.username, clip_id, dt.isoformat())

    dt_display = dt.strftime("%Y-%m-%d %H:%M")
    upd_note = " (updated on YouTube)" if res.get("updated_on_youtube") else ""
    if request.headers.get("hx-request") == "true":
        return Response(
            content="",
            headers={
                "HX-Trigger": json.dumps({
                    "showToast": {
                        "message": f"Clip {clip_id[:8]} scheduled for {dt_display}{upd_note}!",
                        "type": "success"
                    },
                    "clipApproved": clip_id
                })
            }
        )

    return _make_queue_redirect(
        request,
        default_msg=f"Clip {clip_id[:8]} scheduled for {dt_display}{upd_note}.",
        msg_type="success",
        return_query=return_query,
    )


@router.get("/api/clips/{clip_id}/publishing-package", summary="Generate complete SEO and publishing package for clip")
def get_publishing_package(
    clip_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    from shorts_engine.services.seo.engine import SEOMetadataEngine
    seo_engine = SEOMetadataEngine()

    channel = None
    if clip.candidate and clip.candidate.source_video:
        channel = clip.candidate.source_video.channel

    package = seo_engine.generate_complete_upload_package(clip=clip, channel=channel, db=db)
    channels = db.query(Channel).order_by(Channel.name.asc()).all()
    package["available_channels"] = [
        {"id": ch.id, "name": ch.name, "youtube_channel_id": ch.youtube_channel_id}
        for ch in channels
    ]
    return package


@router.post("/api/clips/{clip_id}/publish", summary="Publish or schedule clip with custom SEO parameters")
async def publish_or_schedule_clip_api(
    request: Request,
    clip_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    body = await request.json()
    publish_mode = body.get("publish_mode", "schedule").lower()
    scheduled_at_str = body.get("scheduled_at")
    title = body.get("title") or clip.generated_title
    description = body.get("description") or clip.generated_description
    tags = body.get("tags")
    category_id = body.get("category_id", "22")
    made_for_kids = bool(body.get("made_for_kids", False))
    privacy_status = body.get("privacy_status", "unlisted")
    channel_id = body.get("channel_id")

    if isinstance(tags, str):
        tags_list = [t.strip() for t in tags.split(",") if t.strip()]
    elif isinstance(tags, list):
        tags_list = tags
    else:
        tags_list = ["#Shorts", "#Viral"]

    if channel_id and clip.candidate and clip.candidate.source_video:
        matched_channel = db.query(Channel).filter_by(id=channel_id).first()
        if matched_channel:
            clip.candidate.source_video.channel_id = matched_channel.id
            db.commit()

    if title:
        clip.generated_title = title[:100]
    if description:
        clip.generated_description = description[:5000]
    if tags_list:
        clip.generated_tags = json.dumps(tags_list)

    if publish_mode == "schedule":
        if not scheduled_at_str:
            raise HTTPException(status_code=422, detail="scheduled_at datetime is required for schedule mode")
        dt = datetime.fromisoformat(scheduled_at_str.replace("Z", "+00:00"))
        clip.scheduled_publish_at = dt
        clip.status = ClipStatus.APPROVED
        db.commit()
        logger.info("Operator '%s' scheduled clip %s for %s", current_user.username, clip_id, dt.isoformat())
        return {
            "status": "scheduled",
            "clip_id": clip.id,
            "scheduled_publish_at": dt.isoformat(),
            "title": clip.generated_title,
        }

    from shorts_engine.services.queue.durable_queue import durable_job_queue

    payload = {
        "title": clip.generated_title or "Viral Short #Shorts",
        "description": clip.generated_description or "Check out this Short! #Shorts",
        "tags": tags_list,
        "privacy_status": privacy_status,
        "category_id": category_id,
        "made_for_kids": made_for_kids,
    }
    job = durable_job_queue.enqueue(
        job_type="publish",
        entity_id=clip.id,
        entity_type="clip",
        payload=payload,
        stage="publish",
        db=db,
    )
    clip.status = ClipStatus.APPROVED
    db.commit()
    return {
        "success": True,
        "status": "enqueued",
        "job_id": job.id,
        "clip_id": clip.id,
        "message": "Publish job enqueued for publish worker.",
    }


@router.post("/queue/{clip_id}/publish", summary="Publish or schedule clip from form")
def publish_or_schedule_clip_form(
    request: Request,
    clip_id: str,
    publish_mode: str = Form(default="schedule"),
    scheduled_at: Optional[str] = Form(default=None),
    title: Optional[str] = Form(default=None),
    description: Optional[str] = Form(default=None),
    tags: Optional[str] = Form(default=None),
    category_id: str = Form(default="22"),
    made_for_kids: Optional[str] = Form(default=None),
    privacy_status: str = Form(default="unlisted"),
    channel_id: Optional[str] = Form(default=None),
    return_query: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    is_kids = bool(made_for_kids and made_for_kids.lower() in ("on", "true", "1", "yes"))
    tags_list = [t.strip() for t in (tags or "").split(",") if t.strip()] or ["#Shorts", "#Viral"]

    if title:
        clip.generated_title = title[:100]
    if description:
        clip.generated_description = description[:5000]
    clip.generated_tags = json.dumps(tags_list)

    if channel_id and clip.candidate and clip.candidate.source_video:
        matched_ch = db.query(Channel).filter_by(id=channel_id).first()
        if matched_ch:
            clip.candidate.source_video.channel_id = matched_ch.id

    if publish_mode.lower() == "schedule":
        if not scheduled_at:
            raise HTTPException(status_code=422, detail="Scheduled datetime is required")
        dt = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
        clip.scheduled_publish_at = dt
        clip.status = ClipStatus.APPROVED
        db.commit()
        return _make_queue_redirect(
            request,
            default_msg=f"Clip {clip_id[:8]} scheduled for {dt.strftime('%b %d, %H:%M')}.",
            msg_type="success",
            return_query=return_query,
        )

    from shorts_engine.services.queue.durable_queue import durable_job_queue

    payload = {
        "title": clip.generated_title or "Viral Short #Shorts",
        "description": clip.generated_description or "Check out this Short! #Shorts",
        "tags": tags_list,
        "privacy_status": privacy_status,
        "category_id": category_id,
        "made_for_kids": is_kids,
    }
    job = durable_job_queue.enqueue(
        job_type="publish",
        entity_id=clip.id,
        entity_type="clip",
        payload=payload,
        stage="publish",
        db=db,
    )
    clip.status = ClipStatus.APPROVED
    db.commit()
    return _make_queue_redirect(
        request,
        default_msg=f"Clip {clip_id[:8]} queued for publishing (job {job.id[:8]}).",
        msg_type="success",
        return_query=return_query,
    )


def generate_deterministic_variations(hook: Optional[str], title: Optional[str]) -> List[Dict[str, str]]:
    base = hook or title or "Key Takeaway"
    clean_base = " ".join(base.split()[:8]).strip('",.?!;: ')
    return [
        {
            "title": f"The Secret Behind {clean_base}",
            "angle": "Curiosity Gap",
            "hook_overlay": "Wait For It...",
        },
        {
            "title": f"Why Everyone Is Wrong About {clean_base}",
            "angle": "Contrarian",
            "hook_overlay": "Stop Doing This!",
        },
        {
            "title": f"The #1 Mistake With {clean_base}",
            "angle": "High Stakes",
            "hook_overlay": "Watch Before It's Gone",
        },
    ]


@router.post("/queue/{clip_id}/select-variation", summary="Select an A/B title and hook variation")
def select_variation(
    clip_id: str,
    title: str = Form(...),
    angle: str = Form(default="Custom"),
    hook_overlay: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    clip.generated_title = title
    if hook_overlay:
        try:
            current_qc = json.loads(clip.quality_check_result or "{}")
        except Exception:
            current_qc = {}
        current_qc["selected_variation"] = {
            "selected_angle": angle,
            "selected_title": title,
            "selected_hook_overlay": hook_overlay,
        }
        clip.quality_check_result = json.dumps(current_qc)
    db.commit()
    logger.info("Operator '%s' selected variant '%s' for clip %s: %s (hook: %s)", current_user.username, angle, clip_id, title, hook_overlay)

    toast_msg = f"Applied {angle} variant: '{title[:45]}...'"
    if hook_overlay:
        toast_msg += f" (Hook: '{hook_overlay}')"

    return Response(
        content="",
        headers={
            "HX-Trigger": json.dumps({
                "showToast": {
                    "message": toast_msg,
                    "type": "success"
                }
            })
        }
    )



@router.post("/queue/{clip_id}/reject", summary="Reject a clip")
def reject_clip(
    request: Request,
    clip_id: str,
    reason: Optional[str] = Form(default=None),
    reason_code: Optional[str] = Form(default=None),
    return_query: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    res = reject_clip_action(
        clip_id=clip_id,
        db=db,
        actor=current_user.username,
        reason_code=reason_code,
        note=reason,
    )
    if not res.get("ok"):
        err_type = res.get("error_type")
        if err_type == "NOT_FOUND":
            raise HTTPException(status_code=404, detail="Clip not found")
        raise HTTPException(status_code=409, detail=res.get("error"))

    if request.headers.get("hx-request") == "true":
        undo_html = f"Clip {clip_id[:8]} rejected. <button class='btn btn-sm btn-dark py-0 ms-2' hx-post='/queue/{clip_id}/undo' hx-swap='none'>Undo</button>"
        return Response(
            content="",
            headers={"HX-Trigger": json.dumps({"showToast": {"message": undo_html, "type": "warning"}, "clipRejected": clip_id})}
        )

    if return_query or request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return _make_queue_redirect(
            request,
            default_msg=f"Clip {clip_id[:8]} rejected",
            msg_type="warning",
            return_query=return_query,
            extra_params={"rejected_id": clip_id},
        )
    return {"clip_id": clip_id, "new_status": ClipStatus.REJECTED.value, "reason": reason, "reason_code": res.get("reason_code")}


@router.post("/queue/{clip_id}/restore", summary="Restore a rejected clip back to review queue")
def restore_clip(
    request: Request,
    clip_id: str,
    return_query: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if clip.status != ClipStatus.REJECTED:
        raise HTTPException(
            status_code=409,
            detail=f"Clip is in status {clip.status.value}, not REJECTED",
        )
    clip.status = ClipStatus.WAITING_APPROVAL
    clip.queued_at = datetime.utcnow()
    db.commit()
    logger.info("Operator '%s' restored clip %s from REJECTED back to WAITING_APPROVAL", current_user.username, clip_id)

    if return_query or request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return _make_queue_redirect(
            request,
            default_msg=f"Clip {clip_id[:8]} restored to queue",
            msg_type="success",
            return_query=return_query,
        )
    return {"clip_id": clip_id, "new_status": ClipStatus.WAITING_APPROVAL.value}
@router.post("/queue/re-evaluate/{video_id}", summary="Re-evaluate all rendered clips of a video with updated Quality Gate")
def re_evaluate_video(
    video_id: str,
    request: Request,
    return_query: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    from shorts_engine.orchestration.runner import PipelineRunner
    runner = PipelineRunner(db)
    res = runner.re_evaluate_clips_for_video(video_id)
    recovered = res.get("recovered", 0)
    total = res.get("total_clips", 0)
    msg = f"Re-evaluated {total} clip(s): {recovered} recovered to Review Queue."
    logger.info("Operator '%s' re-evaluated video %s: %s", current_user.username, video_id, msg)

    if return_query or request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return _make_queue_redirect(
            request,
            default_msg=msg,
            msg_type="success" if recovered > 0 else "info",
            return_query=return_query,
        )
    return res


@router.post("/api/clips/{clip_id}/open-folder", summary="Open folder containing clip in native file explorer")
def open_clip_folder(
    clip_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
    csrf_ok: bool = Depends(verify_csrf),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip or not clip.rendered_path:
        raise HTTPException(status_code=404, detail="Clip or rendered file not found")

    import tempfile
    resolved_path = Path(clip.rendered_path).resolve()
    allowed_roots = [
        settings.STORAGE_DIR.resolve(),
        Path(".").resolve(),
        Path(tempfile.gettempdir()).resolve(),
    ]
    is_allowed = False
    for root in allowed_roots:
        try:
            resolved_path.relative_to(root)
            is_allowed = True
            break
        except ValueError:
            pass
    if not is_allowed:
        raise HTTPException(status_code=403, detail="Access denied: file path is outside allowed media root.")

    from shorts_engine.services.quality_gate.recovery import ClipRecoveryService
    return ClipRecoveryService.open_in_folder(clip, db=db)


@router.post(
    "/api/clip/{clip_id}/open-folder",
    summary="Open folder containing clip in native file explorer (deprecated alias)",
    deprecated=True,
)
def open_clip_folder_deprecated(
    clip_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
    csrf_ok: bool = Depends(verify_csrf),
):
    logger.warning("Deprecated route accessed: /api/clip/%s/open-folder. Please migrate to /api/clips/%s/open-folder", clip_id, clip_id)
    return open_clip_folder(clip_id=clip_id, db=db, current_user=current_user, csrf_ok=csrf_ok)


@router.get("/queue/clip/{clip_id}/diagnostics", summary="Get Quality Gate diagnostics and suggested fixes for clip")
def get_clip_diagnostics(
    clip_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    from shorts_engine.services.quality_gate.recovery import ClipRecoveryService
    recovery = ClipRecoveryService()
    return recovery.diagnose_clip(clip)


@router.post("/queue/clip/{clip_id}/re-evaluate", summary="Re-evaluate single clip Quality Gate")
def re_evaluate_single_clip_endpoint(
    clip_id: str,
    request: Request,
    return_query: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    from shorts_engine.services.quality_gate.recovery import ClipRecoveryService
    recovery = ClipRecoveryService()
    new_status, q_report = recovery.re_evaluate_clip(clip, db)
    status_str = new_status.value if hasattr(new_status, "value") else str(new_status)
    msg = f"Clip {clip_id[:8]} re-evaluated: now {status_str}"
    logger.info("Operator '%s' re-evaluated clip %s: %s", current_user.username, clip_id, status_str)

    if return_query or request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return _make_queue_redirect(
            request,
            default_msg=msg,
            msg_type="success" if status_str in ("WAITING_APPROVAL", "APPROVED") else "warning",
            return_query=return_query,
        )
    return {"status": "success", "clip_id": clip_id, "new_status": status_str, "report": q_report}


@router.post("/queue/clip/{clip_id}/force-approve", summary="Operator override: force approve clip despite quality warnings")
def force_approve_single_clip_endpoint(
    clip_id: str,
    request: Request,
    return_query: Optional[str] = Form(default=None),
    note: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    from shorts_engine.services.quality_gate.recovery import ClipRecoveryService
    recovery = ClipRecoveryService()
    new_status, q_report = recovery.force_approve_clip(clip, db, operator_name=current_user.username, note=note)
    status_str = new_status.value if hasattr(new_status, "value") else str(new_status)
    msg = f"Clip {clip_id[:8]} force-approved by {current_user.username}"

    if return_query or request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return _make_queue_redirect(
            request,
            default_msg=msg,
            msg_type="success",
            return_query=return_query,
        )
    return {"status": "success", "clip_id": clip_id, "new_status": status_str, "report": q_report}


@router.post("/queue/clip/{clip_id}/fix", summary="Apply automated failure fix and re-evaluate clip")
def auto_fix_single_clip_endpoint(
    clip_id: str,
    request: Request,
    fix_type: str = Form(...),
    return_query: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    from shorts_engine.services.quality_gate.recovery import ClipRecoveryService
    from shorts_engine.services.rendering.trimmer import ClipTrimmerService
    recovery = ClipRecoveryService(trimmer_service=ClipTrimmerService(db))
    res = recovery.apply_auto_fix(clip, fix_type, db)
    msg = res.get("message") or f"Applied fix '{fix_type}' to clip {clip_id[:8]}"
    logger.info("Operator '%s' applied fix '%s' to clip %s: %s", current_user.username, fix_type, clip_id, res)

    if return_query or request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return _make_queue_redirect(
            request,
            default_msg=msg,
            msg_type="success" if res.get("status") == "success" else "danger",
            return_query=return_query,
        )
    return res


@router.post("/queue/bulk-action", summary="Bulk approve or reject multiple clips")
def bulk_action(
    request: Request,
    action: str = Form(...),
    clip_ids: List[str] = Form(...),
    reason: Optional[str] = Form(default=None),
    reason_code: Optional[str] = Form(default=None),
    return_query: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Invalid action; must be 'approve' or 'reject'")

    if action == "approve":
        res = bulk_approve_clips(
            clip_ids=clip_ids,
            db=db,
            actor=current_user.username,
        )
    else:
        res = bulk_reject_clips(
            clip_ids=clip_ids,
            db=db,
            actor=current_user.username,
            reason_code=reason_code,
            note=reason,
        )

    succeeded = res.get("succeeded", 0)
    failed = res.get("failed", 0)
    msg = f"Bulk {action} completed: {succeeded} succeeded, {failed} failed."
    msg_type = "success" if failed == 0 else ("warning" if succeeded > 0 else "danger")

    if request.headers.get("hx-request") == "true":
        return Response(
            content=json.dumps({"action": action, **res}),
            media_type="application/json",
            headers={
                "HX-Trigger": json.dumps({
                    "showToast": {"message": msg, "type": msg_type},
                    "bulkActionCompleted": {"action": action, **res},
                })
            },
        )

    if return_query or request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return _make_queue_redirect(
            request,
            default_msg=msg,
            msg_type=msg_type,
            return_query=return_query,
        )

    return {"status": "success", "action": action, "count": succeeded, **res}


@router.post("/queue/{clip_id}/re-render-style", summary="Re-render a clip with custom or preset caption styling")
def re_render_clip_style(
    request: Request,
    clip_id: str,
    template_id: Optional[str] = Form(default=None),
    font_name: Optional[str] = Form(default=None),
    font_size: Optional[int] = Form(default=None),
    primary_hex: Optional[str] = Form(default=None),
    highlight_hex: Optional[str] = Form(default=None),
    outline_hex: Optional[str] = Form(default=None),
    outline_width: Optional[float] = Form(default=None),
    shadow_depth: Optional[float] = Form(default=None),
    margin_vertical: Optional[int] = Form(default=None),
    words_per_line: Optional[int] = Form(default=None),
    case_transform: Optional[str] = Form(default=None),
    hook_overlay_text: Optional[str] = Form(default=None),
    cta_overlay_text: Optional[str] = Form(default=None),
    watermark_text: Optional[str] = Form(default=None),
    watermark_anchor: Optional[str] = Form(default="top_right"),
    watermark_image_path: Optional[str] = Form(default=None),
    scale_pop: Optional[bool] = Form(default=None),
    force_re_render: Optional[bool] = Form(default=True),
    return_query: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    t0 = time.time()
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    from shorts_engine.services.captioning.templates import get_caption_template, parse_custom_template
    from shorts_engine.services.storage.path_manager import PathManager
    from shorts_engine.services.captioning.ass_generator import ASSSubtitleGenerator
    from shorts_engine.services.rendering.compositor import VideoCompositor
    from shorts_engine.services.reframing.cropper import VideoReframer
    from shorts_engine.services.transcription.formatter import WordSegment

    cand = clip.candidate
    if not cand:
        raise HTTPException(status_code=404, detail="Clip candidate not found")
    sv = cand.source_video
    if not sv or not sv.downloaded_path:
        raise HTTPException(status_code=404, detail="Source video or download path not found")

    source_path = PathManager.resolve_download_path(sv)
    if not source_path or not source_path.exists():
        raise HTTPException(status_code=404, detail="Source video media file missing on disk")

    # Determine template / custom overrides
    if font_name or primary_hex or highlight_hex:
        custom_dict = {
            "template_id": "custom",
            "name": "Custom Style",
            "font_name": font_name or "Arial Black",
            "font_size": font_size or 56,
            "primary_hex": primary_hex or "#FFFFFF",
            "highlight_hex": highlight_hex or "#00FFFF",
            "outline_hex": outline_hex or "#000000",
            "outline_width": outline_width or 4.0,
            "shadow_depth": shadow_depth or 2.0,
            "margin_vertical": margin_vertical or settings.CAPTION_MARGIN_VERTICAL,
            "max_words_per_line": words_per_line or 4,
            "case_transform": case_transform or "uppercase",
            "scale_pop": scale_pop if scale_pop is not None else True,
        }
        tmpl = parse_custom_template(custom_dict)
        clip.caption_style = json.dumps(tmpl.to_ui_dict())
        style_overrides = tmpl.to_ass_overrides()
    else:
        tmpl = get_caption_template(template_id or settings.DEFAULT_CAPTION_PRESET, db=db)
        clip.caption_style = json.dumps(tmpl.to_ui_dict())
        style_overrides = tmpl.to_ass_overrides()
        if margin_vertical is not None:
            style_overrides["margin_vertical"] = margin_vertical
        if words_per_line is not None:
            style_overrides["max_words_per_line"] = words_per_line
        if case_transform is not None:
            style_overrides["case_transform"] = case_transform
        if scale_pop is not None:
            style_overrides["scale_pop"] = scale_pop
        if outline_width is not None:
            style_overrides["outline_width"] = outline_width
        if shadow_depth is not None:
            style_overrides["shadow_depth"] = shadow_depth

    # Load transcript words
    words = []
    if sv.transcript and sv.transcript.word_level_json:
        try:
            wdata = json.loads(sv.transcript.word_level_json)
            if isinstance(wdata, dict) and "words" in wdata:
                wdata = wdata["words"]
            words = [WordSegment(word=w["word"], start=float(w["start"]), end=float(w["end"])) for w in wdata]
        except Exception as e:
            logger.warning("Could not parse transcript words for re-render: %s", e)

    # Re-generate ASS subtitle with cache invalidation
    sub_path = PathManager.get_subtitle_path(sv.youtube_video_id, cand.start_ms, cand.end_ms)
    ass_gen = ASSSubtitleGenerator()
    ass_gen.generate_ass(
        words=words,
        segment_start_ms=cand.start_ms,
        segment_end_ms=cand.end_ms,
        output_path=sub_path,
        style_overrides=style_overrides,
    )

    # Re-compute crop plan
    reframer = VideoReframer()
    crop_plan = reframer.compute_crop_plan(
        video_path=source_path,
        start_ms=cand.start_ms,
        end_ms=cand.end_ms,
    )

    # Output path
    ch_name = sv.channel.name if sv.channel else "Channel"
    ch_id = sv.channel.youtube_channel_id if sv.channel else "default"
    target_clip_path = PathManager.get_clip_output_path(
        channel_name=ch_name,
        channel_id=ch_id,
        video_title=sv.title,
        video_id=sv.youtube_video_id,
        hook_text=cand.hook_text,
        start_ms=cand.start_ms,
        end_ms=cand.end_ms,
    )
    out_name = str(target_clip_path.relative_to(settings.OUTPUT_DIR)).replace("\\", "/")

    # Visual hook overlay
    hook_overlay = hook_overlay_text
    if not hook_overlay and clip.quality_check_result:
        try:
            qc = json.loads(clip.quality_check_result)
            hook_overlay = qc.get("selected_variation", {}).get("selected_hook_overlay")
        except Exception:
            pass

    # Invalidate cache if force_re_render
    if force_re_render and target_clip_path.exists():
        try:
            target_clip_path.unlink()
            logger.info("Invalidated and unlinked existing render artifact at %s", target_clip_path)
        except Exception as e:
            logger.warning("Could not unlink existing render file: %s", e)

    compositor = VideoCompositor()
    rendered_file = compositor.render_short(
        source_video_path=source_path,
        start_ms=cand.start_ms,
        end_ms=cand.end_ms,
        crop_plan=crop_plan,
        subtitle_ass_path=sub_path,
        output_filename=out_name,
        normalize_audio=True,
        hook_overlay_text=hook_overlay,
        force_re_render=bool(force_re_render),
        watermark_text=watermark_text,
        watermark_anchor=watermark_anchor or "top_right",
        watermark_image_path=Path(watermark_image_path) if watermark_image_path else None,
        cta_overlay_text=cta_overlay_text,
    )

    clip.rendered_path = str(rendered_file)
    db.commit()

    render_duration = round(time.time() - t0, 1)
    logger.info("Operator '%s' re-rendered clip %s with style %s in %0.1fs", current_user.username, clip_id, tmpl.name, render_duration)
    msg = f"Re-rendered clip {clip_id[:8]} with '{tmpl.name}' style in {render_duration}s."
    if return_query or request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return _make_queue_redirect(request, default_msg=msg, msg_type="success", return_query=return_query)
    return {"status": "re_rendered", "clip_id": clip_id, "rendered_path": clip.rendered_path, "style": tmpl.name, "render_time_sec": render_duration}


@router.get("/api/caption-presets", summary="List all caption presets (built-in and custom)")
def list_caption_presets_api(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    from shorts_engine.services.captioning.templates import list_caption_templates
    return list_caption_templates(db=db)


@router.post("/api/caption-presets", summary="Create or update a custom caption preset")
async def create_caption_preset_api(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    from shorts_engine.services.captioning.templates import save_preset_to_db
    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            payload = await request.json()
        else:
            form = await request.form()
            payload = dict(form)
        preset = save_preset_to_db(db, payload)
        return {"status": "saved", "preset": preset.to_dict()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/caption-presets/{preset_id}", summary="Delete a custom caption preset")
def delete_caption_preset_api(
    preset_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    from shorts_engine.services.captioning.templates import delete_preset_from_db
    success = delete_preset_from_db(db, preset_id)
    if not success:
        raise HTTPException(status_code=404, detail="Preset not found or cannot delete built-in preset")
    return {"status": "deleted", "id": preset_id}


@router.post("/queue/{clip_id}/trim", summary="Trim and re-time a clip with speech pause snapping")
def trim_clip(
    request: Request,
    clip_id: str,
    start_sec: float = Form(...),
    end_sec: float = Form(...),
    snap_words: Optional[str] = Form(default="true"),
    caption_style: Optional[str] = Form(default=None),
    return_query: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    from shorts_engine.services.rendering.trimmer import ClipTrimmerService

    should_snap = snap_words is not None and snap_words.lower() in ("true", "1", "yes", "on")
    trimmer = ClipTrimmerService(db)

    try:
        res = trimmer.trim_clip(
            clip_id=clip_id,
            start_sec=start_sec,
            end_sec=end_sec,
            snap_words=should_snap,
            caption_style_preset=caption_style,
        )
    except Exception as e:
        logger.error("Failed to trim clip %s: %s", clip_id, e, exc_info=True)
        err_msg = f"Failed to trim clip: {e}"
        if request.headers.get("hx-request") == "true":
            return Response(
                content="",
                status_code=400,
                headers={"HX-Trigger": json.dumps({"showToast": {"message": err_msg, "type": "error"}})}
            )
        raise HTTPException(status_code=400, detail=str(e))

    msg = f"Trimmed clip {clip_id[:8]} to {res['start_sec']}s – {res['end_sec']}s ({res['duration_sec']}s)!"
    if request.headers.get("hx-request") == "true":
        return Response(
            content="",
            headers={
                "HX-Trigger": json.dumps({
                    "showToast": {"message": msg, "type": "success"},
                    "clipTrimmed": {"clip_id": clip_id, **res},
                })
            }
        )
    if return_query or request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return _make_queue_redirect(request, default_msg=msg, msg_type="success", return_query=return_query)
    return res


@router.post("/queue/{clip_id}/delete", summary="Delete a clip with optional media file cleanup")
def delete_clip(
    request: Request,
    clip_id: str,
    delete_files: Optional[str] = Form(default="false"),
    return_query: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    from shorts_engine.services.storage.path_manager import PathManager
    should_wipe_files = delete_files is not None and delete_files.lower() in ("true", "1", "yes", "on")

    if should_wipe_files:
        if clip.rendered_path:
            p = PathManager.resolve_clip_path(clip)
            if p and p.exists():
                PathManager.safe_unlink(p)
                thumb = p.with_name(f"{p.stem}_thumb.jpg")
                PathManager.safe_unlink(thumb)
        # Also check storage/thumbnails
        q_thumb = settings.STORAGE_DIR / "thumbnails" / f"{clip.id}.jpg"
        PathManager.safe_unlink(q_thumb)
        logger.info("Operator '%s' deleted clip %s and unlinked media files from disk", current_user.username, clip_id)
    else:
        logger.info("Operator '%s' deleted clip %s record (preserved media files on disk)", current_user.username, clip_id)

    cand = clip.candidate
    db.delete(clip)
    if cand:
        cand.selected = False
    db.commit()

    msg = f"Deleted clip {clip_id[:8]} ({'files removed' if should_wipe_files else 'files preserved'})."
    if return_query or request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return _make_queue_redirect(request, default_msg=msg, msg_type="info", return_query=return_query)
    return {"status": "deleted", "clip_id": clip_id, "files_deleted": should_wipe_files}



@router.get("/dead-letter", response_class=HTMLResponse, summary="Dead-letter queue")
def dead_letter_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    failed_clips = (
        db.query(Clip)
        .filter_by(status=ClipStatus.FAILED)
        .order_by(Clip.updated_at.desc())
        .all()
    )
    failed_videos = (
        db.query(SourceVideo)
        .filter_by(status=SourceVideoStatus.FAILED)
        .order_by(SourceVideo.updated_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        request=request,
        name="dead_letter.html",
        context={"failed_clips": failed_clips, "failed_videos": failed_videos, "current_user": current_user, "csrf_token": get_csrf_token(request)},
    )


@router.post("/dead-letter/clip/{clip_id}/retry", summary="Retry a failed clip")
def retry_failed_clip(
    request: Request,
    clip_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if clip.status != ClipStatus.FAILED:
        raise HTTPException(status_code=409, detail="Clip is not in FAILED status")
    if clip.retry_count >= settings.MAX_RETRY_COUNT:
        raise HTTPException(
            status_code=422,
            detail=f"Clip has exhausted max retries ({settings.MAX_RETRY_COUNT}). Manual intervention required.",
        )
    clip.status = ClipStatus.RENDERING
    clip.retry_count += 1
    db.commit()
    logger.info("Operator '%s' triggered retry on failed clip %s (attempt %s/%s)", current_user.username, clip_id, clip.retry_count, settings.MAX_RETRY_COUNT)
    if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return RedirectResponse(url="/dead-letter", status_code=303)
    return {
        "clip_id": clip_id,
        "new_status": ClipStatus.RENDERING.value,
        "retry_count": clip.retry_count,
        "max_retries": settings.MAX_RETRY_COUNT,
    }


@router.post("/dead-letter/video/{video_id}/retry", summary="Retry a failed source video")
def retry_failed_video(
    request: Request,
    video_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    video = db.query(SourceVideo).filter_by(id=video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.status != SourceVideoStatus.FAILED:
        raise HTTPException(status_code=409, detail="Video is not in FAILED status")
    if video.retry_count >= settings.MAX_RETRY_COUNT:
        raise HTTPException(
            status_code=422,
            detail=f"Video has exhausted max retries ({settings.MAX_RETRY_COUNT}). Manual intervention required.",
        )
    video.status = SourceVideoStatus.DETECTED
    video.retry_count += 1
    video.error_message = None
    db.commit()
    logger.info("Operator '%s' triggered retry on failed source video %s (attempt %s/%s)", current_user.username, video_id, video.retry_count, settings.MAX_RETRY_COUNT)
    if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return RedirectResponse(url="/dead-letter", status_code=303)
    return {
        "video_id": video_id,
        "new_status": SourceVideoStatus.DETECTED.value,
        "retry_count": video.retry_count,
        "max_retries": settings.MAX_RETRY_COUNT,
    }


@router.post("/dead-letter/clip/{clip_id}/delete", summary="Dismiss or delete a failed clip")
def delete_failed_clip(
    request: Request,
    clip_id: str,
    delete_files: bool = Form(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    if delete_files and clip.rendered_path:
        from shorts_engine.services.storage.path_manager import PathManager
        PathManager.safe_unlink(clip.rendered_path)
        logger.info("Unlinked rendered file %s for deleted clip %s", clip.rendered_path, clip_id)

    candidate = clip.candidate
    if candidate:
        db.delete(candidate)
    else:
        db.delete(clip)
    db.commit()

    logger.info("Operator '%s' dismissed/deleted failed clip %s (delete_files=%s)", current_user.username, clip_id, delete_files)
    if request.headers.get("hx-request") == "true":
        return Response(
            content="",
            headers={"HX-Trigger": json.dumps({"showToast": {"message": "Clip dismissed.", "type": "info"}, "itemDeleted": clip_id})}
        )
    if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return RedirectResponse(url="/dead-letter", status_code=303)
    return {"status": "deleted", "clip_id": clip_id, "files_deleted": delete_files}


@router.post("/dead-letter/video/{video_id}/delete", summary="Dismiss or delete a failed source video")
def delete_failed_video(
    request: Request,
    video_id: str,
    delete_files: bool = Form(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    video = db.query(SourceVideo).filter(
        (SourceVideo.id == video_id) | (SourceVideo.youtube_video_id == video_id)
    ).first()
    if not video:
        raise HTTPException(status_code=404, detail="Source video not found")

    yt_id = video.youtube_video_id
    from shorts_engine.orchestration.runner import release_video_lock, clear_cancellation
    release_video_lock(yt_id)
    clear_cancellation(yt_id)

    if delete_files:
        from shorts_engine.services.storage.path_manager import PathManager
        for p in [video.downloaded_path, video.audio_path]:
            if p:
                PathManager.safe_unlink(p)
                logger.info("Unlinked file %s for deleted video %s", p, yt_id)

        for cand in video.candidates:
            if cand.clip and cand.clip.rendered_path:
                PathManager.safe_unlink(cand.clip.rendered_path)

    db.delete(video)
    db.commit()

    logger.info("Operator '%s' dismissed/deleted failed source video %s (%s, delete_files=%s)", current_user.username, video.id, yt_id, delete_files)
    if request.headers.get("hx-request") == "true":
        return Response(
            content="",
            headers={"HX-Trigger": json.dumps({"showToast": {"message": "Video dismissed.", "type": "info"}, "itemDeleted": yt_id})}
        )
    if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return RedirectResponse(url="/dead-letter", status_code=303)
    return {"status": "deleted", "video_id": yt_id, "files_deleted": delete_files}


@router.post("/dead-letter/clear-all", summary="Dismiss and delete all failed clips and videos")
def clear_all_failed(
    request: Request,
    delete_files: bool = Form(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    from shorts_engine.orchestration.runner import release_video_lock, clear_cancellation
    from shorts_engine.services.storage.path_manager import PathManager

    failed_clips = db.query(Clip).filter_by(status=ClipStatus.FAILED).all()
    failed_videos = db.query(SourceVideo).filter_by(status=SourceVideoStatus.FAILED).all()

    c_count = len(failed_clips)
    v_count = len(failed_videos)

    for c in failed_clips:
        if delete_files and c.rendered_path:
            PathManager.safe_unlink(c.rendered_path)
        cand = c.candidate
        if cand:
            db.delete(cand)
        else:
            db.delete(c)

    for v in failed_videos:
        release_video_lock(v.youtube_video_id)
        clear_cancellation(v.youtube_video_id)
        if delete_files:
            for p in [v.downloaded_path, v.audio_path]:
                if p:
                    PathManager.safe_unlink(p)
            for cand in v.candidates:
                if cand.clip and cand.clip.rendered_path:
                    PathManager.safe_unlink(cand.clip.rendered_path)
        db.delete(v)

    db.commit()
    logger.info("Operator '%s' cleared all dead-letter items (%d clips, %d videos, delete_files=%s)", current_user.username, c_count, v_count, delete_files)

    if request.headers.get("hx-request") == "true":
        return Response(
            content="",
            headers={"HX-Trigger": json.dumps({"showToast": {"message": f"Cleared {c_count} clips and {v_count} videos.", "type": "info"}, "allCleared": True})}
        )
    if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return RedirectResponse(url="/dead-letter", status_code=303)
    return {"status": "cleared", "deleted_clips": c_count, "deleted_videos": v_count, "files_deleted": delete_files}


@router.get("/dead-letter/clear-all", summary="Redirect direct browser access to dead-letter queue")
def clear_all_failed_get(
    current_user: User = Depends(require_viewer),
):
    return RedirectResponse(url="/dead-letter", status_code=303)


@router.get("/api/queue/summary", summary="Review queue summary for React SPA")
def api_queue_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    active_clips = (
        db.query(Clip)
        .outerjoin(ClipCandidate, Clip.clip_candidate_id == ClipCandidate.id)
        .filter(Clip.status == ClipStatus.WAITING_APPROVAL)
        .order_by(Clip.created_at.desc())
        .limit(100)
        .all()
    )
    rejected_clips = (
        db.query(Clip)
        .outerjoin(ClipCandidate, Clip.clip_candidate_id == ClipCandidate.id)
        .filter(Clip.status == ClipStatus.REJECTED)
        .order_by(Clip.created_at.desc())
        .limit(50)
        .all()
    )

    def _format_clip(c: Clip):
        cand = c.candidate
        vid_id = (cand.source_video_id if cand else "") or ""
        hook = (cand.hook_text if cand else None) or c.generated_title or ""
        payoff = (cand.payoff_text if cand else None) or ""
        score = float(cand.score if (cand and cand.score is not None) else 8.5)
        duration_ms = int((cand.end_ms or 0) - (cand.start_ms or 0)) if cand else 30000

        return {
            "id": c.id,
            "hook_text": hook,
            "payoff_text": payoff,
            "score": score,
            "duration_ms": duration_ms,
            "status": c.status.value,
            "ai_provider": "LLM",
            "ai_model": "heuristic",
            "rendered_path": c.rendered_path,
            "video_id": vid_id,
            "thumbnail_url": f"/api/thumbnails/{c.id}",
            "is_sensitive": bool(getattr(c, "is_sensitive", False)),
        }

    return {
        "clips": [_format_clip(c) for c in active_clips],
        "rejected": [_format_clip(c) for c in rejected_clips],
    }


@router.post("/api/clips/{clip_id}/{action}", summary="Execute action on clip from React SPA")
def api_clip_action(
    clip_id: str,
    action: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    if action == "approve":
        res = approve_clip_for_publishing(clip_id, db=db, actor=current_user.username, publish_inline=True)
        if not res.get("ok"):
            raise HTTPException(status_code=400, detail=res.get("error"))
        return {"status": "success", "clip_id": clip_id, "action": action, "new_status": res.get("status", "APPROVED"), "res": res}
    elif action == "reject":
        res = reject_clip_action(clip_id, db=db, actor=current_user.username)
        if not res.get("ok"):
            raise HTTPException(status_code=400, detail=res.get("error"))
        return {"status": "success", "clip_id": clip_id, "action": action, "new_status": ClipStatus.REJECTED.value}
    elif action in ("restore", "recover"):
        clip.status = ClipStatus.WAITING_APPROVAL
        clip.queued_at = datetime.utcnow()
        db.commit()
    elif action == "open-folder":
        from shorts_engine.services.quality_gate.recovery import ClipRecoveryService
        if not clip.rendered_path:
            raise HTTPException(status_code=404, detail="Clip has no rendered file associated")
        return ClipRecoveryService.open_in_folder(clip, db=db)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action {action}")

    db.commit()
    return {"status": "success", "clip_id": clip_id, "action": action, "new_status": clip.status.value}


@router.post("/api/queue/ingest", summary="Ingest external video file into Review Queue")
async def ingest_external_video(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    channel_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    """
    Accepts external video file (.mp4, .mov, .mkv, .webm), validates format and dimensions,
    and stores it in storage/renders with source='external'.
    """
    import shutil
    from shorts_engine.services.execution.watchdog import SubprocessWatchdog
    from shorts_engine.db.models import generate_uuid

    filename = file.filename or "uploaded_video.mp4"
    ext = Path(filename).suffix.lower()
    if ext not in (".mp4", ".mov", ".mkv", ".webm"):
        raise HTTPException(status_code=400, detail="Only video files (.mp4, .mov, .mkv, .webm) are allowed")

    renders_dir = settings.STORAGE_DIR / "renders"
    renders_dir.mkdir(parents=True, exist_ok=True)
    clean_stem = "".join(c for c in Path(filename).stem if c.isalnum() or c in ("-", "_"))[:30]
    out_filename = f"ext_{int(time.time())}_{clean_stem}{ext}"
    target_path = renders_dir / out_filename

    try:
        with open(target_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        logger.exception("Failed to write ingested video file: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to save video file: {e}")

    duration_sec = 30.0
    width = 1080
    height = 1920
    try:
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration:stream=width,height",
            "-of", "json",
            str(target_path),
        ]
        res = SubprocessWatchdog.run(probe_cmd, timeout_sec=15.0, check=False)
        if res.returncode == 0 and res.stdout.strip():
            probe_data = json.loads(res.stdout)
            format_info = probe_data.get("format", {})
            if "duration" in format_info:
                duration_sec = float(format_info["duration"])
            streams = probe_data.get("streams", [])
            for st in streams:
                if "width" in st and "height" in st:
                    width = int(st["width"])
                    height = int(st["height"])
                    break
    except Exception as e:
        logger.warning("Could not probe ingested video: %s", e)

    is_vertical = height > width
    if duration_sec > 60.0:
        if target_path.exists():
            try:
                target_path.unlink()
            except Exception:
                pass
        raise HTTPException(
            status_code=400,
            detail=f"Video exceeds YouTube Shorts duration limit (duration: {duration_sec:.1f}s > 60.0s).",
        )
    if not is_vertical:
        if target_path.exists():
            try:
                target_path.unlink()
            except Exception:
                pass
        raise HTTPException(
            status_code=400,
            detail=f"Video is not vertical ({width}x{height}). YouTube Shorts must be vertical (aspect ratio 9:16).",
        )

    channel = None
    if channel_id:
        channel = db.query(Channel).filter_by(id=channel_id).first()
        if not channel:
            channel = db.query(Channel).filter_by(youtube_channel_id=channel_id).first()
    if not channel:
        channel = db.query(Channel).filter_by(youtube_channel_id="UCBWVd_s5Gb6silTdeYaMqvA").first() or db.query(Channel).first()

    clean_title = (title or Path(filename).stem.replace("_", " ").title())[:100]

    source_vid = SourceVideo(
        youtube_video_id=f"ext_{int(time.time())}_{generate_uuid()[:6]}",
        title=clean_title,
        channel_id=channel.id if channel else None,
        duration_sec=duration_sec,
    )
    db.add(source_vid)
    db.flush()

    candidate = ClipCandidate(
        source_video_id=source_vid.id,
        start_ms=0,
        end_ms=int(duration_sec * 1000),
        hook_text=clean_title,
        payoff_text="External video ingest",
        score=8.5,
        reason="[external_ingest]",
    )
    db.add(candidate)
    db.flush()

    clip = Clip(
        clip_candidate_id=candidate.id,
        status=ClipStatus.WAITING_APPROVAL,
        source="external",
        rendered_path=str(target_path),
        generated_title=clean_title,
        draft_title=clean_title,
        draft_description="Check out this Short! #Shorts #Viral",
        draft_tags=json.dumps(["#Shorts", "#Viral", "#External"]),
        draft_version=1,
    )
    db.add(clip)
    db.commit()
    db.refresh(clip)

    is_vertical = height > width
    is_shorts_compliant = duration_sec <= 60.0 and is_vertical

    logger.info("Ingested external video %s as clip %s (duration: %.1fs, %dx%d)", filename, clip.id, duration_sec, width, height)

    return {
        "status": "success",
        "clip_id": clip.id,
        "title": clip.generated_title,
        "duration_sec": round(duration_sec, 1),
        "dimensions": f"{width}x{height}",
        "is_vertical": is_vertical,
        "is_shorts_compliant": is_shorts_compliant,
        "rendered_path": str(target_path),
        "source": "external",
        "channel_name": channel.name if channel else "Default Channel",
    }


@router.get("/api/jobs/quarantined", summary="List quarantined jobs with their last error")
def list_quarantined_jobs(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    from shorts_engine.orchestration.stage_registry import get_quarantined_jobs
    jobs = get_quarantined_jobs(db, limit=limit, offset=offset)
    return {
        "total": len(jobs),
        "jobs": [
            {
                "id": j.id,
                "job_type": j.job_type,
                "entity_type": j.entity_type,
                "entity_id": j.entity_id,
                "stage": j.stage,
                "attempt_count": getattr(j, "attempt_count", 0),
                "retry_count": getattr(j, "retry_count", 0),
                "last_error": j.last_error or j.error_message,
                "status": str(j.status),
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "updated_at": j.updated_at.isoformat() if j.updated_at else None,
                "completed_at": j.completed_at.isoformat() if j.completed_at else None,
            }
            for j in jobs
        ],
    }

