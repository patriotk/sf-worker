"""
Full Microsoft SSO login in one shot.
Waits for MFA code via /tmp/ms_code.txt file.
"""
import asyncio
import os
from playwright.async_api import async_playwright

async def run():
    pw = await async_playwright().start()

    import shutil
    profile = "/data/profiles/ms_full_test"
    if os.path.exists(profile):
        shutil.rmtree(profile)
    os.makedirs(profile)

    code_file = "/tmp/ms_code.txt"
    if os.path.exists(code_file):
        os.remove(code_file)

    ctx = await pw.chromium.launch_persistent_context(
        user_data_dir=profile,
        headless=True,
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()

    print("[1] Navigate to Microsoft login...")
    await page.goto("https://login.microsoftonline.com/", wait_until="domcontentloaded")
    await asyncio.sleep(3)

    print("[2] Email...")
    email_input = page.locator("input[type='email'], input[name='loginfmt']")
    await email_input.wait_for(state="visible", timeout=10000)
    await email_input.fill("patliquid@outlook.com")
    await page.locator("input[type='submit'], button:has-text('Next')").first.click()
    await asyncio.sleep(4)

    print("[3] Password...")
    pw_input = page.locator("input[type='password'], input[name='passwd']")
    await pw_input.wait_for(state="visible", timeout=10000)
    await pw_input.type("RepDrive@2", delay=50)
    await page.locator("input[type='submit'], button:has-text('Next'), button:has-text('Sign in')").first.click()
    await asyncio.sleep(6)

    print("[4] Clicking liquidsmarts option...")
    option = page.locator("text=liquidsmarts").first
    await option.click(force=True)
    await asyncio.sleep(5)

    print("[5] Filling verification email...")
    verify_input = page.locator("input").first
    await verify_input.click()
    await verify_input.fill("patriot@liquidsmarts.com")
    await asyncio.sleep(1)

    print("[6] Clicking Send code...")
    send_btn = page.locator("button:has-text('Send code'), input[value='Send code']")
    await send_btn.first.click()
    await asyncio.sleep(5)
    await page.screenshot(path="/data/errors/full_06_after_send.png")

    # Wait for code
    print("[7] WAITING FOR CODE...")
    print("    Write code: docker exec sf-worker_worker_1 bash -c 'echo XXXXXX > /tmp/ms_code.txt'")
    code = None
    for i in range(120):
        if os.path.exists(code_file):
            with open(code_file) as f:
                code = f.read().strip()
            if code:
                print(f"    Got code: {code}")
                break
        if i % 12 == 0 and i > 0:
            print(f"    Still waiting... ({i * 5}s)")
        await asyncio.sleep(5)

    if not code:
        print("[!] Timed out")
        await ctx.close()
        await pw.stop()
        return

    # Enter code digit by digit using keyboard
    print(f"[8] Typing code digit by digit: {code}")
    # Click on the first input box to focus it
    code_boxes = page.locator("input[type='tel'], input[maxlength='1'], input[aria-label*='digit'], input[autocomplete='one-time-code']")
    count = await code_boxes.count()
    print(f"    Found {count} code boxes")

    if count >= 6:
        # Click first box and type each digit
        await code_boxes.first.click()
        await asyncio.sleep(0.5)
        for digit in code:
            await page.keyboard.type(digit, delay=100)
            await asyncio.sleep(0.3)
    else:
        # Fallback: just type the whole code via keyboard on whatever is focused
        print("    Using keyboard fallback...")
        # Click somewhere on the code area
        try:
            area = page.locator("text=Enter the code")
            if await area.count() > 0:
                await area.click()
        except:
            pass
        await page.keyboard.type(code, delay=150)

    print("[9] Code entered, waiting for auto-submit or clicking verify...")
    await asyncio.sleep(5)
    await page.screenshot(path="/data/errors/full_09_after_code.png")

    # If there's a verify button, click it
    try:
        verify_btn = page.locator("button:has-text('Verify'), input[type='submit'], button[type='submit']")
        if await verify_btn.count() > 0 and await verify_btn.first.is_visible():
            await verify_btn.first.click()
            print("    Clicked verify button")
            await asyncio.sleep(5)
    except:
        print("    No verify button (probably auto-submitted)")

    # Stay signed in?
    try:
        stay = page.locator("input[value='Yes'], button:has-text('Yes')")
        if await stay.count() > 0 and await stay.first.is_visible():
            await stay.first.click()
            print("[10] Clicked Stay signed in: Yes")
            await asyncio.sleep(3)
    except:
        pass

    await page.screenshot(path="/data/errors/full_11_final.png")
    print(f"[DONE] Final URL: {page.url}")

    url = page.url.lower()
    if "office.com" in url and "login" not in url:
        print("SUCCESS! Logged into Microsoft Office!")
    elif "salesforce" in url:
        print("SUCCESS! Redirected to Salesforce!")
    elif "login" not in url:
        print("SUCCESS! Passed login!")
    else:
        print(f"Still on login: {url[:100]}")

    await ctx.close()
    await pw.stop()

asyncio.run(run())
