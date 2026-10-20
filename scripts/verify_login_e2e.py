import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        # 1. Visit /login
        print("Navigating to http://127.0.0.1:8000/login...")
        await page.goto("http://127.0.0.1:8000/login", wait_until="networkidle")
        title = await page.title()
        print(f"Login page loaded. Title: {title}")
        
        # Screenshot login page
        artifact_dir = Path("C:/Users/ZACODEC/.gemini/antigravity/brain/c7dfcc93-21b9-4546-8fa8-4da2ac8217de")
        await page.screenshot(path=str(artifact_dir / "login_page_verified.png"))
        
        # 2. Test password toggle
        pwd_input = page.locator("#password")
        toggle_btn = page.locator("#togglePasswordBtn")
        await pwd_input.fill(os.environ["DASHBOARD_PASSWORD"])
        print("Initial password type:", await pwd_input.get_attribute("type"))
        await toggle_btn.click()
        print("Toggled password type:", await pwd_input.get_attribute("type"))
        assert await pwd_input.get_attribute("type") == "text"
        await toggle_btn.click()
        assert await pwd_input.get_attribute("type") == "password"
        
        # 3. Fill username and submit
        await page.fill("#username", "admin")
        print("Submitting login form...")
        await page.click("button[type='submit']")
        
        # 4. Wait for redirection to dashboard
        await page.wait_for_url("http://127.0.0.1:8000/", timeout=8000)
        print(f"Successfully redirected to: {page.url}")
        print(f"Dashboard Title: {await page.title()}")
        
        # Verify session cookie
        cookies = await context.cookies()
        session_cookies = [c for c in cookies if c["name"] == "session_id"]
        assert len(session_cookies) > 0, "session_id cookie not found!"
        s_cookie = session_cookies[0]
        print(f"Session cookie verified: name={s_cookie['name']}, httpOnly={s_cookie['httpOnly']}, sameSite={s_cookie['sameSite']}, secure={s_cookie['secure']}")
        
        # Screenshot post-login dashboard
        await page.screenshot(path=str(artifact_dir / "post_login_dashboard.png"))
        
        # 5. Verify /api/me works with this authenticated browser session
        me_resp = await page.request.get("http://127.0.0.1:8000/api/me")
        print("GET /api/me status:", me_resp.status)
        me_data = await me_resp.json()
        print(f"Authenticated as: username={me_data['username']}, role={me_data['role']}")
        assert me_data["username"] == "admin"
        assert me_data["role"] == "admin"
        
        # 6. Verify SPA /app/status loads with session
        await page.goto("http://127.0.0.1:8000/app/status", wait_until="networkidle")
        print(f"SPA Status URL: {page.url}")
        await page.screenshot(path=str(artifact_dir / "post_login_spa_status.png"))
        
        await browser.close()
        print("ALL LOGIN VERIFICATION TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(run())
