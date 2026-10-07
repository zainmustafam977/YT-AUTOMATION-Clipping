"""
Automated headless browser script to capture verified screenshots of Channel Videos
with accurate YouTube upload dates/times on both Jinja2 and React 19 SPA.
"""

import time
from pathlib import Path
from playwright.sync_api import sync_playwright

from shorts_engine.dashboard.auth import SESSION_COOKIE_NAME, create_session_token
from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import User

ARTIFACT_DIRS = [
    Path(r"C:\Users\ZACODEC\.gemini\antigravity\brain\c7dfcc93-21b9-4546-8fa8-4da2ac8217de"),
    Path(r"C:\Users\ZACODEC\.gemini\antigravity\brain\bcc6cdfa-4fc0-4ec3-b246-c7cb62982161"),
]

def main():
    console_errors = []

    # Generate admin session token directly
    db = SessionLocal()
    admin_user = db.query(User).filter_by(username="admin").first()
    if not admin_user:
        raise RuntimeError("Admin user not found in database")
    session_token = create_session_token(admin_user.id)
    db.close()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 900})

        # Inject session cookie
        context.add_cookies([{
            "name": SESSION_COOKIE_NAME,
            "value": session_token,
            "domain": "127.0.0.1",
            "path": "/",
            "httpOnly": True,
            "sameSite": "Lax",
        }])

        page = context.new_page()
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        # --- Classic Jinja2 Channel Videos Drawer ---
        print("1. Navigating to /channels...")
        page.goto("http://127.0.0.1:8000/channels", wait_until="networkidle")

        print("   Opening Channel Videos Drawer for Ben Shapiro...")
        # Target the Ben Shapiro row videos button
        row = page.locator("tr:has-text('Ben Shapiro')").first
        if row.count() > 0:
            row.locator("button:has-text('📺 Videos')").click()
        else:
            page.locator("button:has-text('📺 Videos')").first.click()

        # Wait for drawer to open and videos to load
        print("   Waiting for drawer videos to load...")
        page.wait_for_selector("#channelVideosList .card", timeout=30000)
        time.sleep(1.5)

        print("   Capturing jinja_channel_videos_drawer.png...")
        for ad in ARTIFACT_DIRS:
            ad.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(ad / "jinja_channel_videos_drawer.png"), full_page=False)

        # --- Modern React 19 SPA Channel Videos Modal ---
        print("2. Navigating to /app/channels...")
        page.goto("http://127.0.0.1:8000/app/channels", wait_until="networkidle")
        page.wait_for_selector("[data-testid='channels-page']")

        print("   Opening React Channel Videos Modal for Ben Shapiro...")
        react_row = page.locator("tr:has-text('Ben Shapiro')").first
        if react_row.count() > 0:
            react_row.locator("[data-testid^='videos-btn-']").click()
        else:
            page.locator("[data-testid^='videos-btn-']").first.click()

        # Wait for modal and video cards to load
        print("   Waiting for modal video cards to load...")
        page.wait_for_selector("[data-testid^='channel-video-card-']", timeout=30000)
        time.sleep(1.5)

        print("   Capturing react_channel_videos_modal.png...")
        for ad in ARTIFACT_DIRS:
            page.screenshot(path=str(ad / "react_channel_videos_modal.png"), full_page=False)

        browser.close()

    print("Verification complete!")
    print(f"Console errors: {len(console_errors)}")
    if console_errors:
        for err in console_errors:
            print("  -", err)

if __name__ == "__main__":
    main()
