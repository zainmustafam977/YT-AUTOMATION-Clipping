"""
Universal Local Video Ingestion Gateway API Routes.
Exposes multipart chunked streaming upload (/api/ingest/local-file)
and zero-copy disk mapping (/api/ingest/local-path).
Protected by multi-user RBAC and CSRF verification.
"""

import json
import logging
from typing import Optional, Dict, Any
from pathlib import Path
from pydantic import BaseModel, Field

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    UploadFile,
    File,
    Form,
    status,
)
from sqlalchemy.orm import Session

from shorts_engine.config.settings import settings
from shorts_engine.db.models import User
from shorts_engine.dashboard.auth import get_db, require_operator, verify_csrf
from shorts_engine.services.ingestion.local_loader import LocalVideoIngestionService
from shorts_engine.services.ingestion.oauth_flow import YouTubeDeviceOAuth
from shorts_engine.services.ingestion.session_harvester import SessionHarvester
from shorts_engine.services.ingestion.proxy_manager import ProxyManager
from shorts_engine.services.ingestion.hotfolder_watcher import hotfolder_watcher

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingest", tags=["Ingestion"])


class LocalPathIngestRequest(BaseModel):
    file_path: str = Field(..., description="Absolute or relative path to the local video file on host")
    channel_id: Optional[str] = Field(None, description="Optional target Channel UUID")
    title: Optional[str] = Field(None, description="Optional custom video title")
    runtime_overrides: Optional[Dict[str, Any]] = Field(None, description="Per-video pipeline overrides")


class LocalPathValidateRequest(BaseModel):
    file_path: str = Field(..., description="Path to check and probe")


from shorts_engine.dashboard.rate_limit import rate_limit_ingestion

@router.post(
    "/local-path",
    summary="Zero-copy ingestion of an existing video file on local host filesystem",
    status_code=status.HTTP_201_CREATED,
)
def ingest_local_path_endpoint(
    payload: LocalPathIngestRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    rate_limit_user: User = Depends(rate_limit_ingestion),
    csrf_ok: bool = Depends(verify_csrf),
):
    """
    Ingests a video file already located on the local disk.
    Performs zero-copy registration to avoid duplicating large media files across directories.
    """
    clean_path = payload.file_path.strip().strip('"').strip("'")
    if not clean_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Local file path cannot be empty.",
        )

    try:
        source_video = LocalVideoIngestionService.ingest_local_path(
            db=db,
            file_path=clean_path,
            channel_id=payload.channel_id,
            title=payload.title,
            runtime_overrides=payload.runtime_overrides,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Failed to ingest local video path '%s': %s", clean_path, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Local ingestion error: {e}",
        )

    return {
        "status": "success",
        "message": f"Successfully ingested local video '{source_video.title}' with zero-copy mapping.",
        "source_video_id": source_video.id,
        "video_id": source_video.youtube_video_id,
        "title": source_video.title,
        "duration_sec": source_video.duration_sec,
        "downloaded_path": source_video.downloaded_path,
        "channel_id": source_video.channel_id,
    }


@router.post(
    "/local-file",
    summary="Multipart chunk-streamed video file upload for web drag-and-drop",
    status_code=status.HTTP_201_CREATED,
)
async def ingest_local_file_endpoint(
    request: Request,
    file: UploadFile = File(...),
    channel_id: Optional[str] = Form(default=None),
    title: Optional[str] = Form(default=None),
    runtime_overrides_json: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
    rate_limit_user: User = Depends(rate_limit_ingestion),
    csrf_ok: bool = Depends(verify_csrf),
):
    """
    Accepts multipart chunked streaming uploads from web browser drag-and-drop.
    Streams directly to storage/downloads with atomic staging to prevent memory bloat.
    """
    if not file or not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No video file provided in multipart payload.",
        )

    ext = Path(file.filename).suffix.lower()
    if ext not in LocalVideoIngestionService.SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported video extension '{ext}'. Supported formats: {sorted(LocalVideoIngestionService.SUPPORTED_EXTENSIONS)}",
        )

    runtime_overrides = None
    if runtime_overrides_json:
        try:
            runtime_overrides = json.loads(runtime_overrides_json)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="runtime_overrides_json must be valid JSON.",
            )

    try:
        source_video = await LocalVideoIngestionService.ingest_uploaded_file(
            db=db,
            upload_file=file,
            channel_id=channel_id,
            title=title,
            runtime_overrides=runtime_overrides,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Failed to stream and ingest uploaded file '%s': %s", file.filename, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Streaming upload failed: {e}",
        )

    return {
        "status": "success",
        "message": f"Successfully uploaded and ingested video '{source_video.title}'.",
        "source_video_id": source_video.id,
        "video_id": source_video.youtube_video_id,
        "title": source_video.title,
        "duration_sec": source_video.duration_sec,
        "downloaded_path": source_video.downloaded_path,
        "channel_id": source_video.channel_id,
    }


