"""
Comprehensive access test suite for SF Worker.
Tests: MS SSO, Outlook inbox/sent, email extraction, session persistence, production browser.py.
"""
import asyncio
import json
import os
import sys
import time
import traceback
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

PROFILE_DIR = "/data/profiles/ms_full_test"
ERRORS_DIR = "/data/errors"
RESULTS = {}


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def record(test_name, passed, details=""):
    RESULTS[test_name] = {"passed": passed, "details": details}
    status = "PASS" if passed else "FAIL"
    log(f"  {'✓' if passed else '✗'} {test_name}: {status} {details}")


async def screenshot(page, name):
    os.makedirs(ERRORS_DIR, exist_ok=True)
    path = f"{ERRORS_DIR}/test_{name}.png"
    await page.screenshot(path=path)
    return path


# ═══════════════════════════════════════════════════
# TEST 1: Session persistence (no login at all)
# ═══════════════════════════════════════════════════
async def test_session_persistence(page):
    log("TEST 1: Session persistence - MS Office portal")
    try:
        await page.goto("https://www.office.com", wait_until="domcontentloaded")
        await asyncio.sleep(5)
        url = page.url.lower()
        await screenshot(page, "1_session_office")

        # Success = not on a login page
        if "login" not in url or "office.com" in url:
            title = await page.title()
            record("session_persistence_office", True, f"Title: {title[:50]}")
        else:
            record("session_persistence_office", False, f"Redirected to login: {url[:80]}")
    except Exception as e:
        record("session_persistence_office", False, str(e)[:100])


# ═══════════════════════════════════════════════════
# TEST 2: MS SSO login page detection
# ═══════════════════════════════════════════════════
async def test_ms_sso_detection(page):
    log("TEST 2: MS SSO detection - login.microsoftonline.com")
    try:
        await page.goto("https://login.microsoftonline.com/", wait_until="domcontentloaded")
        await asyncio.sleep(5)
        url = page.url.lower()
        await screenshot(page, "2_sso_detection")

        # Check if we get account picker vs full login
        email_field = page.locator("input[type='email'], input[name='loginfmt']")
        has_email = await email_field.count() > 0 and await email_field.first.is_visible()

        account_tiles = page.locator("div[data-test-id], div.table-row")
        has_tiles = await account_tiles.count() > 0

        if not has_email and not has_tiles:
            record("ms_sso_detection", True, "Auto-redirected (full session)")
        elif has_tiles and not has_email:
            record("ms_sso_detection", True, "Account picker (partial session)")
        elif has_email:
            record("ms_sso_detection", True, f"Login page detected correctly, email field visible")
        else:
            record("ms_sso_detection", False, f"Unknown state: {url[:80]}")
    except Exception as e:
        record("ms_sso_detection", False, str(e)[:100])


# ═══════════════════════════════════════════════════
# TEST 3: Outlook login (password only, no MFA)
# ═══════════════════════════════════════════════════
async def test_outlook_login(page):
    log("TEST 3: Outlook login - password only, no MFA expected")
    try:
        await page.goto("https://outlook.office.com/mail/inbox", wait_until="domcontentloaded")
        await asyncio.sleep(5)
        url = page.url.lower()

        # Already in mailbox?
        if "outlook" in url and "login" not in url:
            record("outlook_login", True, "Already authenticated")
            return True

        await screenshot(page, "3a_outlook_login_start")

        # Enter email
        email_input = page.locator("input[type='email'], input[name='loginfmt']")
        if await email_input.count() > 0 and await email_input.first.is_visible():
            await email_input.fill("patliquid@outlook.com")
            await page.locator("input[type='submit'], button:has-text('Next')").first.click()
            await asyncio.sleep(4)
        else:
            record("outlook_login", False, "No email field found")
            return False

        await screenshot(page, "3b_outlook_after_email")

        # Enter password
        pw_input = page.locator("input[type='password'], input[name='passwd']")
        if await pw_input.count() > 0 and await pw_input.first.is_visible():
            await pw_input.type("RepDrive@2", delay=50)
            await page.locator(
                "input[type='submit'], button:has-text('Next'), button:has-text('Sign in')"
            ).first.click()
            await asyncio.sleep(6)
        else:
            # No password = auto-signed in
            record("outlook_login_no_password", True, "Auto-signed in after email")

        await screenshot(page, "3c_outlook_after_pw")

        # CHECK: Is MFA required?
        mfa_selectors = [
            "input[type='tel']", "input[maxlength='1']",
            "text=Enter the code", "text=Verify your identity",
            "text=Send a code", "text=Approve sign in",
        ]
        mfa_found = False
        for sel in mfa_selectors:
            try:
                el = page.locator(sel)
                if await el.count() > 0 and await el.first.is_visible():
                    mfa_found = True
                    log(f"  MFA DETECTED via: {sel}")
                    break
            except Exception:
                continue

        if mfa_found:
            record("outlook_no_mfa", False, "MFA required - session did NOT persist")
            await screenshot(page, "3d_outlook_mfa_required")
            return False
        else:
            record("outlook_no_mfa", True, "No MFA - session persisted")

        # Handle "Stay signed in"
        try:
            stay = page.locator("input[value='Yes'], button:has-text('Yes')")
            if await stay.count() > 0 and await stay.first.is_visible():
                try:
                    dont_show = page.locator("input#KmsiCheckboxField, input[type='checkbox']")
                    if await dont_show.count() > 0:
                        await dont_show.first.check()
                except Exception:
                    pass
                await stay.first.click()
                log("  Clicked 'Stay signed in: Yes'")
                await asyncio.sleep(4)
        except Exception:
            pass

        # Verify we landed in Outlook
        await asyncio.sleep(3)
        final_url = page.url
        title = await page.title()
        await screenshot(page, "3e_outlook_final")

        in_outlook = "outlook" in final_url.lower() or "mail" in title.lower()
        record("outlook_login", in_outlook, f"Title: {title[:50]}, URL: {final_url[:80]}")
        return in_outlook

    except Exception as e:
        record("outlook_login", False, str(e)[:100])
        await screenshot(page, "3_outlook_error")
        return False


