"""
==============================================================================
Deep Health, Liveness & Readiness Observability Routes
==============================================================================
Standard cloud-native Kubernetes & Docker health endpoints:
  - /livez  : Basic process liveness probe
  - /readyz : Deep dependency readiness probe (DB, Storage, FFmpeg, Memory)
  - /healthz: Unified health status summary
==============================================================================
"""

import time
import shutil
import logging
from pathlib import Path
from typing import Dict, Any
from fastapi import APIRouter, status, Response
from sqlalchemy import text
import psutil

from shorts_engine.config.settings import settings
from shorts_engine.db.session import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health & Observability"])

# Record application boot timestamp for uptime calculation
BOOT_TIME = time.time()


@router.get("/livez", summary="Liveness Probe")
def liveness_probe():
    """
    Kubernetes/container liveness probe.
    Returns 200 OK if the event loop is active and answering HTTP requests.
    """
    return {
        "status": "alive",
        "uptime_seconds": round(time.time() - BOOT_TIME, 2),
        "timestamp": time.time(),
    }


@router.get("/readyz", summary="Deep Readiness Probe")
def readiness_probe(response: Response):
    """
    Kubernetes/container readiness probe.
    Conducts deep non-blocking health checks:
      1. Database pool query & latency measurement
      2. Storage disk headroom (> 1.0 GB required, > 3.0 GB optimal)
      3. FFmpeg and FFprobe binary availability
      4. Host memory and CPU headroom
    Returns 200 if ready for traffic; returns 503 if any critical dependency fails.
    """
    checks: Dict[str, Any] = {}
    is_ready = True
    warnings = []

    # 1. Database Connection & Latency Check
    db_start = time.monotonic()
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1")).scalar()
        db_latency_ms = round((time.monotonic() - db_start) * 1000.0, 2)
        checks["database"] = {
            "status": "healthy",
            "latency_ms": db_latency_ms,
            "engine": "sqlite" if settings.DATABASE_URL.startswith("sqlite") else "postgresql",
        }
    except Exception as e:
        is_ready = False
        checks["database"] = {
            "status": "unhealthy",
            "error": str(e),
        }

    # 2. Storage Disk Space Headroom Check
    try:
        storage_path = str(settings.STORAGE_DIR)
        disk = psutil.disk_usage(storage_path)
        free_gb = round(disk.free / (1024 ** 3), 2)
        total_gb = round(disk.total / (1024 ** 3), 2)
        used_percent = disk.percent

        if free_gb < 1.0:
            is_ready = False
            disk_status = "critical"
            warnings.append(f"Storage free space critically low: {free_gb} GB remaining.")
        elif free_gb < 3.0:
            disk_status = "warning"
            warnings.append(f"Storage free space low: {free_gb} GB remaining.")
        else:
            disk_status = "healthy"

        checks["storage"] = {
            "status": disk_status,
            "path": storage_path,
            "free_gb": free_gb,
            "total_gb": total_gb,
            "used_percent": used_percent,
        }
    except Exception as e:
        checks["storage"] = {
            "status": "warning",
            "error": str(e),
        }

    # 3. FFmpeg & FFprobe Tooling Availability
    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    if ffmpeg_bin and ffprobe_bin:
        checks["media_tools"] = {
            "status": "healthy",
            "ffmpeg": ffmpeg_bin,
            "ffprobe": ffprobe_bin,
        }
    else:
        # Without FFmpeg, rendering worker cannot function
        is_ready = False
        checks["media_tools"] = {
            "status": "unhealthy",
            "ffmpeg_found": bool(ffmpeg_bin),
            "ffprobe_found": bool(ffprobe_bin),
            "error": "FFmpeg or FFprobe missing from PATH",
        }

    # 4. Host Resource Utilization
    try:
        mem = psutil.virtual_memory()
        cpu_pct = psutil.cpu_percent(interval=None)
        checks["resources"] = {
            "status": "healthy" if mem.percent < 95.0 else "warning",
            "memory_used_percent": mem.percent,
            "memory_available_mb": round(mem.available / (1024 ** 2), 1),
            "cpu_percent": cpu_pct,
        }
    except Exception as e:
        checks["resources"] = {"status": "unknown", "error": str(e)}

    # Determine overall status code
    overall_status = "ready" if is_ready else "not_ready"
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": overall_status,
        "is_ready": is_ready,
        "uptime_seconds": round(time.time() - BOOT_TIME, 2),
        "timestamp": time.time(),
        "checks": checks,
        "warnings": warnings,
    }


@router.get("/healthz", summary="Unified Health Status")
def healthz_summary(response: Response):
    """
    Standard top-level healthcheck returning clean status and uptime.
    Delegates failure codes if readiness probe evaluates critical failure.
    """
    res = readiness_probe(response)
    overall_status = "healthy" if res["is_ready"] else "unhealthy"
    return {
        "status": overall_status,
        "uptime_seconds": res["uptime_seconds"],
        "checks": {k: v.get("status", "unknown") for k, v in res.get("checks", {}).items()},
    }
