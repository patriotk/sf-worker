"""
Microsoft SSO login test - two-step flow:
  Step 1: python test_microsoft_login.py <email> <password>        → sends MFA code
  Step 2: python test_microsoft_login.py <email> <password> <code> → enters code, completes login
Uses persistent browser profile so session survives between runs.
"""
import asyncio
import sys
import os
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

PROFILE_DIR = "/data/profiles/ms_test"
ERRORS_DIR = "/data/errors"


async def test_ms_login(email: str, password: str, mfa_code: str = None):
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
        # Check if we're already logged in from persistent profile
        print(f"[1] Navigating to Microsoft login...")
        await page.goto("https://login.microsoftonline.com/", wait_until="domcontentloaded")
        await asyncio.sleep(3)
        url = page.url.lower()

        # If we already got redirected past login, we're in
        if "office.com" in url or "myapps" in url or "portal.azure" in url:
            await page.screenshot(path=f"{ERRORS_DIR}/ms_already_logged_in.png")
            print(f"[!] Already logged in! URL: {page.url}")
            return

        # === STEP 2: We have a code, enter it ===
        if mfa_code:
            print(f"[2] Have MFA code, looking for code input...")

            # Check if we're on the MFA page or need to re-login
            # Try to find the code input directly
            try:
                code_input = page.locator("input[type='tel'], input[name='otc'], input[type='text'][aria-label*='code'], input[placeholder*='Code'], input[id='iOttText']")
                await code_input.wait_for(state="visible", timeout=10000)
                print(f"[3] Found code input, entering code...")
                await code_input.fill(mfa_code)
                await asyncio.sleep(1)

                # Click verify/submit
                verify_btn = page.locator("input[type='submit'], button[type='submit']")
                await verify_btn.click()
                await asyncio.sleep(5)

                await page.screenshot(path=f"{ERRORS_DIR}/ms_05_after_code.png")
                print(f"[4] Screenshot saved: ms_05_after_code.png")
                print(f"    URL: {page.url}")

            except PlaywrightTimeout:
                # Maybe we need to go through email/password again
                print(f"[!] No code input found. May need to re-login first.")
                await page.screenshot(path=f"{ERRORS_DIR}/ms_05_no_code_input.png")
                print(f"    URL: {page.url}")

                # Try email field
                try:
                    email_input = page.locator("input[type='email'], input[name='loginfmt']")
                    if await email_input.count() > 0 and await email_input.is_visible():
                        print(f"[5] On login page, entering email...")
                        await email_input.fill(email)
                        await page.locator("input[type='submit']").click()
                        await asyncio.sleep(3)

                        pw_input = page.locator("input[type='password'], input[name='passwd']")
                        await pw_input.wait_for(state="visible", timeout=10000)
                        await pw_input.type(password, delay=50)
                        await page.locator("input[type='submit']").click()
                        await asyncio.sleep(5)

                        # Now look for code input again
                        code_input = page.locator("input[type='tel'], input[name='otc'], input[type='text'][aria-label*='code'], input[placeholder*='Code'], input[id='iOttText']")
                        await code_input.wait_for(state="visible", timeout=10000)
                        await code_input.fill(mfa_code)
                        await page.locator("input[type='submit']").click()
                        await asyncio.sleep(5)

                        await page.screenshot(path=f"{ERRORS_DIR}/ms_05_after_code.png")
                        print(f"[6] Screenshot: ms_05_after_code.png")
                except Exception as e:
                    print(f"[!] Re-login attempt failed: {e}")

            # Handle "Stay signed in?" prompt
            try:
                await asyncio.sleep(2)
                stay_btn = page.locator("input[type='submit'][value='Yes'], button:has-text('Yes')")
                if await stay_btn.count() > 0 and await stay_btn.first.is_visible():
                    print(f"[7] 'Stay signed in?' - clicking Yes...")
                    await stay_btn.first.click()
                    await asyncio.sleep(3)
            except Exception:
                pass

            await page.screenshot(path=f"{ERRORS_DIR}/ms_06_final.png")
            print(f"[8] Final screenshot: ms_06_final.png")
            print(f"    Final URL: {page.url}")

            url = page.url.lower()
            if "office.com" in url or "myapps" in url:
                print(f"\n[RESULT] SUCCESS - Logged into Microsoft!")
            elif "salesforce" in url:
                print(f"\n[RESULT] SUCCESS - Redirected to Salesforce!")
            else:
                print(f"\n[RESULT] Ended at: {url}")
            return

        # === STEP 1: Login and trigger MFA code send ===
        print(f"[2] Looking for email field...")
        try:
            email_input = page.locator("input[type='email'], input[name='loginfmt']")
            await email_input.wait_for(state="visible", timeout=10000)
            await email_input.fill(email)
            await page.locator("input[type='submit']").click()
            await asyncio.sleep(3)
        except PlaywrightTimeout:
            await page.screenshot(path=f"{ERRORS_DIR}/ms_step1_no_email.png")
            print(f"[!] No email field. URL: {page.url}")
            return

        print(f"[3] Entering password...")
        try:
            pw_input = page.locator("input[type='password'], input[name='passwd']")
            await pw_input.wait_for(state="visible", timeout=10000)
            await pw_input.type(password, delay=50)
            await page.locator("input[type='submit']").click()
            await asyncio.sleep(5)
        except PlaywrightTimeout:
            await page.screenshot(path=f"{ERRORS_DIR}/ms_step1_no_password.png")
            print(f"[!] No password field. URL: {page.url}")
            return

        await page.screenshot(path=f"{ERRORS_DIR}/ms_step1_after_password.png")
        print(f"[4] After password. URL: {page.url}")

        # Click first "Send a code" option
        print(f"[5] Looking for 'Send a code' option...")
        try:
            # Try clicking the first send-code option
            send_code_btn = page.locator("[data-value='OneWaySMS'], [data-value='Email'], div[role='button']:has-text('Send a code'), button:has-text('Send a code'), div:has-text('Send a code to pa')")
            if await send_code_btn.count() > 0:
                await send_code_btn.first.click()
                await asyncio.sleep(5)
                await page.screenshot(path=f"{ERRORS_DIR}/ms_step1_code_sent.png")
                print(f"[6] Clicked send code. Screenshot: ms_step1_code_sent.png")
                print(f"    URL: {page.url}")
            else:
                # Try a broader selector
                all_options = page.locator("div[data-testid='proofOption']")
                count = await all_options.count()
                print(f"    Found {count} proof options")
                if count > 0:
                    await all_options.first.click()
                    await asyncio.sleep(5)
                    await page.screenshot(path=f"{ERRORS_DIR}/ms_step1_code_sent.png")
                    print(f"[6] Clicked first option. Screenshot: ms_step1_code_sent.png")
                else:
                    print(f"[!] No send-code options found")
                    await page.screenshot(path=f"{ERRORS_DIR}/ms_step1_no_options.png")
        except Exception as e:
            print(f"[!] Error clicking send code: {e}")
            await page.screenshot(path=f"{ERRORS_DIR}/ms_step1_error.png")

        print(f"\n[WAITING] Check your email for the code, then run:")
        print(f"  python test_microsoft_login.py '{email}' '{password}' <CODE>")

    except Exception as e:
        await page.screenshot(path=f"{ERRORS_DIR}/ms_error.png")
        print(f"\n[ERROR] {e}")
    finally:
        await context.close()
        await pw.stop()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python test_microsoft_login.py <email> <password> [mfa_code]")
        sys.exit(1)
    code = sys.argv[3] if len(sys.argv) > 3 else None
    asyncio.run(test_ms_login(sys.argv[1], sys.argv[2], code))
