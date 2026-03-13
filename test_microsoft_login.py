"""
Quick test: Can Playwright get through Microsoft's login page?
Usage: python test_microsoft_login.py <email> <password>
"""
import asyncio
import sys
import os
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

PROFILE_DIR = "/data/profiles/ms_test"
ERRORS_DIR = "/data/errors"


async def test_ms_login(email: str, password: str):
    print(f"[1] Launching browser (headless)...")
    pw = await async_playwright().start()

    os.makedirs(PROFILE_DIR, exist_ok=True)
    os.makedirs(ERRORS_DIR, exist_ok=True)

    context = await pw.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        headless=True,
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )
    page = context.pages[0] if context.pages else await context.new_page()

    try:
        # Navigate to Microsoft login
        print(f"[2] Navigating to Microsoft login...")
        await page.goto("https://login.microsoftonline.com/", wait_until="domcontentloaded")
        await asyncio.sleep(3)

        # Screenshot: initial page
        await page.screenshot(path=f"{ERRORS_DIR}/ms_01_initial.png")
        print(f"[3] Screenshot saved: ms_01_initial.png")
        print(f"    URL: {page.url}")

        # Enter email
        print(f"[4] Looking for email field...")
        try:
            email_input = page.locator("input[type='email'], input[name='loginfmt']")
            await email_input.wait_for(state="visible", timeout=10000)
            await email_input.fill(email)
            print(f"    Filled email: {email}")

            # Click Next
            next_btn = page.locator("input[type='submit'], button[type='submit']")
            await next_btn.click()
            await asyncio.sleep(3)

            await page.screenshot(path=f"{ERRORS_DIR}/ms_02_after_email.png")
            print(f"[5] Screenshot saved: ms_02_after_email.png")
            print(f"    URL: {page.url}")

        except PlaywrightTimeout:
            await page.screenshot(path=f"{ERRORS_DIR}/ms_02_email_timeout.png")
            print(f"[!] Email field not found. Screenshot saved.")
            print(f"    URL: {page.url}")
            return

        # Enter password
        print(f"[6] Looking for password field...")
        try:
            pw_input = page.locator("input[type='password'], input[name='passwd']")
            await pw_input.wait_for(state="visible", timeout=10000)
            await pw_input.type(password, delay=50)
            print(f"    Filled password ({len(password)} chars)")

            # Click Sign In
            sign_in_btn = page.locator("input[type='submit'], button[type='submit']")
            await sign_in_btn.click()
            await asyncio.sleep(5)

            await page.screenshot(path=f"{ERRORS_DIR}/ms_03_after_password.png")
            print(f"[7] Screenshot saved: ms_03_after_password.png")
            print(f"    URL: {page.url}")

        except PlaywrightTimeout:
            await page.screenshot(path=f"{ERRORS_DIR}/ms_03_password_timeout.png")
            print(f"[!] Password field not found. Screenshot saved.")
            print(f"    URL: {page.url}")
            return

        # Check for "Stay signed in?" prompt
        try:
            stay_signed_in = page.locator("input[type='submit'][value='Yes'], button:has-text('Yes')")
            if await stay_signed_in.count() > 0:
                print(f"[8] 'Stay signed in?' prompt detected, clicking Yes...")
                await stay_signed_in.first.click()
                await asyncio.sleep(3)
        except Exception:
            pass

        # Check for MFA prompt
        await page.screenshot(path=f"{ERRORS_DIR}/ms_04_final.png")
        print(f"[8] Final screenshot saved: ms_04_final.png")
        print(f"    Final URL: {page.url}")

        # Determine result
        url = page.url.lower()
        if "microsoftonline" in url and ("kmsi" in url or "login" in url):
            print(f"\n[RESULT] Still on Microsoft login - may need MFA or got blocked")
        elif "myapps" in url or "office" in url or "portal" in url:
            print(f"\n[RESULT] SUCCESS - logged into Microsoft!")
        elif "salesforce" in url:
            print(f"\n[RESULT] SUCCESS - redirected to Salesforce!")
        else:
            print(f"\n[RESULT] Ended up at: {url}")

    except Exception as e:
        await page.screenshot(path=f"{ERRORS_DIR}/ms_error.png")
        print(f"\n[ERROR] {e}")
        print(f"    URL: {page.url}")

    finally:
        await context.close()
        await pw.stop()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python test_microsoft_login.py <email> <password>")
        sys.exit(1)
    asyncio.run(test_ms_login(sys.argv[1], sys.argv[2]))