@router.post(
    "/validate-path",
    summary="Pre-flight validation and metadata probe for a local video path",
)
def validate_local_path_endpoint(
    payload: LocalPathValidateRequest,
    current_user: User = Depends(require_operator),
):
    """
    Pre-flight probe endpoint allowing UI to validate local file existence and probe
    video specs before triggering ingestion.
    """
    clean_path = payload.file_path.strip().strip('"').strip("'")
    if not clean_path:
        return {"valid": False, "exists": False, "error": "Path cannot be empty"}

    try:
        canonical_path = LocalVideoIngestionService.validate_local_file(clean_path)
        meta = LocalVideoIngestionService.probe_video_metadata(canonical_path)
        width = meta.get("width", 0)
        height = meta.get("height", 0)
        codec = meta.get("codec", "unknown")
        format_name = meta.get("format_name", canonical_path.suffix.lstrip(".").upper())
        return {
            "valid": True,
            "exists": True,
            "is_file": True,
            "canonical_path": str(canonical_path),
            "filename": canonical_path.name,
            "size_bytes": meta.get("size_bytes", 0),
            "duration_sec": meta.get("duration_sec", 0.0),
            "width": width,
            "height": height,
            "resolution": f"{width}x{height}",
            "fps": meta.get("fps", 0.0),
            "codec": codec,
            "codec_name": codec,
            "format_name": format_name,
            "has_audio": meta.get("has_audio", False),
        }
    except FileNotFoundError as e:
        return {"valid": False, "exists": False, "error": str(e)}
    except ValueError as e:
        return {"valid": False, "exists": True, "error": str(e)}
    except Exception as e:
        return {"valid": False, "exists": False, "error": str(e)}


# ── Ingestion & Network Armor Hub API Endpoints (Pillars 1, 3, 4, 5) ──

class DeviceOAuthPollRequest(BaseModel):
    device_code: str = Field(..., description="Google RFC-8628 device code")


class ProxyTestRequest(BaseModel):
    proxy_url: Optional[str] = Field(None, description="Optional specific proxy URL to test")


class CookiesSaveRequest(BaseModel):
    cookies_content: str = Field(..., description="Raw Netscape format cookies text")


@router.post(
    "/oauth/start",
    summary="Initiate Google RFC-8628 Device Code authorization flow",
)
def start_device_oauth_endpoint(
    current_user: User = Depends(require_operator),
):
    """
    Requests device code and user verification code from Google OAuth2.
    Operator opens https://www.google.com/device and inputs the user_code.
    """
    try:
        data = YouTubeDeviceOAuth.initiate_device_auth()
        return {
            "status": "success",
            **data,
        }
    except Exception as e:
        logger.error("Failed to initiate device OAuth: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"OAuth initiation failed: {e}",
        )


@router.post(
    "/oauth/poll",
    summary="Poll Google Device Code token status",
)
def poll_device_oauth_endpoint(
    payload: DeviceOAuthPollRequest,
    current_user: User = Depends(require_operator),
):
    """
    Non-blocking poll for user confirmation.
    Saves encrypted token into vault upon successful authorization.
    """
    res = YouTubeDeviceOAuth.poll_device_token(device_code=payload.device_code)
    return res


@router.get(
    "/oauth/status",
    summary="Check current YouTube Device OAuth connection status",
)
def get_oauth_status_endpoint(
    current_user: User = Depends(require_operator),
):
    """Returns diagnostic status of YouTube Device OAuth session."""
    return YouTubeDeviceOAuth.get_status()


