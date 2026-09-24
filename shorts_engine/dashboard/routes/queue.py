import os
import csv
import io
import json
import logging
import urllib.parse
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus
from typing import Optional, List, Dict, Any
from collections import OrderedDict
from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import or_

from shorts_engine.config.settings import settings
from shorts_engine.dashboard.auth import require_operator, require_viewer, verify_csrf, get_csrf_token
from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import Clip, ClipStatus, ClipCandidate, SourceVideo, SourceVideoStatus, Channel, User
from shorts_engine.services.clip_selection.selector import parse_ai_provenance
from shorts_engine.services.analytics.predictor import viral_predictor

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
    active_count = db.query(Clip).filter_by(status=ClipStatus.WAITING_APPROVAL).count()
    rejected_count = db.query(Clip).filter_by(status=ClipStatus.REJECTED).count()

    current_tab = "rejected" if tab == "rejected" else "active"
    target_status = ClipStatus.REJECTED if current_tab == "rejected" else ClipStatus.WAITING_APPROVAL

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
    total_items = base_q.count()
    limit = max(1, min(limit, 100))
    total_pages = max(1, (total_items + limit - 1) // limit)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * limit

    clips = base_q.offset(offset).limit(limit).all()

    has_prev = page > 1
    has_next = page < total_pages
    prev_page = page - 1
    next_page = page + 1
    start_item = (page - 1) * limit + 1 if total_items > 0 else 0
    end_item = min(page * limit, total_items)

    channels = db.query(Channel).order_by(Channel.name.asc()).all()

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
            "page": page,
            "limit": limit,
            "sort_by": sort_by_clean,
            "order": order_clean,
            "q": q or "",
            "channel_id": channel_id or "",
            "source_video_id": source_video_id or "",
            "ai_provider": ai_provider or "",
            "channels": channels,
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


@router.get("/api/video/{clip_id}", summary="Stream rendered Short video")
def stream_clip_video(
    clip_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    from shorts_engine.services.storage.path_manager import PathManager
    file_path = PathManager.resolve_clip_path(clip)
    if not file_path or not file_path.is_file() or file_path.stat().st_size == 0:
        raise HTTPException(status_code=404, detail=f"Rendered video file not found on disk for clip {clip_id}")

    return FileResponse(
        path=str(file_path),
        media_type="video/mp4",
        filename=file_path.name,
    )


@router.post("/queue/{clip_id}/approve", summary="Approve a clip for publishing")
def approve_clip(
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
    if clip.status != ClipStatus.WAITING_APPROVAL:
        raise HTTPException(
            status_code=409,
            detail=f"Clip is in status {clip.status.value}, not WAITING_APPROVAL",
        )
    clip.status = ClipStatus.APPROVED
    db.commit()
    logger.info("Operator '%s' approved clip %s for publishing", current_user.username, clip_id)

    if request.headers.get("hx-request") == "true":
        undo_html = f"Clip {clip_id[:8]} approved. <button class='btn btn-sm btn-dark py-0 ms-2' hx-post='/queue/{clip_id}/undo' hx-swap='none'>Undo</button>"
        return Response(
            content="",
            headers={"HX-Trigger": json.dumps({"showToast": {"message": undo_html, "type": "success"}, "clipApproved": clip_id})}
        )

    if return_query or request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return _make_queue_redirect(
            request,
            default_msg=f"Clip {clip_id[:8]} approved for publishing.",
            msg_type="success",
            return_query=return_query,
        )
    return {"clip_id": clip_id, "new_status": ClipStatus.APPROVED.value}


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
    clip.status = ClipStatus.WAITING_APPROVAL
    db.commit()
    logger.info("Operator '%s' undid action for clip %s", current_user.username, clip_id)
    return Response(
        content="",
        headers={
            "HX-Trigger": json.dumps({
                "showToast": {"message": f"Action undone for {clip_id[:8]}. Refresh page to see clip.", "type": "info"},
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

    clip.scheduled_publish_at = dt
    clip.status = ClipStatus.APPROVED
    db.commit()
    logger.info("Operator '%s' scheduled clip %s for %s", current_user.username, clip_id, dt.isoformat())

    dt_display = dt.strftime("%Y-%m-%d %H:%M")
    if request.headers.get("hx-request") == "true":
        return Response(
            content="",
            headers={
                "HX-Trigger": json.dumps({
                    "showToast": {
                        "message": f"Clip {clip_id[:8]} scheduled for {dt_display}!",
                        "type": "success"
                    },
                    "clipApproved": clip_id
                })
            }
        )

    return _make_queue_redirect(
        request,
        default_msg=f"Clip {clip_id[:8]} scheduled for publishing at {dt_display}.",
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
    return_query: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if clip.status != ClipStatus.WAITING_APPROVAL:
        raise HTTPException(
            status_code=409,
            detail=f"Clip is in status {clip.status.value}, not WAITING_APPROVAL",
        )
    clip.status = ClipStatus.REJECTED
    if reason:
        existing = json.loads(clip.quality_check_result or "{}")
        existing["rejection_reason"] = reason
        clip.quality_check_result = json.dumps(existing)
    db.commit()
    logger.info("Operator '%s' rejected clip %s (reason: %s)", current_user.username, clip_id, reason)

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
    return {"clip_id": clip_id, "new_status": ClipStatus.REJECTED.value, "reason": reason}


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


@router.post("/api/clip/{clip_id}/open-folder", summary="Open folder containing clip in native file explorer")
def open_clip_folder(
    clip_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    clip = db.query(Clip).filter_by(id=clip_id).first()
    if not clip or not clip.rendered_path:
        raise HTTPException(status_code=404, detail="Clip or rendered file not found")
    from shorts_engine.services.quality_gate.recovery import ClipRecoveryService
    return ClipRecoveryService.open_in_folder(Path(clip.rendered_path))


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
    return_query: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Invalid action; must be 'approve' or 'reject'")

    count = 0
    target_status = ClipStatus.APPROVED if action == "approve" else ClipStatus.REJECTED
    for cid in clip_ids:
        c = db.query(Clip).filter_by(id=cid, status=ClipStatus.WAITING_APPROVAL).first()
        if c:
            c.status = target_status
            count += 1
    db.commit()
    logger.info("Operator '%s' performed bulk %s on %d clips", current_user.username, action, count)

    msg = f"Bulk {action} completed for {count} clip(s)."
    if return_query or request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return _make_queue_redirect(
            request,
            default_msg=msg,
            msg_type="success",
            return_query=return_query,
        )
    return {"action": action, "count": count}


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
    return_query: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
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
    if template_id and template_id in ("hormozi", "mrbeast", "neon", "minimalist", "high_contrast_box"):
        tmpl = get_caption_template(template_id)
        clip.caption_style = json.dumps(tmpl.to_ui_dict())
        style_overrides = tmpl.to_ass_overrides()
    elif font_name or primary_hex or highlight_hex:
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
        }
        tmpl = parse_custom_template(custom_dict)
        clip.caption_style = json.dumps(tmpl.to_ui_dict())
        style_overrides = tmpl.to_ass_overrides()
    else:
        tmpl = get_caption_template(settings.DEFAULT_CAPTION_PRESET)
        clip.caption_style = json.dumps(tmpl.to_ui_dict())
        style_overrides = tmpl.to_ass_overrides()

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

    # Re-generate ASS subtitle
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
    )


    clip.rendered_path = str(rendered_file)
    db.commit()

    logger.info("Operator '%s' re-rendered clip %s with style %s", current_user.username, clip_id, tmpl.name)
    msg = f"Re-rendered clip {clip_id[:8]} with '{tmpl.name}' style."
    if return_query or request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return _make_queue_redirect(request, default_msg=msg, msg_type="success", return_query=return_query)
    return {"status": "re_rendered", "clip_id": clip_id, "rendered_path": clip.rendered_path, "style": tmpl.name}


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
            headers={"HX-Trigger": json.dumps({"showToast": {"message": f"Clip dismissed.", "type": "info"}, "itemDeleted": clip_id})}
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
            headers={"HX-Trigger": json.dumps({"showToast": {"message": f"Video dismissed.", "type": "info"}, "itemDeleted": yt_id})}
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
def clear_all_failed_get():
    return RedirectResponse(url="/dead-letter", status_code=303)
