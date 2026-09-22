"""
Channel management routes: list channels, toggle watch/review settings,
and manually trigger RSS poll or WebSub subscription for a channel.
Protected by multi-user RBAC and CSRF tokens.
"""

import json
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path

from shorts_engine.dashboard.auth import require_operator, require_viewer, verify_csrf, get_csrf_token
from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import Channel, User

import re
from shorts_engine.config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["settings"] = settings


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/channels", response_class=HTMLResponse, summary="Channel manager")
def channel_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    channels = db.query(Channel).order_by(Channel.created_at.asc()).all()
    cb_url = settings.WEBSUB_CALLBACK_URL or ""
    is_websub_public = bool(cb_url and not any(loc in cb_url.lower() for loc in ("localhost", "127.0.0.1", "0.0.0.0", "testserver")))

    return templates.TemplateResponse(
        request=request,
        name="channels.html",
        context={
            "channels": channels,
            "current_user": current_user,
            "csrf_token": get_csrf_token(request),
            "websub_public": is_websub_public,
            "websub_url": cb_url,
            "rss_interval": settings.RSS_POLL_INTERVAL_MINUTES,
        },
    )


@router.get("/api/channels", summary="Channel list JSON API")
def api_channel_list(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    channels = db.query(Channel).all()
    return [
        {
            "id": ch.id,
            "youtube_channel_id": ch.youtube_channel_id,
            "name": ch.name,
            "watch_enabled": ch.watch_enabled,
            "human_review_required": ch.human_review_required,
            "last_seen_video_id": ch.last_seen_video_id,
        }
        for ch in channels
    ]


@router.post("/channels/{channel_id}/toggle-watch", summary="Enable or disable channel watching")
def toggle_watch(
    request: Request,
    channel_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    channel = db.query(Channel).filter_by(id=channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    channel.watch_enabled = not channel.watch_enabled
    db.commit()
    logger.info("Operator '%s' set watch_enabled=%s for channel %s (%s)", current_user.username, channel.watch_enabled, channel.name, channel.youtube_channel_id)
    if request.headers.get("hx-request") == "true":
        watch_text = "ON" if channel.watch_enabled else "OFF"
        btn_class = "btn-outline-success" if channel.watch_enabled else "btn-outline-secondary"
        csrf = get_csrf_token(request)
        form_html = (
            f'<form method="post" action="/channels/{channel.id}/toggle-watch" class="m-0" '
            f'hx-post="/channels/{channel.id}/toggle-watch" hx-swap="outerHTML">'
            f'<input type="hidden" name="csrf_token" value="{csrf}">'
            f'<button type="submit" class="btn btn-sm {btn_class} text-nowrap py-1 px-2" style="font-size: 0.75rem;" title="Toggle background video polling">'
            f'Watch: {watch_text}</button></form>'
        )
        return Response(
            content=form_html,
            headers={"HX-Trigger": json.dumps({"showToast": {"message": f"Channel watch set to {watch_text}", "type": "info"}})}
        )
    if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return RedirectResponse(url="/channels", status_code=303)
    return {"channel_id": channel_id, "watch_enabled": channel.watch_enabled}


@router.post("/channels/{channel_id}/toggle-review", summary="Toggle human review requirement")
def toggle_review(
    request: Request,
    channel_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    channel = db.query(Channel).filter_by(id=channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    channel.human_review_required = not channel.human_review_required
    db.commit()
    logger.info("Operator '%s' set human_review_required=%s for channel %s (%s)", current_user.username, channel.human_review_required, channel.name, channel.youtube_channel_id)
    if request.headers.get("hx-request") == "true":
        rev_text = "REQ" if channel.human_review_required else "AUTO"
        btn_class = "btn-outline-warning" if channel.human_review_required else "btn-outline-secondary"
        csrf = get_csrf_token(request)
        form_html = (
            f'<form method="post" action="/channels/{channel.id}/toggle-review" class="m-0" '
            f'hx-post="/channels/{channel.id}/toggle-review" hx-swap="outerHTML">'
            f'<input type="hidden" name="csrf_token" value="{csrf}">'
            f'<button type="submit" class="btn btn-sm {btn_class} text-nowrap py-1 px-2" style="font-size: 0.75rem;" title="Toggle human review requirement">'
            f'Review: {rev_text}</button></form>'
        )
        return Response(
            content=form_html,
            headers={"HX-Trigger": json.dumps({"showToast": {"message": f"Channel review set to {rev_text}", "type": "info"}})}
        )
    if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return RedirectResponse(url="/channels", status_code=303)
    return {
        "channel_id": channel_id,
        "human_review_required": channel.human_review_required,
    }


@router.post("/channels/{channel_id}/trigger-poll", summary="Manually trigger RSS poll")
def trigger_poll(
    request: Request,
    channel_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    """Immediately runs one RSS poll cycle for the specified channel."""
    from shorts_engine.services.detection.rss_poller import RSSPoller

    channel = db.query(Channel).filter_by(id=channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    poller = RSSPoller()
    new_ids = poller.poll_channel(channel, db)
    logger.info("Operator '%s' triggered RSS poll for channel %s (%s): found %s new videos", current_user.username, channel.name, channel.youtube_channel_id, len(new_ids))
    if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return RedirectResponse(url="/channels", status_code=303)
    return {
        "channel_id": channel_id,
        "youtube_channel_id": channel.youtube_channel_id,
        "new_videos_detected": new_ids,
    }


@router.post("/channels/{channel_id}/subscribe-websub", summary="Register WebSub subscription")
def subscribe_websub(
    request: Request,
    channel_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    """Registers or renews a WebSub subscription for the channel."""
    from shorts_engine.services.detection.websub import WebSubSubscriber

    channel = db.query(Channel).filter_by(id=channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    subscriber = WebSubSubscriber()
    success = subscriber.subscribe(channel.youtube_channel_id)
    logger.info("Operator '%s' triggered WebSub subscribe for channel %s (%s): success=%s", current_user.username, channel.name, channel.youtube_channel_id, success)
    if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return RedirectResponse(url="/channels", status_code=303)
    return {
        "channel_id": channel_id,
        "youtube_channel_id": channel.youtube_channel_id,
        "subscribed": success,
    }


@router.post("/channels/add", summary="Add a new YouTube channel")
def add_channel(
    request: Request,
    youtube_channel_id: str = Form(...),
    name: str = Form(...),
    human_review: Optional[str] = Form(default=None),
    only_new_uploads: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    raw_input = youtube_channel_id.strip()
    if not raw_input:
        raise HTTPException(status_code=422, detail="YouTube Channel ID cannot be empty")

    channel_id_clean = raw_input

    # 1. URL extraction
    if "youtube.com/channel/" in channel_id_clean:
        channel_id_clean = channel_id_clean.split("youtube.com/channel/")[1].split("/")[0].split("?")[0].strip()
    elif "youtube.com/@" in channel_id_clean or channel_id_clean.startswith("@"):
        raise HTTPException(
            status_code=422,
            detail="Handles (like @ChannelName) cannot be queried directly via YouTube Atom feeds. Please provide the 24-character Channel ID starting with 'UC' (found in YouTube Studio > Settings > Channel > Advanced settings).",
        )

    # 2. Strict UC format check
    if not re.match(r"^UC[a-zA-Z0-9_-]{22}$", channel_id_clean):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid YouTube Channel ID '{channel_id_clean}'. "
                "Channel IDs must begin with 'UC' followed by exactly 22 characters (24 characters total, e.g. 'UC_x5XG1OV2P6uZZ5FSM9Ttw')."
            ),
        )

    existing = db.query(Channel).filter_by(youtube_channel_id=channel_id_clean).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Channel {channel_id_clean} is already registered")

    last_seen = None
    if only_new_uploads and only_new_uploads.lower() in ("on", "true", "1", "yes"):
        try:
            from shorts_engine.services.detection.rss_poller import RSSPoller
            poller = RSSPoller()
            feed_items = poller.fetch_feed(channel_id_clean)
            if feed_items:
                last_seen = feed_items[0][0]
                logger.info("Skip backfill enabled for channel %s: initialized last_seen_video_id to %s", channel_id_clean, last_seen)
        except Exception as e:
            logger.warning("Could not pre-fetch latest video for channel %s: %s", channel_id_clean, e)

    channel = Channel(
        youtube_channel_id=channel_id_clean,
        name=name.strip(),
        watch_enabled=True,
        human_review_required=(human_review is not None and human_review.lower() in ("on", "true", "1", "yes")),
        last_seen_video_id=last_seen,
    )
    db.add(channel)
    db.commit()
    logger.info("Operator '%s' registered new channel: %s (%s, last_seen=%s)", current_user.username, channel.name, channel.youtube_channel_id, last_seen)
    if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return RedirectResponse(url="/channels", status_code=303)
    return {"channel_id": channel.id, "created": True, "last_seen_video_id": last_seen}


@router.post("/channels/{channel_id}/delete", summary="Delete a YouTube channel")
def delete_channel(
    request: Request,
    channel_id: str,
    delete_files: bool = Form(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    channel = db.query(Channel).filter_by(id=channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    ch_name = channel.name
    ch_yt_id = channel.youtube_channel_id

    if delete_files:
        import shutil
        from shorts_engine.services.storage.path_manager import PathManager

        # Wipe download & output folder for channel if exists
        ch_slug = PathManager.slugify(ch_name)
        dl_chan_dir = settings.STORAGE_DOWNLOADS / ch_slug
        if dl_chan_dir.exists():
            try:
                shutil.rmtree(dl_chan_dir, ignore_errors=True)
                logger.info("Purged download directory for channel %s: %s", ch_name, dl_chan_dir)
            except Exception as e:
                logger.warning("Could not delete channel download dir %s: %s", dl_chan_dir, e)

        out_chan_dir = settings.STORAGE_OUTPUTS / ch_slug
        if out_chan_dir.exists():
            try:
                shutil.rmtree(out_chan_dir, ignore_errors=True)
                logger.info("Purged output directory for channel %s: %s", ch_name, out_chan_dir)
            except Exception as e:
                logger.warning("Could not delete channel output dir %s: %s", out_chan_dir, e)

        # Unlink legacy flat files if present
        for v in channel.source_videos:
            for p in [v.downloaded_path, v.audio_path]:
                if p:
                    PathManager.safe_unlink(p)
            for cand in v.candidates:
                if cand.clip and cand.clip.rendered_path:
                    PathManager.safe_unlink(cand.clip.rendered_path)

    db.delete(channel)
    db.commit()
    logger.info("Operator '%s' deleted channel: %s (%s, delete_files=%s)", current_user.username, ch_name, ch_yt_id, delete_files)

    if request.headers.get("hx-request") == "true":
        return Response(
            content="",
            headers={"HX-Trigger": json.dumps({"showToast": {"message": f"Channel '{ch_name}' deleted.", "type": "info"}, "channelDeleted": channel_id})}
        )

    if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return RedirectResponse(url="/channels", status_code=303)
    return {"channel_id": channel_id, "deleted": True, "files_deleted": delete_files}


@router.get("/api/channels/{channel_id}/videos", summary="Fetch public videos for a channel with ingestion status")
def api_channel_videos(
    channel_id: str,
    query: Optional[str] = None,
    limit: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    channel = db.query(Channel).filter(
        (Channel.id == channel_id) | (Channel.youtube_channel_id == channel_id)
    ).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    from shorts_engine.services.detection.rss_poller import RSSPoller, FeedEntry
    poller = RSSPoller(timeout_sec=8)
    entries = poller.fetch_feed(channel.youtube_channel_id)

    # Fallback to yt-dlp flat extraction if RSS returns empty (e.g. non-UC ID or handle)
    if not entries:
        try:
            import yt_dlp
            from datetime import datetime, timezone
            chan_url = (
                f"https://www.youtube.com/channel/{channel.youtube_channel_id}/videos"
                if channel.youtube_channel_id.startswith("UC")
                else f"https://www.youtube.com/{channel.youtube_channel_id}/videos"
            )
            ydl_opts = {
                "quiet": True,
                "extract_flat": True,
                "playlistend": limit,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(chan_url, download=False)
                if info and "entries" in info:
                    for item in info["entries"]:
                        vid = item.get("id")
                        title = item.get("title", "")
                        if vid:
                            entries.append(FeedEntry(vid, datetime.now(tz=timezone.utc), title=title))
        except Exception as e:
            logger.warning("yt-dlp flat fetch fallback failed for channel %s: %s", channel.name, e)

    # Reconcile with database source_videos
    from shorts_engine.db.models import SourceVideo
    all_source_vids = {
        v.youtube_video_id: v
        for v in db.query(SourceVideo).filter_by(channel_id=channel.id).all()
    }
    extra_vids = {
        v.youtube_video_id: v
        for v in db.query(SourceVideo).filter(SourceVideo.youtube_video_id.in_([e[0] for e in entries])).all()
    }
    all_source_vids.update(extra_vids)

    # If entries was empty from RSS/yt-dlp, populate from existing DB videos for this channel
    if not entries and all_source_vids:
        from datetime import timezone
        for v in all_source_vids.values():
            pub_dt = v.created_at or datetime.now(tz=timezone.utc)
            entries.append(FeedEntry(v.youtube_video_id, pub_dt, title=v.title or v.youtube_video_id))

    results = []
    q_clean = query.strip().lower() if query else None

    for item in entries[:limit]:
        vid = item[0]
        title = getattr(item, "title", f"Video {vid}")
        pub = item[1].strftime("%Y-%m-%d %H:%M") if hasattr(item[1], "strftime") else str(item[1])

        if q_clean and q_clean not in title.lower() and q_clean not in vid.lower():
            continue

        existing = all_source_vids.get(vid)
        results.append({
            "video_id": vid,
            "title": title or vid,
            "published_at": pub,
            "thumbnail_url": f"/api/thumbnails/{vid}",
            "is_ingested": bool(existing),
            "status": existing.status.value if existing else None,
            "status_label": existing.status.value if existing else "NOT INGESTED",
            "clips_count": len(existing.candidates) if existing else 0,
            "queue_url": f"/queue?tab=active&source_video_id={vid}" if existing else None,
        })

    return {
        "channel_id": channel.id,
        "channel_name": channel.name,
        "youtube_channel_id": channel.youtube_channel_id,
        "count": len(results),
        "videos": results,
    }
