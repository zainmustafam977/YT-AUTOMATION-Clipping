"""
==============================================================================
Prometheus & OpenMetrics Telemetry Exposition Route (/metrics)
==============================================================================
Standard Prometheus metrics exposition endpoint for Grafana, Datadog,
and Prometheus scraping. Reports operational KPIs, queue depths,
storage capacity, and AI provider statuses.
==============================================================================
"""

import time
import psutil
from fastapi import APIRouter, Response
from sqlalchemy import func
from shorts_engine.config.settings import settings
from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import Clip, SourceVideo
from shorts_engine.dashboard.routes.health import BOOT_TIME

router = APIRouter(tags=["Metrics & Telemetry"])


@router.get("/metrics", summary="Prometheus Metrics Exposition")
def prometheus_metrics():
    """
    Exposes system metrics in standard Prometheus / OpenMetrics text format.
    Scraped by Prometheus server at regular intervals.
    """
    lines = []

    # 1. System Uptime
    uptime = time.time() - BOOT_TIME
    lines.append("# HELP shorts_uptime_seconds Total seconds elapsed since dashboard boot")
    lines.append("# TYPE shorts_uptime_seconds gauge")
    lines.append(f"shorts_uptime_seconds {uptime:.2f}")

    # 2. Host CPU & Memory
    mem = psutil.virtual_memory()
    lines.append("\n# HELP shorts_host_memory_used_percent System memory utilization percentage")
    lines.append("# TYPE shorts_host_memory_used_percent gauge")
    lines.append(f"shorts_host_memory_used_percent {mem.percent:.1f}")

    lines.append("\n# HELP shorts_host_memory_available_bytes Free available system memory in bytes")
    lines.append("# TYPE shorts_host_memory_available_bytes gauge")
    lines.append(f"shorts_host_memory_available_bytes {mem.available}")

    # 3. Storage Headroom
    try:
        disk = psutil.disk_usage(str(settings.STORAGE_DIR))
        lines.append("\n# HELP shorts_storage_bytes Storage partition capacity in bytes")
        lines.append("# TYPE shorts_storage_bytes gauge")
        lines.append(f'shorts_storage_bytes{{type="total"}} {disk.total}')
        lines.append(f'shorts_storage_bytes{{type="used"}} {disk.used}')
        lines.append(f'shorts_storage_bytes{{type="free"}} {disk.free}')
    except Exception:
        pass

    # 4. Database Metrics (Clips and Videos)
    try:
        with SessionLocal() as db:
            # Clip status counts
            clip_counts = (
                db.query(Clip.status, func.count(Clip.id))
                .group_by(Clip.status)
                .all()
            )
            lines.append("\n# HELP shorts_clips_total Total generated clips partitioned by lifecycle status")
            lines.append("# TYPE shorts_clips_total gauge")
            for c_status, count in clip_counts:
                status_name = c_status.value if hasattr(c_status, "value") else str(c_status)
                lines.append(f'shorts_clips_total{{status="{status_name}"}} {count}')

            # Source video counts
            video_counts = (
                db.query(SourceVideo.status, func.count(SourceVideo.id))
                .group_by(SourceVideo.status)
                .all()
            )
            lines.append("\n# HELP shorts_source_videos_total Total ingested source videos partitioned by status")
            lines.append("# TYPE shorts_source_videos_total gauge")
            for v_status, count in video_counts:
                v_name = v_status.value if hasattr(v_status, "value") else str(v_status)
                lines.append(f'shorts_source_videos_total{{status="{v_name}"}} {count}')
    except Exception:
        pass

    # 5. AI API Key Configuration Status
    lines.append("\n# HELP shorts_configured_api_keys Total operational API keys configured per provider")
    lines.append("# TYPE shorts_configured_api_keys gauge")
    lines.append(f'shorts_configured_api_keys{{provider="groq"}} {len(settings.get_groq_keys())}')
    lines.append(f'shorts_configured_api_keys{{provider="gemini"}} {len(settings.get_gemini_keys())}')
    lines.append(f'shorts_configured_api_keys{{provider="openrouter"}} {len(settings.get_openrouter_keys())}')
    lines.append(f'shorts_configured_api_keys{{provider="openai"}} {len(settings.get_openai_keys())}')

    lines.append("")  # trailing newline required by Prometheus specification
    body = "\n".join(lines)
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")