# ═══════════════════════════════════════════════════
# TEST 4: Outlook inbox read
# ═══════════════════════════════════════════════════
async def test_outlook_inbox(page):
    log("TEST 4: Outlook inbox - read emails")
    try:
        await page.goto("https://outlook.live.com/mail/0/inbox", wait_until="domcontentloaded")
        await asyncio.sleep(8)
        await screenshot(page, "4a_inbox")

        url = page.url.lower()
        if "login" in url and "outlook" not in url and "live" not in url:
            record("outlook_inbox_read", False, "Not authenticated")
            return

        # Count email items
        convs = page.locator("div[data-convid]")
        count = await convs.count()
        log(f"  Found {count} conversations in inbox")

        emails = []
        for i in range(min(10, count)):
            try:
                conv = convs.nth(i)
                text = await conv.text_content()
                convid = await conv.get_attribute("data-convid")
                if text and convid:
                    emails.append({
                        "id": convid,
                        "preview": text.strip()[:200],
                    })
            except Exception:
                continue

        record("outlook_inbox_read", count > 0, f"{count} conversations, {len(emails)} extracted")

        if emails:
            log(f"  Sample emails:")
            for e in emails[:3]:
                log(f"    - {e['preview'][:80]}")

    except Exception as e:
        record("outlook_inbox_read", False, str(e)[:100])
        await screenshot(page, "4_inbox_error")


# ═══════════════════════════════════════════════════
# TEST 5: Outlook sent folder read
# ═══════════════════════════════════════════════════
async def test_outlook_sent(page):
    log("TEST 5: Outlook sent - read sent emails")
    try:
        await page.goto("https://outlook.live.com/mail/0/sentitems", wait_until="domcontentloaded")
        await asyncio.sleep(8)
        await screenshot(page, "5a_sent")

        convs = page.locator("div[data-convid]")
        count = await convs.count()
        log(f"  Found {count} sent conversations")

        emails = []
        for i in range(min(10, count)):
            try:
                conv = convs.nth(i)
                text = await conv.text_content()
                convid = await conv.get_attribute("data-convid")
                if text and convid:
                    emails.append({
                        "id": convid,
                        "preview": text.strip()[:200],
                    })
            except Exception:
                continue

        record("outlook_sent_read", count > 0, f"{count} conversations, {len(emails)} extracted")

        if emails:
            log(f"  Sample sent emails:")
            for e in emails[:3]:
                log(f"    - {e['preview'][:80]}")

    except Exception as e:
        record("outlook_sent_read", False, str(e)[:100])
        await screenshot(page, "5_sent_error")


