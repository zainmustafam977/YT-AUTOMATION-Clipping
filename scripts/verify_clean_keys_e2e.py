import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        # 1. Login
        print("Logging in...")
        await page.goto("http://127.0.0.1:8000/login", wait_until="networkidle")
        await page.fill("#username", "admin")
        await page.fill("#password", "AdminPass12345!")
        await page.click("button[type='submit']")
        await page.wait_for_url("http://127.0.0.1:8000/", timeout=8000)
        print("Logged in successfully.")
        
        # 2. Go to /settings
        print("Navigating to /settings...")
        await page.goto("http://127.0.0.1:8000/settings", wait_until="networkidle")
        
        # Verify Key pools
        groq_badge = page.locator("text='1 Key(s) Configured'").first
        print("Groq '1 Key(s) Configured' visible:", await groq_badge.is_visible())
        
        # Find test buttons and click them to ping live
        test_buttons = page.locator("button:has-text('Test')")
        count = await test_buttons.count()
        print(f"Found {count} Test button(s) in key pools.")
        for i in range(count):
            btn = test_buttons.nth(i)
            await btn.click()
            print(f"Clicked Test button #{i+1}")
            
        # Wait a moment for network pings to resolve
        await page.wait_for_timeout(4000)
        
        # Check badges
        active_badges = page.locator("span:has-text('Active (')")
        active_count = await active_badges.count()
        print(f"Number of verified 'Active (...ms)' badges: {active_count}")
        for i in range(active_count):
            print(f"  Badge #{i+1}: {await active_badges.nth(i).text_content()}")
            
        # Take screenshot of API keys section
        artifact_dir = Path("C:/Users/ZACODEC/.gemini/antigravity/brain/c7dfcc93-21b9-4546-8fa8-4da2ac8217de")
        await page.screenshot(path=str(artifact_dir / "clean_api_key_pools_verified.png"), full_page=False)
        print(f"Screenshot saved to clean_api_key_pools_verified.png")
        
        # 3. Also check React SPA Settings Page
        print("Checking React SPA /app/settings...")
        await page.goto("http://127.0.0.1:8000/app/settings", wait_until="networkidle")
        await page.screenshot(path=str(artifact_dir / "react_spa_clean_settings.png"), full_page=False)
        print(f"Screenshot saved to react_spa_clean_settings.png")
        
        await browser.close()
        print("VERIFICATION COMPLETE!")

if __name__ == "__main__":
    asyncio.run(run())
