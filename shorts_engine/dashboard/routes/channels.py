"""
Channel management routes: list channels, toggle watch/review settings,
and manually trigger RSS poll or WebSub subscription for a channel.
Protected by multi-user RBAC and CSRF tokens.
"""

import json
import logging
import concurrent.futures
from datetime import datetime, timezone
from typing import Optional
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Form, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path

from pydantic import BaseModel
from shorts_engine.dashboard.auth import require_operator, require_viewer, verify_csrf, get_csrf_token
from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import Channel, User, ChannelCredential, PendingOAuthMismatch
from shorts_engine.services.publishing.oauth_manager import ChannelOAuthManager

import re
from shorts_engine.config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter()
oauth_manager = ChannelOAuthManager()
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


class ChannelConnectPayload(BaseModel):
    channel_id: Optional[str] = None
    expected_handle: Optional[str] = None
    redirect_uri: Optional[str] = None


class ChannelResolveMismatchPayload(BaseModel):
    mismatch_id: str
    action: str  # connect_authorized, retry_intended, cancel


@router.get("/api/channels", summary="Channel list JSON API")
def api_channel_list(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    channels = db.query(Channel).order_by(Channel.name.asc()).all()
    return [
        {
            "id": ch.id,
            "youtube_channel_id": ch.youtube_channel_id,
            "name": ch.name,
            "title": ch.title or ch.name,
            "handle": ch.handle or "",
            "avatar_url": ch.avatar_url or "",
            "last_verified_at": ch.last_verified_at.isoformat() if ch.last_verified_at else None,
            "is_connected": bool(ch.credential or ch.encrypted_oauth_token),
            "watch_enabled": ch.watch_enabled,
            "human_review_required": ch.human_review_required,
            "last_seen_video_id": ch.last_seen_video_id,
        }
        for ch in channels
    ]


@router.post("/api/channels/connect", summary="Initiate identity-first OAuth connect flow")
def connect_channel_oauth(
    payload: ChannelConnectPayload,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    eff_redirect = payload.redirect_uri or f"{str(request.base_url).rstrip('/')}/oauth2callback"
    try:
        res = oauth_manager.start_connect(
            target_channel_id=payload.channel_id,
            expected_handle=payload.expected_handle,
            redirect_uri=eff_redirect,
            db=db,
        )
        return res
    except Exception as e:
        logger.exception("Failed to initiate OAuth connect flow: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to initiate OAuth connect flow: {str(e)}")


@router.post("/api/channels/connect/resolve", summary="Resolve OAuth channel mismatch")
def resolve_channel_mismatch(
    payload: ChannelResolveMismatchPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    try:
        res = oauth_manager.resolve_mismatch(
            mismatch_id=payload.mismatch_id,
            action=payload.action,
            db=db,
        )
        return res
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.exception("Failed to resolve OAuth mismatch %s: %s", payload.mismatch_id, e)
        raise HTTPException(status_code=500, detail=f"Failed to resolve mismatch: {str(e)}")


@router.post("/api/channels/{channel_id}/disconnect", summary="Disconnect OAuth credentials for a channel")
def disconnect_channel_oauth(
    channel_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    channel = db.query(Channel).filter((Channel.id == channel_id) | (Channel.youtube_channel_id == channel_id)).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    try:
        res = oauth_manager.disconnect_channel(channel=channel, db=db)
        return {"ok": res, "channel_id": channel.id, "message": "Channel disconnected successfully"}
    except Exception as e:
        logger.exception("Failed to disconnect channel %s: %s", channel_id, e)
        raise HTTPException(status_code=500, detail=f"Failed to disconnect channel: {str(e)}")


@router.post("/api/channels/{channel_id}/verify", summary="Verify channel credentials and identity before upload")
def verify_channel_identity_endpoint(
    channel_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    channel = db.query(Channel).filter((Channel.id == channel_id) | (Channel.youtube_channel_id == channel_id)).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    res = oauth_manager.verify_channel_token(channel, db=db)
    return {
        "ok": res.get("connected", False),
        "channel_id": channel.id,
        "actual_channel_id": res.get("actual_channel_id") or res.get("channel_id"),
        "actual_title": res.get("title"),
        "error": res.get("error"),
    }


@router.get("/api/channels/mismatches/pending", summary="List pending OAuth mismatches awaiting operator resolution")
def get_pending_mismatches(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    mismatches = (
        db.query(PendingOAuthMismatch)
        .filter(PendingOAuthMismatch.expires_at > now)
        .order_by(PendingOAuthMismatch.created_at.desc())
        .all()
    )
    result = []
    for m in mismatches:
        intended_ch = db.query(Channel).filter_by(id=m.intended_channel_id).first() if m.intended_channel_id else None
        result.append({
            "id": m.id,
            "intended_channel_id": m.intended_channel_id,
            "intended_channel_name": intended_ch.name if intended_ch else (m.intended_channel_id or "New Channel"),
            "authorized_channel_id": m.authorized_channel_id,
            "authorized_title": m.authorized_title,
            "authorized_handle": m.authorized_handle,
            "authorized_avatar_url": m.authorized_avatar_url,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "expires_at": m.expires_at.isoformat() if m.expires_at else None,
        })
    return result


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


def _fetch_video_upload_date(video_id: str) -> Optional[datetime]:
    """Fetches exact upload/publish datetime from YouTube watch page schema metadata."""
    if not video_id:
        return None
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }
        with httpx.Client(timeout=4.0) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                m = re.search(r'itemprop=["\'](?:datePublished|uploadDate)["\'] content=["\']([^"\'\s>]+)', resp.text)
                if not m:
                    m = re.search(r'"(?:datePublished|uploadDate|publishDate)"\s*:\s*"([^"]+)"', resp.text)
                if m:
                    date_str = m.group(1).strip()
                    try:
                        dt = datetime.fromisoformat(date_str)
                        return dt.astimezone(timezone.utc)
                    except Exception:
                        pass
    except Exception as e:
        logger.debug("Could not resolve upload date for %s: %s", video_id, e)
    return None


def _format_duration(seconds: Optional[float]) -> Optional[str]:
    if seconds is None:
        return None
    try:
        s = int(round(float(seconds)))
    except (ValueError, TypeError):
        return None
    m, sec = divmod(s, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def _format_views(count: Optional[int]) -> Optional[str]:
    if count is None:
        return None
    try:
        c = int(count)
    except (ValueError, TypeError):
        return None
    if c >= 1_000_000_000:
        return f"{c / 1_000_000_000:.1f}B views"
    if c >= 1_000_000:
        return f"{c / 1_000_000:.1f}M views"
    if c >= 1_000:
        return f"{c / 1_000:.1f}K views"
    return f"{c} views"


def _format_relative_time(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(tz=timezone.utc)
    diff = now - dt
    total_seconds = int(diff.total_seconds())
    if total_seconds < 0:
        return "just now"
    if total_seconds < 60:
        return f"{total_seconds}s ago"
    minutes = total_seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days}d ago"
    weeks = days // 7
    if weeks < 4:
        return f"{weeks}w ago"
    months = days // 30
    if months < 12:
        return f"{months}mo ago"
    years = days // 365
    return f"{years}y ago"


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
                    raw_items = [it for it in info["entries"][:limit] if it.get("id")]
                    need_date_vids = [
                        it.get("id") for it in raw_items
                        if not (it.get("timestamp") or it.get("upload_date"))
                    ]

                    fetched_dates = {}
                    if need_date_vids:
                        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(need_date_vids))) as executor:
                            future_to_vid = {
                                executor.submit(_fetch_video_upload_date, vid): vid
                                for vid in need_date_vids
                            }
                            for future in concurrent.futures.as_completed(future_to_vid):
                                v_id = future_to_vid[future]
                                try:
                                    resolved_dt = future.result()
                                    if resolved_dt:
                                        fetched_dates[v_id] = resolved_dt
                                except Exception:
                                    pass

                    for item in raw_items:
                        vid = item.get("id")
                        title = item.get("title", "")

                        # Extract exact upload/publish datetime
                        pub_dt = None
                        ts = item.get("timestamp") or item.get("release_timestamp")
                        if ts:
                            try:
                                pub_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                            except Exception:
                                pass
                        if not pub_dt and item.get("upload_date"):
                            try:
                                ud = str(item["upload_date"])
                                if len(ud) == 8:
                                    pub_dt = datetime(
                                        int(ud[:4]), int(ud[4:6]), int(ud[6:8]),
                                        12, 0, 0, tzinfo=timezone.utc
                                    )
                            except Exception:
                                pass
                        if not pub_dt:
                            pub_dt = fetched_dates.get(vid)

                        duration = item.get("duration")
                        views = item.get("view_count")
                        desc = item.get("description", "")
                        thumb = item.get("thumbnail") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"

                        entries.append(FeedEntry(
                            vid,
                            pub_dt,
                            title=title,
                            duration_sec=duration,
                            view_count=views,
                            description=desc,
                            thumbnail_url=thumb,
                        ))
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
        for v in all_source_vids.values():
            meta = v.metadata_json or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}

            pub_dt = None
            if meta.get("timestamp"):
                try:
                    pub_dt = datetime.fromtimestamp(meta["timestamp"], tz=timezone.utc)
                except Exception:
                    pass
            if not pub_dt and meta.get("upload_date"):
                try:
                    ud = str(meta["upload_date"])
                    if len(ud) == 8:
                        pub_dt = datetime(
                            int(ud[:4]), int(ud[4:6]), int(ud[6:8]),
                            12, 0, 0, tzinfo=timezone.utc
                        )
                except Exception:
                    pass
            if not pub_dt:
                pub_dt = v.created_at

            entries.append(FeedEntry(
                v.youtube_video_id,
                pub_dt,
                title=v.title or v.youtube_video_id,
                duration_sec=v.duration_sec or meta.get("duration"),
                view_count=meta.get("view_count"),
                description=meta.get("description", ""),
                thumbnail_url=f"/api/thumbnails/{v.youtube_video_id}",
            ))

    results = []
    q_clean = query.strip().lower() if query else None

    for item in entries[:limit]:
        vid = item[0]
        title = getattr(item, "title", f"Video {vid}")
        pub_dt = item[1]
        existing = all_source_vids.get(vid)
        if not pub_dt and existing:
            pub_dt = existing.created_at

        if isinstance(pub_dt, datetime):
            pub = pub_dt.strftime("%Y-%m-%d %H:%M")
            rel_time = _format_relative_time(pub_dt)
        elif pub_dt:
            pub = str(pub_dt)
            rel_time = ""
        else:
            pub = "Date unknown"
            rel_time = ""

        if q_clean and q_clean not in title.lower() and q_clean not in vid.lower():
            continue

        existing = all_source_vids.get(vid)
        dur_sec = getattr(item, "duration_sec", None)
        if dur_sec is None and existing and existing.duration_sec:
            dur_sec = existing.duration_sec

        views = getattr(item, "view_count", None)

        results.append({
            "video_id": vid,
            "title": title or vid,
            "published_at": pub,
            "published_relative": rel_time,
            "duration_sec": dur_sec,
            "duration_formatted": _format_duration(dur_sec),
            "view_count": views,
            "views_formatted": _format_views(views),
            "youtube_url": f"https://www.youtube.com/watch?v={vid}",
            "thumbnail_url": getattr(item, "thumbnail_url", f"/api/thumbnails/{vid}") or f"/api/thumbnails/{vid}",
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


@router.get("/api/channels/{channel_id}/setup", summary="Get channel upload setup & defaults")
def get_channel_setup(
    channel_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    channel = db.query(Channel).filter_by(id=channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    return {
        "channel_id": channel.id,
        "name": channel.name,
        "youtube_channel_id": channel.youtube_channel_id,
        "publish_target_channel_id": channel.publish_target_channel_id,
        "human_review_required": channel.human_review_required,
        "has_oauth_token": bool(channel.encrypted_oauth_token),
        "upload_defaults": channel.upload_defaults,
    }


@router.post("/channels/{channel_id}/setup", summary="Update channel upload setup from form")
def update_channel_setup_form(
    request: Request,
    channel_id: str,
    privacy_status: str = Form(default="unlisted"),
    made_for_kids: Optional[str] = Form(default=None),
    category_id: str = Form(default="22"),
    default_tags: str = Form(default="#Shorts, #Viral, #Trending"),
    default_language: str = Form(default="en"),
    license: str = Form(default="youtube"),
    daily_upload_quota: int = Form(default=4),
    preferred_publish_hour_utc: int = Form(default=18),
    auto_publish: Optional[str] = Form(default=None),
    publish_target_channel_id: Optional[str] = Form(default=None),
    oauth_token_json: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    channel = db.query(Channel).filter_by(id=channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    is_kids = bool(made_for_kids and made_for_kids.lower() in ("on", "true", "1", "yes"))
    is_auto = bool(auto_publish and auto_publish.lower() in ("on", "true", "1", "yes"))
    tag_list = [t.strip() for t in default_tags.split(",") if t.strip()]

    new_defaults = {
        "privacy_status": privacy_status.strip().lower(),
        "made_for_kids": is_kids,
        "category_id": str(category_id).strip(),
        "default_tags": tag_list,
        "default_language": default_language.strip().lower(),
        "license": license.strip().lower(),
        "daily_upload_quota": max(1, min(int(daily_upload_quota), 50)),
        "preferred_publish_hour_utc": max(0, min(int(preferred_publish_hour_utc), 23)),
        "auto_publish": is_auto,
    }
    channel.upload_defaults = new_defaults
    channel.human_review_required = not is_auto

    if publish_target_channel_id is not None:
        channel.publish_target_channel_id = publish_target_channel_id.strip() or None

    if oauth_token_json and oauth_token_json.strip():
        from shorts_engine.db.security import encrypt_secret
        channel.encrypted_oauth_token = encrypt_secret(oauth_token_json.strip())
        logger.info("Updated encrypted OAuth token for channel %s (%s)", channel.name, channel.youtube_channel_id)

    db.commit()
    logger.info("Operator '%s' updated upload configuration for channel %s (%s)", current_user.username, channel.name, channel.youtube_channel_id)

    if request.headers.get("sec-fetch-dest") == "document" or ("text/html" in request.headers.get("accept", "") and "application/json" not in request.headers.get("accept", "")):
        return RedirectResponse(
            url=f"/channels?msg=Upload+settings+for+{channel.name}+updated+successfully!&type=success",
            status_code=303,
        )
    return {"status": "success", "channel_id": channel.id, "upload_defaults": new_defaults}


@router.post("/api/channels/{channel_id}/setup", summary="Update channel upload setup JSON API")
async def update_channel_setup_api(
    request: Request,
    channel_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    channel = db.query(Channel).filter_by(id=channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    body = await request.json()
    curr = dict(channel.upload_defaults)
    for k in ("privacy_status", "made_for_kids", "category_id", "default_tags", "default_language", "license", "daily_upload_quota", "preferred_publish_hour_utc", "auto_publish"):
        if k in body:
            curr[k] = body[k]

    channel.upload_defaults = curr
    if "auto_publish" in body:
        channel.human_review_required = not bool(body["auto_publish"])
    if "publish_target_channel_id" in body:
        channel.publish_target_channel_id = body["publish_target_channel_id"] or None
    if "oauth_token_json" in body and body["oauth_token_json"]:
        from shorts_engine.db.security import encrypt_secret
        channel.encrypted_oauth_token = encrypt_secret(body["oauth_token_json"].strip())

    db.commit()
    return {"status": "success", "channel_id": channel.id, "upload_defaults": curr}


@router.get("/api/channels/export", summary="Export monitored channels to JSON backup")
def export_channels_api(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    from shorts_engine.services.storage.backup_service import BackupService
    payload = BackupService.export_channels(db)
    content = json.dumps(payload, indent=2)
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="channels_backup.json"',
        },
    )


@router.post("/api/channels/import", summary="Restore monitored channels from JSON backup")
async def import_channels_api(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    from shorts_engine.services.storage.backup_service import BackupService
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {e}")

    update_existing = bool(body.get("update_existing", True)) if isinstance(body, dict) else True
    try:
        result = BackupService.import_channels(body, db, update_existing=update_existing)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Unhandled error during channel import: %s", e)
        raise HTTPException(status_code=500, detail=f"Channel import failed: {e}")


@router.post("/channels/import", summary="Restore channels from HTML form upload")
async def import_channels_form(
    request: Request,
    backup_file: Optional[UploadFile] = File(None),
    backup_json: Optional[str] = Form(None),
    update_existing: Optional[str] = Form(default="on"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    csrf_ok: bool = Depends(verify_csrf),
):
    import urllib.parse
    from shorts_engine.services.storage.backup_service import BackupService

    payload = None
    if backup_file and backup_file.filename:
        try:
            content = await backup_file.read()
            payload = json.loads(content.decode("utf-8"))
        except Exception as e:
            err = urllib.parse.quote(f"Could not parse uploaded file: {e}")
            return RedirectResponse(url=f"/channels?msg={err}&type=danger", status_code=303)
    elif backup_json and backup_json.strip():
        try:
            payload = json.loads(backup_json.strip())
        except Exception as e:
            err = urllib.parse.quote(f"Invalid JSON string: {e}")
            return RedirectResponse(url=f"/channels?msg={err}&type=danger", status_code=303)

    if not payload:
        err = urllib.parse.quote("No channel backup file or JSON provided.")
        return RedirectResponse(url=f"/channels?msg={err}&type=danger", status_code=303)

    is_update = bool(update_existing and update_existing.lower() in ("on", "true", "1", "yes"))
    try:
        res = BackupService.import_channels(payload, db, update_existing=is_update)
        msg = urllib.parse.quote(
            f"Channels restored successfully! Added: {res['imported']}, Updated: {res['updated']}, Skipped: {res['skipped']}."
        )
        return RedirectResponse(url=f"/channels?msg={msg}&type=success", status_code=303)
    except Exception as e:
        err = urllib.parse.quote(f"Channel restoration failed: {e}")
        return RedirectResponse(url=f"/channels?msg={err}&type=danger", status_code=303)