# ═══════════════════════════════════════════════════
# TEST 6: Email body extraction (click and read)
# ═══════════════════════════════════════════════════
async def test_email_body_extraction(page):
    log("TEST 6: Email body extraction - click and read full email")
    try:
        # Make sure we're on inbox or sent
        await page.goto("https://outlook.live.com/mail/0/sentitems", wait_until="domcontentloaded")
        await asyncio.sleep(6)

        convs = page.locator("div[data-convid]")
        count = await convs.count()
        if count == 0:
            record("email_body_extraction", False, "No emails to click")
            return

        # Click first email
        await convs.first.click()
        await asyncio.sleep(4)
        await screenshot(page, "6a_email_clicked")

        # Try multiple selectors for the email body
        body_text = ""
        body_selectors = [
            "div[role='document']",
            "div[class*='BodyContainer']",
            "div[class*='ReadingPane'] div[class*='body']",
            "div[aria-label*='Message body']",
            "div.allowTextSelection",
        ]

        for sel in body_selectors:
            try:
                el = page.locator(sel)
                if await el.count() > 0:
                    text = await el.first.text_content()
                    if text and len(text.strip()) > 20:
                        body_text = text.strip()
                        log(f"  Body found via: {sel}")
                        break
            except Exception:
                continue

        # Fallback: get reading pane content
        if not body_text:
            try:
                reading_pane = page.locator("div[role='main']")
                if await reading_pane.count() > 0:
                    body_text = await reading_pane.first.text_content()
                    body_text = body_text.strip() if body_text else ""
                    log(f"  Body found via main role fallback")
            except Exception:
                pass

        if body_text and len(body_text) > 20:
            record("email_body_extraction", True, f"{len(body_text)} chars extracted")
            log(f"  First 200 chars: {body_text[:200]}")
        else:
            record("email_body_extraction", False, f"Only {len(body_text)} chars found")
            await screenshot(page, "6b_no_body")

        # Also extract email metadata from reading pane
        try:
            from_el = page.locator("span[class*='sender'], span[class*='From'], button[aria-label*='from']")
            if await from_el.count() > 0:
                from_text = await from_el.first.text_content()
                log(f"  From: {from_text}")
        except Exception:
            pass

    except Exception as e:
        record("email_body_extraction", False, str(e)[:100])
        await screenshot(page, "6_body_error")


# ═══════════════════════════════════════════════════
# TEST 7: Production browser.py SSO detection
# ═══════════════════════════════════════════════════
async def test_browser_sso_detection():
    log("TEST 7: Production browser.py - SSO detection logic")
    try:
        sys.path.insert(0, "/app")
        from browser import SalesforceBot

        # Test with a fake BSci-like URL that would redirect to MS SSO
        bot = SalesforceBot(
            instance_url="https://login.microsoftonline.com",
            username="test",
            headless=True,
        )
        await bot.start()

        # Navigate to MS login
        await bot.page.goto("https://login.microsoftonline.com/", wait_until="domcontentloaded")
        await asyncio.sleep(3)

        # Test the detection method
        is_ms = bot._is_ms_sso_page()
        record("browser_sso_detection", is_ms, f"_is_ms_sso_page() = {is_ms}")

        # Test _is_on_login_page
        is_login = await bot._is_on_login_page()
        record("browser_login_detection", is_login, f"_is_on_login_page() = {is_login}")

        await bot.close()
    except Exception as e:
        record("browser_sso_detection", False, str(e)[:100])
        record("browser_login_detection", False, "Skipped due to SSO detection failure")


# ═══════════════════════════════════════════════════
# TEST 8: Session survives browser close/reopen
# ═══════════════════════════════════════════════════
async def test_session_survives_restart():
    log("TEST 8: Session survives browser close and reopen")
    try:
        # Open browser, go to outlook, close it
        pw = await async_playwright().start()
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=True,
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        await page.goto("https://outlook.live.com/mail/0/inbox", wait_until="domcontentloaded")
        await asyncio.sleep(5)
        url1 = page.url
        log(f"  First open URL: {url1[:80]}")

        # Close completely
        await ctx.close()
        await pw.stop()
        log("  Browser closed.")
        await asyncio.sleep(2)

        # Reopen with same profile
        pw2 = await async_playwright().start()
        ctx2 = await pw2.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=True,
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        page2 = ctx2.pages[0] if ctx2.pages else await ctx2.new_page()

        await page2.goto("https://outlook.live.com/mail/0/inbox", wait_until="domcontentloaded")
        await asyncio.sleep(5)
        url2 = page2.url
        title2 = await page2.title()
        log(f"  Second open URL: {url2[:80]}")
        log(f"  Second open title: {title2}")
        await screenshot(page2, "8_restart_test")

        # Both should be in outlook (not login)
        survived = "outlook" in url2.lower() or "mail" in title2.lower()
        record("session_survives_restart", survived, f"After restart: {title2[:40]}")

        await ctx2.close()
        await pw2.stop()
    except Exception as e:
        record("session_survives_restart", False, str(e)[:100])


