"""
FastAPI application factory for the Shorts Engine operator dashboard.
Mounts all route groups and configures Jinja2 templating and unauthenticated browser redirects.
"""

import urllib.parse
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import RedirectResponse, JSONResponse
from contextlib import asynccontextmanager

from shorts_engine.dashboard.routes import (
    status as status_route,
    queue,
    channels,
    calendar as calendar_route,
    websub,
    auth,
    settings as settings_route,
    logs as logs_route,
    health as health_route,
    metrics as metrics_route,
    events as events_route,
    ingestion as ingestion_route,
    oauth as oauth_route,
    publish as publish_route,
    visibility as visibility_route,
)
from shorts_engine.config.settings import settings
from shorts_engine.config.logging_config import setup_logging, reload_runtime_settings
from shorts_engine.services.publishing.drip_feed import DripFeedDaemon
from shorts_engine.services.ab_testing.winner_daemon import ABWinnerPromotionDaemon
from shorts_engine.services.storage.housekeeping import HousekeepingDaemon
from shorts_engine.services.ingestion.hotfolder_watcher import hotfolder_watcher


@asynccontextmanager
async def lifespan(app: FastAPI):
    drip_daemon = DripFeedDaemon()
    ab_daemon = ABWinnerPromotionDaemon()
    housekeeping_daemon = HousekeepingDaemon()
    await drip_daemon.start()
    await ab_daemon.start()
    await housekeeping_daemon.start()
    if getattr(settings, "HOTFOLDER_ENABLED", True):
        await hotfolder_watcher.start()
    try:
        yield
    finally:
        if getattr(settings, "HOTFOLDER_ENABLED", True):
            await hotfolder_watcher.stop()
        await housekeeping_daemon.stop()
        await ab_daemon.stop()
        await drip_daemon.stop()


def create_dashboard_app() -> FastAPI:

    setup_logging()
    reload_runtime_settings()

    # Reconcile stale claim locks and purge old temp files from prior crashed runs
    try:
        from shorts_engine.db.session import SessionLocal, init_db
        from shorts_engine.services.worker.runner import reconcile_stale_claims
        from shorts_engine.services.ingestion.downloader import IngestionService
        from shorts_engine.services.execution.win32_job import init_win32_job_object
        from shorts_engine.dashboard.auth import ensure_default_admin
        init_win32_job_object()
        init_db()
        with SessionLocal() as db:
            ensure_default_admin(db)
            reconcile_stale_claims(db)
            from shorts_engine.services.publishing.orchestrator import PublishOrchestrator
            PublishOrchestrator().reconcile_startup_uploading_jobs(db)
        IngestionService.purge_stale_temp_files(max_age_hours=24.0)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Startup reconciliation warning: %s", e)


    app = FastAPI(
        title="Shorts Engine Dashboard",
        description="Operator dashboard for YouTube Shorts Automation Platform",
        version="2.0.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        lifespan=lifespan,
    )

    @app.exception_handler(HTTPException)
    async def custom_http_exception_handler(request: Request, exc: HTTPException):
        """
        Catches 401 Unauthorized exceptions:
        - If from a browser requesting an HTML document, redirects immediately to /login with next-url.
        - If from an API/JSON client, returns clean JSON {"detail": "..."}.
        """
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            accept = request.headers.get("accept", "")
            sec_fetch_dest = request.headers.get("sec-fetch-dest", "")
            if sec_fetch_dest in ("document", "iframe") or ("text/html" in accept and "application/json" not in accept):
                next_url = request.url.path
                if request.url.query:
                    next_url += f"?{request.url.query}"
                if request.url.path not in (
                    "/login", "/logout", "/livez", "/readyz", "/healthz", "/metrics", "/api/health", "/api/readiness", "/api/metrics/ai-tokens"
                ):
                    return RedirectResponse(
                        url=f"/login?next={urllib.parse.quote_plus(next_url)}",
                        status_code=status.HTTP_303_SEE_OTHER,
                    )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )

    @app.middleware("http")
    async def validate_host_header(request: Request, call_next):
        host_header = request.headers.get("host", "")
        # Strip port if present (handle both ipv4/hostname:port and [ipv6]:port)
        raw_host = host_header
        if raw_host.startswith("["):
            raw_host = raw_host.split("]")[0] + "]"
        elif ":" in raw_host:
            raw_host = raw_host.split(":")[0]

        allowed_hosts = {"127.0.0.1", "localhost", "testserver", "[::1]"}
        if raw_host and raw_host.lower() not in allowed_hosts:
            return JSONResponse(
                status_code=400,
                content={"detail": f"Invalid Host header '{host_header}'. Localhost access only."},
            )
        return await call_next(request)

    @app.middleware("http")
    async def add_no_cache_headers_for_html(request: Request, call_next):
        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        # Apply no-cache to HTML responses so browser back-forward cache (bfcache) doesn't leak authenticated pages after logout
        if "text/html" in content_type:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    # Mount route groups
    app.include_router(auth.router, tags=["Auth"])
    app.include_router(status_route.router, tags=["Status"])
    app.include_router(queue.router, tags=["Review Queue"])
    app.include_router(calendar_route.router, tags=["Calendar"])
    app.include_router(channels.router, tags=["Channels"])
    app.include_router(settings_route.router, tags=["Settings"])
    app.include_router(logs_route.router, tags=["Logs"])
    app.include_router(websub.router, tags=["WebSub"])  # No Basic Auth — HMAC secured
    app.include_router(health_route.router)
    app.include_router(metrics_route.router)
    app.include_router(events_route.router, tags=["Events"])
    app.include_router(ingestion_route.router, tags=["Ingestion"])
    app.include_router(oauth_route.router, tags=["OAuth"])
    app.include_router(publish_route.router, tags=["Publishing"])
    app.include_router(visibility_route.router, tags=["Visibility"])

    # Mount pre-compiled React 19 SPA static assets
    from pathlib import Path
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    dist_dir = Path(__file__).parent / "static" / "dist"
    if dist_dir.exists():
        assets_dir = dist_dir / "assets"
        if assets_dir.exists():
            app.mount("/app/assets", StaticFiles(directory=str(assets_dir)), name="spa_assets")

        @app.get("/app", include_in_schema=False)
        @app.get("/app/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str = ""):
            index_file = dist_dir / "index.html"
            if index_file.exists():
                return FileResponse(str(index_file))
            return JSONResponse({"error": "SPA build not found"}, status_code=404)

    return app




# Module-level app instance (used by uvicorn)
app = create_dashboard_app()

__all__ = ["create_dashboard_app", "app", "lifespan", "HousekeepingDaemon"]
