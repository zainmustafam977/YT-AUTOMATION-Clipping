"""
FastAPI application factory for the Shorts Engine operator dashboard.
Mounts all route groups and configures Jinja2 templating and unauthenticated browser redirects.
"""

import urllib.parse
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
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
)
from shorts_engine.config.logging_config import setup_logging, reload_runtime_settings
from shorts_engine.services.publishing.drip_feed import DripFeedDaemon


@asynccontextmanager
async def lifespan(app: FastAPI):
    daemon = DripFeedDaemon()
    await daemon.start()
    try:
        yield
    finally:
        await daemon.stop()


def create_dashboard_app() -> FastAPI:

    setup_logging()
    reload_runtime_settings()

    # Reconcile stale claim locks and purge old temp files from prior crashed runs
    try:
        from shorts_engine.db.session import SessionLocal
        from shorts_engine.services.worker.runner import reconcile_stale_claims
        from shorts_engine.services.ingestion.downloader import IngestionService
        with SessionLocal() as db:
            reconcile_stale_claims(db)
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
                if request.url.path not in ("/login", "/logout"):
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

    return app



# Module-level app instance (used by uvicorn)
app = create_dashboard_app()