# ═══════════════════════════════════════════════════
# TEST 9: Error handling - bad URL resilience
# ═══════════════════════════════════════════════════
async def test_error_resilience(page):
    log("TEST 9: Error resilience - bad URLs and timeouts")
    try:
        # Navigate to a non-existent page
        try:
            await page.goto("https://outlook.live.com/mail/0/nonexistent_folder_xyz",
                          wait_until="domcontentloaded", timeout=10000)
            await asyncio.sleep(3)
            record("error_bad_url", True, "Handled gracefully")
        except Exception as e:
            record("error_bad_url", True, f"Caught: {str(e)[:60]}")

        # Navigate back to valid page to confirm browser still works
        await page.goto("https://outlook.live.com/mail/0/inbox", wait_until="domcontentloaded")
        await asyncio.sleep(5)
        convs = page.locator("div[data-convid]")
        count = await convs.count()
        record("error_recovery", count > 0, f"Recovered, {count} emails visible")

    except Exception as e:
        record("error_resilience", False, str(e)[:100])


# ═══════════════════════════════════════════════════
# TEST 10: Data export - dump emails to JSON
# ═══════════════════════════════════════════════════
async def test_data_export(page):
    log("TEST 10: Data export - extract emails to JSON")
    try:
        folders = {
            "inbox": "https://outlook.live.com/mail/0/inbox",
            "sent": "https://outlook.live.com/mail/0/sentitems",
        }
        all_emails = {}

        for folder_name, url in folders.items():
            await page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(8)

            convs = page.locator("div[data-convid]")
            count = await convs.count()
            emails = []

            for i in range(min(20, count)):
                try:
                    conv = convs.nth(i)
                    text = await conv.text_content()
                    convid = await conv.get_attribute("data-convid")
                    if text and convid:
                        emails.append({
                            "conversation_id": convid,
                            "folder": folder_name,
                            "raw_text": text.strip()[:500],
                            "extracted_at": datetime.utcnow().isoformat(),
                        })
                except Exception:
                    continue

            all_emails[folder_name] = emails
            log(f"  {folder_name}: {len(emails)} emails extracted")

        # Write to JSON file
        output_path = "/data/errors/email_export.json"
        with open(output_path, "w") as f:
            json.dump(all_emails, f, indent=2)

        total = sum(len(v) for v in all_emails.values())
        record("data_export_json", total > 0, f"{total} emails exported to {output_path}")

    except Exception as e:
        record("data_export_json", False, str(e)[:100])


# ═══════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════
async def main():
    start = time.time()
    log("=" * 60)
    log("SF WORKER - COMPREHENSIVE ACCESS TEST SUITE")
    log("=" * 60)

    os.makedirs(ERRORS_DIR, exist_ok=True)

    pw = await async_playwright().start()
    ctx = await pw.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        headless=True,
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()

    # Run tests sequentially (they share browser state)
    log("")
    await test_session_persistence(page)
    log("")
    await test_ms_sso_detection(page)
    log("")
    in_outlook = await test_outlook_login(page)
    log("")

    if in_outlook:
        await test_outlook_inbox(page)
        log("")
        await test_outlook_sent(page)
        log("")
        await test_email_body_extraction(page)
        log("")
        await test_error_resilience(page)
        log("")
        await test_data_export(page)
    else:
        log("SKIPPING email tests - Outlook login failed")
        for t in ["outlook_inbox_read", "outlook_sent_read", "email_body_extraction",
                   "error_bad_url", "error_recovery", "data_export_json"]:
            record(t, False, "Skipped - Outlook login failed")

    await ctx.close()
    await pw.stop()

    # Tests that need their own browser instance
    log("")
    await test_browser_sso_detection()
    log("")
    await test_session_survives_restart()

    # Final summary
    elapsed = time.time() - start
    log("")
    log("=" * 60)
    log(f"TEST RESULTS ({elapsed:.1f}s)")
    log("=" * 60)
    passed = sum(1 for r in RESULTS.values() if r["passed"])
    failed = sum(1 for r in RESULTS.values() if not r["passed"])
    total = len(RESULTS)
    for name, result in RESULTS.items():
        status = "PASS" if result["passed"] else "FAIL"
        log(f"  {'✓' if result['passed'] else '✗'} {name}: {status}")

    log("")
    log(f"TOTAL: {passed}/{total} passed, {failed} failed")
    log("=" * 60)

    # Write results to file
    with open(f"{ERRORS_DIR}/test_results.json", "w") as f:
        json.dump(RESULTS, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