@router.post(
    "/oauth/revoke",
    summary="Revoke and disconnect active YouTube Device OAuth session",
)
def revoke_oauth_endpoint(
    current_user: User = Depends(require_operator),
):
    """Removes stored tokens from vault and revokes them with Google."""
    ok = YouTubeDeviceOAuth.revoke_oauth()
    return {
        "status": "success" if ok else "error",
        "message": "YouTube Device OAuth credentials unlinked successfully." if ok else "Could not delete token vault file.",
    }


@router.post(
    "/harvester/run",
    summary="Trigger immediate headless session harvest",
)
def run_harvester_endpoint(
    current_user: User = Depends(require_operator),
):
    """Runs a fresh visitor session harvest, exporting fresh Netscape cookies."""
    try:
        res = SessionHarvester.run_harvest_job()
        return res
    except Exception as e:
        logger.error("Session harvest failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Harvester job failed: {e}",
        )


@router.get(
    "/harvester/status",
    summary="Get status and freshness of harvested cookies",
)
def get_harvester_status_endpoint(
    current_user: User = Depends(require_operator),
):
    return SessionHarvester.get_status()


@router.get(
    "/cookies/status",
    summary="Get status of manual cookies vault (storage/cookies.txt)",
)
def get_cookies_status_endpoint(
    current_user: User = Depends(require_operator),
):
    cookies_path = getattr(settings, "YOUTUBE_COOKIES_PATH", None) or (settings.STORAGE_DIR / "cookies.txt")
    if not cookies_path.exists() or cookies_path.stat().st_size == 0:
        return {
            "exists": False,
            "path": str(cookies_path),
            "cookies_count": 0,
            "message": "No manual cookies.txt found in storage vault.",
        }

    count = 0
    try:
        with open(cookies_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip() and not line.strip().startswith("#"):
                    count += 1
    except Exception:
        pass

    return {
        "exists": True,
        "path": str(cookies_path),
        "size_bytes": cookies_path.stat().st_size,
        "cookies_count": count,
    }


@router.post(
    "/cookies/save",
    summary="Save or update manual Netscape cookies vault",
)
def save_cookies_endpoint(
    payload: CookiesSaveRequest,
    current_user: User = Depends(require_operator),
):
    raw_content = payload.cookies_content.strip()
    if not raw_content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cookies content cannot be empty.")

    cookies_path = getattr(settings, "YOUTUBE_COOKIES_PATH", None) or (settings.STORAGE_DIR / "cookies.txt")
    cookies_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cookies_path, "w", encoding="utf-8") as f:
        f.write(raw_content + "\n")

    return {
        "status": "success",
        "message": f"Saved manual cookies to {cookies_path.name}.",
        "path": str(cookies_path),
    }


@router.post(
    "/proxy/test",
    summary="Test network egress ping and latency through proxy mesh",
)
def test_proxy_endpoint(
    payload: Optional[ProxyTestRequest] = None,
    current_user: User = Depends(require_operator),
):
    if payload and payload.proxy_url:
        return ProxyManager.test_proxy(payload.proxy_url)
    return {
        "results": ProxyManager.test_all_proxies(),
        "status": ProxyManager.get_status(),
    }


@router.get(
    "/proxy/status",
    summary="Get status of proxy pool mesh and security posture",
)
def get_proxy_status_endpoint(
    current_user: User = Depends(require_operator),
):
    return ProxyManager.get_status()


@router.get(
    "/hotfolder/status",
    summary="Get automated hotfolder watcher status and pending drops",
)
def get_hotfolder_status_endpoint(
    current_user: User = Depends(require_operator),
):
    return hotfolder_watcher.get_status()


@router.post(
    "/hotfolder/scan",
    summary="Trigger an immediate scan and ingestion of pending hotfolder files",
)
def trigger_hotfolder_scan_endpoint(
    current_user: User = Depends(require_operator),
):
    ingested = hotfolder_watcher.scan_once()
    return {
        "status": "success",
        "ingested_count": len(ingested),
        "ingested_titles": [v.title for v in ingested if hasattr(v, "title")],
    }
