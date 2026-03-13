"""Test Outlook access using persistent MS session profile."""
import asyncio
from playwright.async_api import async_playwright

async def run():
    pw = await async_playwright().start()
    ctx = await pw.chromium.launch_persistent_context(
        user_data_dir="/data/profiles/ms_full_test",
        headless=True,
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()

    # Navigate to Outlook - will redirect to MS login
    print("[1] Going to outlook.office.com...")
    await page.goto("https://outlook.office.com/mail/inbox", wait_until="domcontentloaded")
    await asyncio.sleep(5)
    url = page.url
    print(f"URL: {url[:120]}")
    await page.screenshot(path="/data/errors/outlook_step1.png")

    # Enter email if login page
    email_input = page.locator("input[type='email'], input[name='loginfmt']")
    if await email_input.count() > 0 and await email_input.first.is_visible():
        print("[2] Entering email...")
        await email_input.fill("patliquid@outlook.com")
        await page.locator("input[type='submit'], button:has-text('Next')").first.click()
        await asyncio.sleep(4)
        print(f"URL after email: {page.url[:100]}")
        await page.screenshot(path="/data/errors/outlook_step2.png")

        # Check if password needed
        pw_input = page.locator("input[type='password'], input[name='passwd']")
        if await pw_input.count() > 0 and await pw_input.first.is_visible():
            print("[3] Password required - entering...")
            await pw_input.type("RepDrive@2", delay=50)
            await page.locator("input[type='submit'], button:has-text('Next'), button:has-text('Sign in')").first.click()
            await asyncio.sleep(6)
            print(f"URL after password: {page.url[:100]}")
            await page.screenshot(path="/data/errors/outlook_step3.png")

            # Check if MFA is needed (THIS IS THE KEY TEST)
            mfa_indicators = [
                "input[type='tel']", "input[maxlength='1']",
                "text=Enter the code", "text=Verify your identity",
                "text=Send a code",
            ]
            mfa_found = False
            for sel in mfa_indicators:
                el = page.locator(sel)
                if await el.count() > 0:
                    mfa_found = True
                    print(f"MFA DETECTED: {sel}")
                    break

            if mfa_found:
                print("[!] MFA REQUIRED - session did NOT persist for Outlook")
            else:
                print("[OK] No MFA - session persisted!")

            # Stay signed in?
            try:
                stay = page.locator("input[value='Yes'], button:has-text('Yes')")
                if await stay.count() > 0 and await stay.first.is_visible():
                    # Check "Don't show again"
                    try:
                        dont_show = page.locator("input#KmsiCheckboxField, input[type='checkbox']")
                        if await dont_show.count() > 0:
                            await dont_show.first.check()
                    except Exception:
                        pass
                    await stay.first.click()
                    print("[4] Clicked Stay signed in")
                    await asyncio.sleep(3)
            except Exception:
                pass
        else:
            print("[OK] No password needed - auto-signed in!")
    else:
        print("[OK] No login page - already authenticated!")

    final_url = page.url
    print(f"\nFinal URL: {final_url[:150]}")
    title = await page.title()
    print(f"Title: {title}")
    await page.screenshot(path="/data/errors/outlook_final.png")

    if "mail" in final_url.lower() and "login" not in final_url.lower():
        print("\nSUCCESS - In Outlook mailbox!")
        # Try to read some emails
        await asyncio.sleep(3)
        rows = page.locator("[aria-label*='message'], [role='option'], [data-convid]")
        count = await rows.count()
        print(f"Email rows found: {count}")
        for i in range(min(5, count)):
            try:
                text = await rows.nth(i).text_content()
                if text:
                    clean = text.strip().replace("\n", " ")[:100]
                    print(f"  [{i+1}] {clean}")
            except Exception:
                pass

        # Test sent folder
        print("\n[5] Checking Sent folder...")
        await page.goto("https://outlook.office.com/mail/sentitems", wait_until="domcontentloaded")
        await asyncio.sleep(5)
        print(f"Sent URL: {page.url[:100]}")
        await page.screenshot(path="/data/errors/outlook_sent.png")
    elif "login" not in final_url.lower():
        print("\nPASSED LOGIN - landed somewhere else")
    else:
        print("\nFAILED - Still on login page")

    await ctx.close()
    await pw.stop()

asyncio.run(run())
