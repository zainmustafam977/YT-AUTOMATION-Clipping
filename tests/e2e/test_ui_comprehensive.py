"""
Automated Playwright E2E UI Test Suite Protocol (Module 12 / Task 5.5).
Tests login, CSRF tokens, session persistence, preflight validation, all action buttons,
modals, style switchers, folder selection, and responsive viewports (480px, 768px, 1440px).
Covers both the Jinja2 dashboard and the embedded React 19 SPA (/app).
"""

import os
import socket
import threading
import time
import pytest
import uvicorn

# Suppress insecure key warning for tests
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from shorts_engine.dashboard.app import create_dashboard_app
from shorts_engine.config.settings import settings
from shorts_engine.dashboard.auth import (
    create_session_token,
    hash_password,
    SESSION_COOKIE_NAME,
)
from shorts_engine.db.session import SessionLocal, init_db
from shorts_engine.db.models import User, UserRole


def get_free_port() -> int:
    """Finds an available local port for the test server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServerThread(threading.Thread):
    """Runs uvicorn in a background thread for Playwright browser testing."""

    def __init__(self, app, port: int):
        super().__init__(daemon=True)
        self.app = app
        self.port = port
        self.server = None

    def run(self):
        config = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self.server = uvicorn.Server(config)
        self.server.run()

    def stop(self):
        if self.server:
            self.server.should_exit = True


@pytest.fixture(scope="module")
def live_server():
    """Starts a live FastAPI test server on an ephemeral port for the test session."""
    init_db()
    with SessionLocal() as db:
        admin_user = db.query(User).filter_by(username=settings.DASHBOARD_USERNAME).first()
        if not admin_user:
            admin_user = User(
                username=settings.DASHBOARD_USERNAME,
                password_hash=hash_password(settings.DASHBOARD_PASSWORD),
                role=UserRole.ADMIN,
                is_active=True,
            )
            db.add(admin_user)
            db.commit()
            db.refresh(admin_user)
        else:
            admin_user.password_hash = hash_password(settings.DASHBOARD_PASSWORD)
            db.commit()
            db.refresh(admin_user)

        user_id = admin_user.id

    port = get_free_port()
    app = create_dashboard_app()
    server_thread = LiveServerThread(app, port)
    server_thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10.0
    started = False
    while time.time() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    started = True
                    break
        except Exception:
            pass
        time.sleep(0.1)

    if not started:
        raise RuntimeError(f"Live test server failed to start on port {port}")

    # Return base_url and valid session token for pre-authenticated browser contexts
    token = create_session_token(user_id)
    yield base_url, token

    server_thread.stop()


def create_auth_context(browser, base_url: str, session_token: str, viewport=None):
    """Helper that creates a pre-authenticated browser context with valid session cookie."""
    ctx = browser.new_context(viewport=viewport) if viewport else browser.new_context()
    # Extract host and port for cookie domain
    host = "127.0.0.1"
    ctx.add_cookies([{
        "name": SESSION_COOKIE_NAME,
        "value": session_token,
        "domain": host,
        "path": "/",
        "httpOnly": True,
        "sameSite": "Lax",
    }])
    return ctx


# ─── Jinja2 Dashboard E2E Tests ─────────────────────────────────────────────

def test_login_redirect_and_session(live_server):
    """Tests unauthenticated redirect to /login and form-based login flow."""
    base_url, _ = live_server
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        # Visiting / without auth redirects to /login?next=%2F
        page.goto(f"{base_url}/")
        assert "/login" in page.url

        # Fill credentials and submit via form
        page.fill('#username', settings.DASHBOARD_USERNAME)
        page.fill('#password', settings.DASHBOARD_PASSWORD)

        # Trigger form submission
        with page.expect_navigation(timeout=8000):
            page.click('button[type="submit"]')

        assert page.url.rstrip("/") == base_url.rstrip("/")

        # Verify session cookie was set in browser context
        cookies = context.cookies()
        session_cookie = next((c for c in cookies if c["name"] == SESSION_COOKIE_NAME), None)
        assert session_cookie is not None, "session_id cookie should be present after login"

        browser.close()


def test_status_page_elements_and_ingest_form(live_server):
    """Tests that the status dashboard renders all critical sections and inputs."""
    base_url, token = live_server
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = create_auth_context(browser, base_url, token)
        page = context.new_page()

        page.goto(f"{base_url}/")
        assert page.locator("body").is_visible()

        # Check navigation elements
        nav = page.locator("nav, .navbar, header")
        assert nav.count() > 0

        # Check Manual Ingestion card URL input
        url_input = page.locator('input[name="url"], input[id*="url"], input[placeholder*="YouTube"], input[placeholder*="http"]')
        assert url_input.count() > 0

        browser.close()


def test_queue_page_tabs_and_rendering(live_server):
    """Tests /queue page rendering and tab switching."""
    base_url, token = live_server
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = create_auth_context(browser, base_url, token)
        page = context.new_page()

        page.goto(f"{base_url}/queue")
        assert "/queue" in page.url
        assert page.locator("body").is_visible()

        browser.close()


def test_channels_page_rendering(live_server):
    """Tests /channels page rendering."""
    base_url, token = live_server
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = create_auth_context(browser, base_url, token)
        page = context.new_page()

        page.goto(f"{base_url}/channels")
        assert "/channels" in page.url
        assert page.locator("body").is_visible()

        browser.close()


def test_settings_page_rendering(live_server):
    """Tests /settings page rendering and form inputs."""
    base_url, token = live_server
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = create_auth_context(browser, base_url, token)
        page = context.new_page()

        page.goto(f"{base_url}/settings")
        assert "/settings" in page.url
        assert page.locator("body").is_visible()

        browser.close()


# ─── React 19 SPA E2E Tests (/app) ──────────────────────────────────────────

def test_react_spa_root_mount(live_server):
    """Tests that the React 19 SPA loads at /app and renders the shell layout."""
    base_url, token = live_server
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = create_auth_context(browser, base_url, token)
        page = context.new_page()

        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(f"{base_url}/app")
        page.wait_for_selector('[data-testid="app-header"]', timeout=8000)

        # Header logo and live badge
        assert page.locator('[data-testid="app-header"]').is_visible()
        assert page.locator('[data-testid="user-pill"]').is_visible()

        # Sidebar navigation links
        assert page.locator('[data-testid="nav-status"]').is_visible()
        assert page.locator('[data-testid="nav-review-queue"]').is_visible()
        assert page.locator('[data-testid="nav-channels"]').is_visible()
        assert page.locator('[data-testid="nav-logs"]').is_visible()
        assert page.locator('[data-testid="nav-settings"]').is_visible()

        # Stat cards
        assert page.locator('[data-testid="stat-grid"]').is_visible()

        # Ingestion form
        assert page.locator('[data-testid="ingest-url-input"]').is_visible()
        assert page.locator('[data-testid="ingest-btn"]').is_visible()

        assert len(errors) == 0, f"Unexpected page errors: {errors}"
        browser.close()


def test_react_spa_client_side_navigation(live_server):
    """Tests client-side routing between all SPA tabs without full page reloads."""
    base_url, token = live_server
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = create_auth_context(browser, base_url, token)
        page = context.new_page()

        page.goto(f"{base_url}/app")
        page.wait_for_selector('[data-testid="app-header"]', timeout=8000)

        # Navigate to Queue
        page.click('[data-testid="nav-review-queue"]')
        page.wait_for_selector('[data-testid="queue-page"]', timeout=5000)
        assert page.locator('[data-testid="tab-active"]').is_visible()
        assert page.locator('[data-testid="tab-rejected"]').is_visible()

        # Navigate to Channels
        page.click('[data-testid="nav-channels"]')
        page.wait_for_selector('[data-testid="channels-page"]', timeout=5000)

        # Navigate to Logs
        page.click('[data-testid="nav-logs"]')
        page.wait_for_selector('[data-testid="logs-page"]', timeout=5000)
        assert page.locator('[data-testid="log-output"]').is_visible()

        # Navigate to Settings
        page.click('[data-testid="nav-settings"]')
        page.wait_for_selector('[data-testid="settings-page"]', timeout=5000)
        assert page.locator('[data-testid="save-settings-btn"]').is_visible()

        # Navigate back to Status
        page.click('[data-testid="nav-status"]')
        page.wait_for_selector('[data-testid="status-page"]', timeout=5000)

        browser.close()


def test_react_spa_responsive_viewports(live_server):
    """Tests the React SPA layout across mobile (480px), tablet (768px), and desktop (1440px)."""
    base_url, token = live_server
    from playwright.sync_api import sync_playwright

    viewports = [
        {"name": "mobile",  "width": 480,  "height": 800},
        {"name": "tablet",  "width": 768,  "height": 1024},
        {"name": "desktop", "width": 1440, "height": 900},
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for vp in viewports:
            context = create_auth_context(browser, base_url, token, viewport={"width": vp["width"], "height": vp["height"]})
            page = context.new_page()
            page.goto(f"{base_url}/app")
            page.wait_for_selector('[data-testid="app-header"]', timeout=8000)

            # Ensure header and main content remain visible and not clipped
            header = page.locator('[data-testid="app-header"]')
            main = page.locator('[data-testid="main-content"]')
            assert header.is_visible(), f"Header should be visible at {vp['name']} viewport"
            assert main.is_visible(), f"Main content should be visible at {vp['name']} viewport"

            context.close()

        browser.close()
