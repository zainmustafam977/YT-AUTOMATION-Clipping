"""
Visibility and Operational Health Dashboard Routes.

Provides REST endpoints and a real-time monitoring panel displaying:
1. Queue depth per stage and age of oldest pending job
2. Active running jobs with worker locks and heartbeat
3. Quarantined dead-letter jobs with failure error details
4. Channel token health indicators and last successful publish
5. Daily quota units remaining from the Pacific-aware quota ledger
6. Media root storage capacity and disk usage
7. Jobs succeeded/failed in the rolling 24h window
8. Webhook alert evaluation trigger
"""

import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from shorts_engine.config.settings import settings
from shorts_engine.dashboard.auth import (
    require_viewer,
    require_operator,
    get_csrf_token,
)
from shorts_engine.db.session import get_db
from shorts_engine.db.models import User
from shorts_engine.services.monitoring.visibility import (
    get_full_visibility_snapshot,
    get_queue_depth_and_oldest_age,
    get_running_jobs,
    get_quarantined_jobs,
    get_channels_visibility,
    get_quota_visibility,
    get_media_root_disk_usage,
    get_jobs_summary_24h,
    evaluate_system_thresholds_and_alert,
)

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["settings"] = settings


@router.get("/api/visibility/overview", summary="Full operational visibility snapshot")
def api_visibility_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
) -> Dict[str, Any]:
    """Returns the complete aggregated operational health snapshot."""
    return get_full_visibility_snapshot(db)


@router.get("/api/visibility/queue", summary="Queue depth per stage and oldest job age")
def api_visibility_queue(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
) -> Dict[str, Any]:
    """Returns pending queue depth breakdown by stage and oldest job age."""
    return get_queue_depth_and_oldest_age(db)


@router.get("/api/visibility/running", summary="Active running jobs")
def api_visibility_running(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
) -> List[Dict[str, Any]]:
    """Returns currently running pipeline jobs with worker lock info."""
    return get_running_jobs(db)


@router.get("/api/visibility/quarantined", summary="Quarantined dead-letter jobs")
def api_visibility_quarantined(
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
) -> List[Dict[str, Any]]:
    """Returns quarantined jobs with their failure reasons (last_error)."""
    return get_quarantined_jobs(db, limit=limit)


@router.get("/api/visibility/channels", summary="Channel token health and last publish")
def api_visibility_channels(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
) -> List[Dict[str, Any]]:
    """Returns channel credential status and last successful video publication."""
    return get_channels_visibility(db)


@router.get("/api/visibility/quota", summary="Quota units remaining today")
def api_visibility_quota(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
) -> Dict[str, Any]:
    """Returns YouTube Data API quota spent and remaining today."""
    return get_quota_visibility(db)


@router.get("/api/visibility/storage", summary="Media root disk usage")
def api_visibility_storage(
    current_user: User = Depends(require_viewer),
) -> Dict[str, Any]:
    """Returns storage partition consumption and free space for media root."""
    return get_media_root_disk_usage()


@router.get("/api/visibility/jobs-24h", summary="Jobs succeeded and failed in last 24h")
def api_visibility_jobs_24h(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
) -> Dict[str, Any]:
    """Returns 24h rolling window job outcomes and completion rate."""
    return get_jobs_summary_24h(db)


@router.post("/api/visibility/alerts/check", summary="Evaluate thresholds and dispatch alerts")
def api_visibility_check_alerts(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
) -> Dict[str, Any]:
    """
    Evaluates system thresholds (disk >= 80%, quota <= 20%, channel needs_reauth)
    and dispatches webhook notifications if configured.
    """
    return evaluate_system_thresholds_and_alert(db, wait=True)


@router.get("/visibility", response_class=HTMLResponse, summary="Operational visibility control panel")
def visibility_panel(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_viewer),
):
    """
    Renders the dedicated operational visibility control room panel.
    """
    snapshot = get_full_visibility_snapshot(db)

    ctx = {
        "request": request,
        "current_user": current_user,
        "csrf_token": get_csrf_token(request),
        "snapshot": snapshot,
    }
    return templates.TemplateResponse(request=request, name="visibility.html", context=ctx)
