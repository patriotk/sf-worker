import asyncio
import logging
import os
import random
import re
from datetime import datetime
from playwright.async_api import async_playwright, Page, BrowserContext, TimeoutError as PlaywrightTimeout

log = logging.getLogger("salesforce_bot")

try:
    from config import PROFILES_DIR, ERRORS_DIR
except ImportError:
    PROFILES_DIR = "profiles"
    ERRORS_DIR = "errors"


async def _human_delay(min_s: float = 0.5, max_s: float = 2.0):
    """Random delay to mimic human browsing patterns."""
    await asyncio.sleep(random.uniform(min_s, max_s))


def _to_sf_date(date_str: str) -> str:
    """Convert YYYY-MM-DD to MM/DD/YYYY for Salesforce date fields."""
    if not date_str:
        return ""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%m/%d/%Y")
    except ValueError:
        return date_str  # Already in correct format or unknown


class SalesforceBot:
    def __init__(self, instance_url: str, username: str, headless: bool = True):
        self.instance_url = instance_url.rstrip("/")
        self.username = username
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def _profile_dir(self) -> str:
        """Per-user persistent browser profile directory."""
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", self.username.lower())
        return os.path.join(PROFILES_DIR, safe_name)

    async def start(self):
        """Launch browser with a persistent profile so login/MFA trust survives across runs."""
        profile = self._profile_dir()
        os.makedirs(profile, exist_ok=True)
        log.info("Launching browser (headless=%s) with profile: %s", self.headless, profile)

        self.playwright = await async_playwright().start()

        # Persistent context = real Chrome profile. Cookies, localStorage, IndexedDB all persist.
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=profile,
            headless=self.headless,
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )

        # Use existing page or create one
        if self.context.pages:
            self.page = self.context.pages[0]
        else:
            self.page = await self.context.new_page()

    async def close(self):
        log.info("Closing browser")
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()

    # ──────────────────────────────────────────────
    # Utility helpers
    # ──────────────────────────────────────────────

    async def _screenshot(self, label: str) -> str:
        try:
            os.makedirs(ERRORS_DIR, exist_ok=True)
            ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            path = os.path.join(ERRORS_DIR, f"{ts}_{label}.png")
            await self.page.screenshot(path=path)
            log.info("Screenshot: %s", path)
            return path
        except Exception as e:
            log.error("Screenshot failed: %s", e)
            return ""

    async def _retry(self, action, max_retries: int = 2):
        last_err = None
        for attempt in range(max_retries + 1):
            try:
                return await action()
            except PlaywrightTimeout as e:
                last_err = e
                if attempt == max_retries:
                    raise
                log.warning("Attempt %d/%d timed out. Retrying...", attempt + 1, max_retries + 1)
                await asyncio.sleep(2)
            except Exception as e:
                if "Navigation failed" in str(e) and attempt < max_retries:
                    last_err = e
                    log.warning("Attempt %d/%d nav failed. Retrying...", attempt + 1, max_retries + 1)
                    await asyncio.sleep(2)
                    continue
                raise
        raise last_err

    async def _is_on_lightning(self) -> bool:
        try:
            await self.page.wait_for_selector(
                "button:has-text('Search'), nav[aria-label='Main']", timeout=15000
            )
            return True
        except PlaywrightTimeout:
            return False

    async def _is_on_login_page(self) -> bool:
        url = self.page.url.lower()
        if "login.salesforce" in url:
            return True
        # Only match /login in Salesforce URLs, not in query params
        if ".salesforce.com/login" in url or ".force.com/login" in url:
            return True
        # Microsoft SSO pages are also login pages
        if self._is_ms_sso_page():
            return True
        # Also check for login form elements on the page
        try:
            username_field = self.page.locator("#username")
            if await username_field.count() > 0 and await username_field.is_visible():
                log.info("Login page detected (found #username field)")
                return True
        except Exception:
            pass
        return False

    async def _wait_lightning(self, timeout: int = 20000):
        """Wait for Lightning shell to be ready."""
        await self.page.wait_for_selector(
            "button:has-text('Search'), nav[aria-label='Main']", timeout=timeout
        )

    async def _find_visible_dialog(self, names: list[str], timeout: int = 15000):
        """Find a visible dialog by trying multiple name variants."""
        # First try each name directly
        for name in names:
            dialog = self.page.get_by_role("dialog", name=name)
            try:
                if await dialog.count() > 0 and await dialog.is_visible():
                    log.info("Found dialog: %s", name)
                    return dialog
            except Exception:
                continue

        # Wait for any of them to appear
        for name in names:
            try:
                dialog = self.page.get_by_role("dialog", name=name)
                await dialog.wait_for(state="visible", timeout=timeout // len(names))
                log.info("Found dialog (after wait): %s", name)
                return dialog
            except PlaywrightTimeout:
                continue

        # Last resort: any visible non-auraError dialog
        try:
            dialogs = self.page.locator("div[role='dialog']:visible:not(#auraError)")
            if await dialogs.count() > 0:
                log.info("Found dialog via generic selector")
                return dialogs.first
        except Exception:
            pass

        return None

    async def _close_any_dialog(self):
        """Close any open modal/dialog."""
        for name in ["Log a Call", "New Contact", "New Account", "New Task", "New Event", "New Note"]:
            try:
                dialog = self.page.get_by_role("dialog", name=name)
                if await dialog.count() > 0 and await dialog.is_visible():
                    close_btn = dialog.locator("button:has-text('Close'), button[title='Close']")
                    if await close_btn.count() > 0:
                        await close_btn.first.click()
                        await asyncio.sleep(1)
                        log.info("Closed dialog: %s", name)
            except Exception:
                continue

    async def _fill_field(self, container, label: str, value: str):
        """Fill a text input or textarea by label inside a container."""
        if not value:
            return
        # Try input first
        field = container.get_by_label(label, exact=False)
        try:
            if await field.count() > 0:
                await field.scroll_into_view_if_needed()
                await asyncio.sleep(0.3)
                await field.click()
                await asyncio.sleep(0.2)
                await field.fill(value)
                await asyncio.sleep(0.2)
                log.info("Filled '%s': %s", label, value[:50])
                return
        except Exception:
            pass
        # Try textarea
        ta = container.locator(f"textarea:near(:text('{label}'))").first
        try:
            if await ta.count() > 0:
                await ta.scroll_into_view_if_needed()
                await asyncio.sleep(0.3)
                await ta.click()
                await ta.fill(value)
                log.info("Filled '%s' (textarea): %s", label, value[:50])
        except Exception:
            log.warning("Could not fill field '%s'", label)

    async def _select_picklist(self, container, label: str, value: str):
        """Select a value from a Salesforce picklist/combobox."""
        if not value:
            return
        combo = container.get_by_role("combobox", name=label)
        try:
            if await combo.count() > 0:
                await combo.click()
                await asyncio.sleep(0.5)
                option = container.get_by_role("option", name=value)
                if await option.count() > 0:
                    await option.first.click()
                    log.info("Selected '%s' = '%s'", label, value)
                else:
                    # Type it in
                    await combo.fill(value)
                    await combo.press("Tab")
                    log.info("Typed '%s' = '%s' (no matching option)", label, value)
                await asyncio.sleep(0.3)
                return
        except Exception:
            pass
        log.warning("Could not select picklist '%s' = '%s'", label, value)

    async def _fill_lookup(self, container, label: str, value: str):
        """Fill a Salesforce lookup/combobox field and pick from dropdown or Advanced Search."""
        if not value:
            return
        combo = container.get_by_role("combobox", name=label)
        try:
            if await combo.count() == 0:
                raise Exception("No combobox found")
            await combo.scroll_into_view_if_needed()
            await combo.click()
            await combo.fill(value)
            await asyncio.sleep(2)

            # Always check for Advanced Search FIRST (it can open on top of everything)
            adv_search = self.page.locator("div:has(> h2:text('Advanced Search'))").first
            adv_dialog_visible = False
            try:
                # Check multiple ways for the Advanced Search overlay
                for sel in [
                    "h2:text('Advanced Search')",
                    "div.modal-container:has-text('Advanced Search')",
                ]:
                    el = self.page.locator(sel)
                    if await el.count() > 0 and await el.first.is_visible():
                        adv_dialog_visible = True
                        break
            except Exception:
                pass

            if adv_dialog_visible:
                log.info("Advanced Search dialog detected for '%s'", label)
                await asyncio.sleep(1)
                # Click first radio button
                radio = self.page.locator("input[type='radio']").first
                if await radio.count() > 0:
                    await radio.click()
                    await asyncio.sleep(0.5)
                # Click Select button
                select_btn = self.page.get_by_role("button", name="Select")
                if await select_btn.count() > 0 and await select_btn.is_visible():
                    await select_btn.click()
                    await asyncio.sleep(1.5)
                    log.info("Selected lookup '%s' via Advanced Search: %s", label, value)
                    return
                # If Select not found, close and tab out
                cancel = self.page.get_by_role("button", name="Cancel")
                if await cancel.count() > 0:
                    await cancel.last.click()
                    await asyncio.sleep(0.5)

            # Try dropdown option (if no Advanced Search)
            option = self.page.get_by_role("option", name=value).first
            try:
                if await option.count() > 0 and await option.is_visible():
                    await option.click()
                    log.info("Selected lookup '%s': %s", label, value)
                    return
            except Exception:
                pass

            # Just tab out
            await combo.press("Tab")
            log.warning("Lookup '%s' = '%s' not found, typed directly", label, value)
            return
        except Exception as e:
            log.warning("Could not fill lookup '%s': %s", label, e)

    async def _fill_lookup_with_adv_search(self, container, label: str, value: str):
        """Fill a lookup field that may open a dropdown or Advanced Search dialog."""
        if not value:
            return
        combo = container.get_by_role("combobox", name=label)
        try:
            if await combo.count() == 0:
                log.warning("No combobox found for '%s'", label)
                return
            await combo.scroll_into_view_if_needed()
            await combo.click()
            await combo.fill(value)
            await asyncio.sleep(2)

            # Look for dropdown results under "Search Results" -- click the FIRST result
            # but NOT "Show more results" (which opens Advanced Search)
            search_results = self.page.locator("lightning-base-combobox-item[data-value]")
            if await search_results.count() > 0:
                # Click the first actual result (skip any header/action items)
                for i in range(await search_results.count()):
                    item = search_results.nth(i)
                    try:
                        text = await item.text_content()
                        if text and value.split()[0] in text and "Show more" not in text and "New " not in text:
                            await item.click()
                            await asyncio.sleep(1)
                            log.info("Selected lookup '%s' from dropdown: %s", label, value)
                            return
                    except Exception:
                        continue

            # Try option role but skip "Show more results"
            options = self.page.get_by_role("option")
            for i in range(await options.count()):
                opt = options.nth(i)
                try:
                    text = await opt.text_content()
                    if text and value.split()[0] in text and "Show more" not in text and "New " not in text:
                        await opt.click()
                        await asyncio.sleep(1)
                        log.info("Selected lookup '%s' via option: %s", label, value)
                        return
                except Exception:
                    continue

            # If we got here, close any dropdown and tab out
            await combo.press("Escape")
            await asyncio.sleep(0.3)
            await combo.press("Tab")
            log.warning("Lookup '%s' = '%s' -- typed directly", label, value)
        except Exception as e:
            log.warning("_fill_lookup_with_adv_search '%s' failed: %s", label, e)

    async def _fill_combobox_text(self, container, label: str, value: str):
        """Fill a combobox that accepts free text (like Task Subject)."""
        if not value:
            return
        combo = container.get_by_role("combobox", name=label)
        try:
            if await combo.count() > 0:
                await combo.scroll_into_view_if_needed()
                await combo.click()
                await combo.fill(value)
                await combo.press("Tab")
                await asyncio.sleep(0.3)
                log.info("Filled combobox '%s': %s", label, value[:50])
                return
        except Exception:
            pass
        # Fallback to regular input
        await self._fill_field(container, label, value)

    async def _click_save_and_wait(self, container=None, timeout: int = 20) -> bool:
        """Click Save and wait for success (dialog/modal closes)."""
        scope = container or self.page
        save_btn = scope.get_by_role("button", name="Save", exact=True)
        if await save_btn.count() == 0:
            save_btn = self.page.get_by_role("button", name="Save", exact=True).last
        await save_btn.click()
        log.info("Clicked Save")

        # Wait for the container (dialog/modal) to close
        if container:
            for _ in range(timeout):
                await asyncio.sleep(1)
                try:
                    if await container.count() == 0 or not await container.is_visible():
                        return True
                except Exception:
                    return True
            log.error("Save timed out -- dialog still open after %ds", timeout)
            await self._screenshot("save_timeout")
            return False

        # No container -- wait for URL change or toast
        await asyncio.sleep(3)
        return True

    # ──────────────────────────────────────────────
    # Authentication
    # ──────────────────────────────────────────────

    def _is_ms_sso_page(self) -> bool:
        """Check if current page is a Microsoft SSO login page (personal or enterprise)."""
        url = self.page.url.lower()
        return (
            "login.microsoftonline.com" in url
            or "login.live.com" in url
            or "adfs" in url  # On-prem Active Directory Federation Services
            or "sts." in url  # Security Token Service (enterprise SSO)
        )

    async def login(self, username: str, password: str, mfa_code: str | None = None,
                    mfa_code_callback=None, verification_email: str | None = None) -> bool:
        log.info("Logging in as %s", username)

        # Always navigate fresh to the login page to avoid stale state
        await self.page.goto(self.instance_url, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        # Detect Microsoft SSO redirect
        if self._is_ms_sso_page():
            log.info("Microsoft SSO detected, handling MS login flow")
            return await self._login_microsoft_sso(
                username, password,
                mfa_code_callback=mfa_code_callback,
                verification_email=verification_email,
            )

        # Standard Salesforce login
        return await self._login_salesforce_direct(username, password, mfa_code, mfa_code_callback)

    async def _login_salesforce_direct(self, username: str, password: str,
                                        mfa_code: str | None = None, mfa_code_callback=None) -> bool:
        """Standard Salesforce login (username/password on login.salesforce.com)."""
        # Handle identity confirmation page (username pre-filled + hidden)
        username_el = self.page.locator("#username")
        password_el = self.page.locator("#password")

        try:
            await password_el.wait_for(state="visible", timeout=15000)
        except PlaywrightTimeout:
            log.error("Neither username nor password field found")
            await self._screenshot("login_no_fields")
            return False

        # Fill username if visible, otherwise it's pre-filled (identity confirmation)
        try:
            if await username_el.is_visible():
                await username_el.fill("")
                await username_el.type(username, delay=50)
                log.info("Filled username")
            else:
                log.info("Username pre-filled (identity confirmation page)")
        except Exception:
            pass

        # Clear and type password (type() is more reliable than fill() for password fields)
        await password_el.fill("")
        await password_el.type(password, delay=50)
        log.info("Filled password (%d chars)", len(password))

        await asyncio.sleep(1)

        # Check "Remember Me"
        try:
            rm = self.page.locator("#rememberUn")
            if await rm.is_visible():
                await rm.check()
        except Exception:
            pass

        await self.page.click("#Login")

        try:
            await self.page.wait_for_url(
                lambda u: "/login" not in u.lower() and "login.salesforce" not in u.lower(),
                timeout=30000,
            )
        except PlaywrightTimeout:
            log.error("Login timed out")
            await self._screenshot("login_failed")
            return False

        mfa_result = await self._handle_mfa(mfa_code=mfa_code, mfa_code_callback=mfa_code_callback)
        if mfa_result is False:
            return False

        if not await self._is_on_lightning():
            log.warning("Lightning not fully detected, continuing...")

        log.info("Login successful")
        return True

    async def _login_microsoft_sso(self, username: str, password: str,
                                    mfa_code_callback=None,
                                    verification_email: str | None = None) -> bool:
        """Handle Microsoft SSO (Azure AD / ADFS / SAML2) login flow.

        Handles both personal and enterprise (Azure AD) Microsoft accounts.
        Flow: [account picker] → email → password → [MFA] → [stay signed in] → SF
        The persistent browser profile retains the MS session after first login.
        """
        try:
            # Step 0: Handle "Pick an account" page (enterprise SSO with cached sessions)
            try:
                pick_account = self.page.locator(
                    "div[data-test-id='otherTile'], "
                    "div.table[role='button']:has-text('Use another account'), "
                    "div#otherTileText"
                )
                if await pick_account.count() > 0 and await pick_account.first.is_visible():
                    # Try to click the matching account first
                    acct_tile = self.page.locator(f"div[data-test-id][role='button']:has-text('{username}')")
                    if await acct_tile.count() > 0:
                        await acct_tile.first.click()
                        log.info("[MS SSO] Selected existing account: %s", username)
                    else:
                        # Click "Use another account"
                        await pick_account.first.click()
                        log.info("[MS SSO] Clicked 'Use another account'")
                    await asyncio.sleep(3)

                    # After picking account, may go straight to password or back to email
                    if await self._check_ms_sso_complete():
                        return True
            except Exception:
                pass

            # Step 1: Email (may be skipped if account was picked above)
            email_input = self.page.locator("input[type='email'], input[name='loginfmt']")
            try:
                await email_input.wait_for(state="visible", timeout=8000)
                log.info("[MS SSO] Entering email...")
                await _human_delay(0.3, 0.8)
                await email_input.fill(username)
                await _human_delay(0.5, 1.0)
                await self.page.locator(
                    "input[type='submit'], button:has-text('Next')"
                ).first.click()
                await asyncio.sleep(4)
            except PlaywrightTimeout:
                log.info("[MS SSO] No email field -- may already be past email step")

            # Step 1b: Handle ADFS redirect (enterprise on-prem identity provider)
            url_now = self.page.url.lower()
            if "adfs" in url_now or "sts." in url_now:
                log.info("[MS SSO] ADFS/STS page detected, looking for credentials form")
                adfs_user = self.page.locator("input#userNameInput, input[name='UserName']")
                adfs_pass = self.page.locator("input#passwordInput, input[name='Password']")
                if await adfs_user.count() > 0 and await adfs_pass.count() > 0:
                    await adfs_user.fill(username)
                    await _human_delay(0.3, 0.8)
                    await adfs_pass.type(password, delay=50)
                    await _human_delay(0.3, 0.8)
                    submit = self.page.locator("span#submitButton, input[type='submit']")
                    if await submit.count() > 0:
                        await submit.first.click()
                    await asyncio.sleep(6)
                    if await self._check_ms_sso_complete():
                        return True

            # Step 2: Password
            log.info("[MS SSO] Entering password...")
            pw_input = self.page.locator("input[type='password'], input[name='passwd']")
            try:
                await pw_input.wait_for(state="visible", timeout=15000)
                await _human_delay(0.3, 0.8)
                await pw_input.type(password, delay=50)
                await _human_delay(0.5, 1.0)
                await self.page.locator(
                    "input[type='submit'], button:has-text('Next'), button:has-text('Sign in')"
                ).first.click()
                await asyncio.sleep(6)
            except PlaywrightTimeout:
                log.info("[MS SSO] No password field -- may have auto-signed in")

            # Check if we landed on Salesforce already (no MFA required)
            if await self._check_ms_sso_complete():
                return True

            # Step 3: MFA option selection (if present)
            # Microsoft shows verification options like "Send a code to pa***@liquidsmarts.com"
            # or an authenticator app option. We look for the email/SMS code option.
            mfa_option_clicked = False
            for selector in [
                "div[data-value='OneWaySMS']",
                "div[data-value='Email']",
                "div[role='button']:has-text('Send a code')",
                "div:has-text('Send a code to')",
            ]:
                try:
                    opt = self.page.locator(selector)
                    if await opt.count() > 0 and await opt.first.is_visible():
                        await opt.first.click(force=True)
                        log.info("[MS SSO] Clicked MFA option: %s", selector)
                        mfa_option_clicked = True
                        await asyncio.sleep(5)
                        break
                except Exception:
                    continue

            # If there's a specific org option to click (e.g. "liquidsmarts")
            if not mfa_option_clicked:
                try:
                    # Some SSO flows show org selection
                    org_options = self.page.locator("div[role='button'], div.table")
                    count = await org_options.count()
                    if count > 0:
                        await org_options.first.click(force=True)
                        log.info("[MS SSO] Clicked first org option")
                        await asyncio.sleep(5)
                except Exception:
                    pass

            # Check again if we're through
            if await self._check_ms_sso_complete():
                return True

            # Step 4: Verification email (if MS asks for email to send code to)
            try:
                verify_input = self.page.locator(
                    "input[type='email']:visible, input[name='EmailAddress']:visible, "
                    "input[placeholder*='email']:visible"
                )
                if await verify_input.count() > 0 and await verify_input.first.is_visible():
                    email_to_use = verification_email or username
                    log.info("[MS SSO] Filling verification email: %s", email_to_use)
                    await verify_input.first.click()
                    await verify_input.first.fill(email_to_use)
                    await asyncio.sleep(1)

                    # Click "Send code"
                    send_btn = self.page.locator(
                        "button:has-text('Send code'), input[value='Send code'], "
                        "input[type='submit']:has-text('Send')"
                    )
                    if await send_btn.count() > 0:
                        await send_btn.first.click()
                        log.info("[MS SSO] Clicked Send code")
                        await asyncio.sleep(5)
            except Exception as e:
                log.info("[MS SSO] No verification email step: %s", e)

            # Check again
            if await self._check_ms_sso_complete():
                return True

            # Step 5: Enter 6-digit MFA code (poll via callback)
            log.info("[MS SSO] Waiting for MFA code...")
            await self._screenshot("ms_sso_waiting_for_code")

            code = None
            for i in range(60):  # Poll up to 5 minutes
                if mfa_code_callback:
                    code = await mfa_code_callback()

                if code:
                    log.info("[MS SSO] Got MFA code, entering...")
                    entered = await self._enter_ms_mfa_code(code)
                    if entered:
                        await asyncio.sleep(5)
                        if await self._check_ms_sso_complete():
                            return True
                        # Code might have been wrong, try again
                        log.warning("[MS SSO] Code entry didn't complete login, will retry")
                    code = None

                await asyncio.sleep(5)
                if await self._check_ms_sso_complete():
                    return True
                if i % 4 == 3:
                    log.info("[MS SSO] Still waiting for MFA code... (%ds)", (i + 1) * 5)

            log.error("[MS SSO] MFA timeout (5 minutes)")
            await self._screenshot("ms_sso_mfa_timeout")
            return False

        except Exception as e:
            log.error("[MS SSO] Login failed: %s", e)
            await self._screenshot("ms_sso_error")
            return False

    async def _enter_ms_mfa_code(self, code: str) -> bool:
        """Enter a 6-digit MFA code on Microsoft's verification page.
        Handles both individual digit boxes and single-input code fields.
        """
        # Try individual digit boxes first (Microsoft's default for 6-digit codes)
        code_boxes = self.page.locator(
            "input[type='tel'], input[maxlength='1'], "
            "input[aria-label*='digit'], input[autocomplete='one-time-code']"
        )
        count = await code_boxes.count()

        if count >= 6:
            log.info("[MS SSO] Found %d code boxes, typing digit by digit", count)
            await code_boxes.first.click()
            await asyncio.sleep(0.5)
            for digit in code:
                await self.page.keyboard.type(digit, delay=100)
                await asyncio.sleep(0.3)
        else:
            # Try single input field (otc, tel, or named code field)
            single_input = self.page.locator(
                "input[name='otc'], input#iOttText, input[type='tel'], "
                "input[placeholder*='Code'], input[placeholder*='code']"
            )
            if await single_input.count() > 0 and await single_input.first.is_visible():
                log.info("[MS SSO] Found single code input, filling")
                await single_input.first.fill(code)
            else:
                # Fallback: type code via keyboard on whatever is focused
                log.info("[MS SSO] No code input found, typing via keyboard")
                await self.page.keyboard.type(code, delay=150)

        await asyncio.sleep(1)

        # Click verify/submit button
        for btn_sel in [
            "button:has-text('Verify')", "input[type='submit']",
            "button[type='submit']", "input[value='Verify']",
        ]:
            try:
                btn = self.page.locator(btn_sel)
                if await btn.count() > 0 and await btn.first.is_visible():
                    await btn.first.click()
                    log.info("[MS SSO] Clicked verify: %s", btn_sel)
                    return True
            except Exception:
                continue

        log.warning("[MS SSO] No verify button found (may auto-submit)")
        return True

    async def _check_ms_sso_complete(self) -> bool:
        """Check if Microsoft SSO flow completed and we're back on Salesforce/Office."""
        url = self.page.url.lower()

        # Handle consent/permissions prompt (enterprise Azure AD conditional access)
        try:
            accept_btn = self.page.locator(
                "input[value='Accept'], button:has-text('Accept'), "
                "input[value='Consent'], button:has-text('Continue')"
            )
            if await accept_btn.count() > 0 and await accept_btn.first.is_visible():
                await accept_btn.first.click()
                log.info("[MS SSO] Accepted consent/permissions prompt")
                await asyncio.sleep(3)
                url = self.page.url.lower()
        except Exception:
            pass

        # Check for "Stay signed in?" prompt and click Yes
        try:
            stay_btn = self.page.locator(
                "input[type='submit'][value='Yes'], button:has-text('Yes'), "
                "input[value='Yes']"
            )
            if await stay_btn.count() > 0 and await stay_btn.first.is_visible():
                # Also check "Don't show this again"
                try:
                    dont_show = self.page.locator(
                        "input[type='checkbox']:near(:text('Don\\'t show')), "
                        "input#KmsOptions, input#KmsiCheckboxField"
                    )
                    if await dont_show.count() > 0:
                        await dont_show.first.check()
                except Exception:
                    pass
                await stay_btn.first.click()
                log.info("[MS SSO] Clicked 'Stay signed in: Yes'")
                await asyncio.sleep(3)
                url = self.page.url.lower()
        except Exception:
            pass

        # Success conditions: landed on Salesforce Lightning
        if "lightning" in url and "login" not in url:
            log.info("[MS SSO] Login complete -- on Salesforce Lightning")
            return True

        # Salesforce classic or setup page (still a success)
        if ".salesforce.com" in url and "login" not in url:
            log.info("[MS SSO] Login complete -- on Salesforce (classic/setup)")
            return True

        # Still on MS login pages
        if any(x in url for x in [
            "login.microsoftonline.com", "login.live.com",
            "adfs", "sts.", "device.login.microsoftonline.com"
        ]):
            return False

        # On Office/M365 (shouldn't happen for SF SSO, but handle it)
        if "office.com" in url or "m365.cloud.microsoft" in url:
            log.info("[MS SSO] Landed on Office, navigating to Salesforce...")
            await self.page.goto(self.instance_url, wait_until="domcontentloaded")
            await asyncio.sleep(5)
            return "lightning" in self.page.url.lower()

        # Other URL -- might be a redirect in progress
        return False

    async def _handle_mfa(self, mfa_code: str | None = None, mfa_code_callback=None) -> bool | None:
        """Handle MFA verification. If mfa_code is provided, auto-enter it.
        If mfa_code_callback is provided, poll it for a code (async callable returning str|None).
        """
        mfa_input_selectors = ["input#emc", "input[name='otp']", "input[name='verificationCode']"]
        mfa_indicator_selectors = [
            "#save-device-checkbox", "button:has-text('Verify')",
            "text=Verify Your Identity", "text=Enter Verification Code",
        ]
        all_selectors = mfa_input_selectors + mfa_indicator_selectors

        mfa_found = False
        for sel in all_selectors:
            try:
                el = self.page.locator(sel)
                if await el.count() > 0 and await el.first.is_visible():
                    mfa_found = True
                    break
            except Exception:
                continue

        if not mfa_found:
            return None

        log.info("MFA DETECTED -- looking for verification code")

        # Auto-check trust checkboxes
        for sel in ["#save-device-checkbox", "input[name='rememberDevice']",
                     "input[type='checkbox']:near(:text('remember'))",
                     "input[type='checkbox']:near(:text('trust'))"]:
            try:
                cb = self.page.locator(sel)
                if await cb.count() > 0 and await cb.first.is_visible():
                    await cb.first.check()
                    log.info("Checked trust checkbox")
                    break
            except Exception:
                continue

        # Try to auto-enter MFA code
        code = mfa_code
        for i in range(60):  # Poll up to 5 minutes
            if not code and mfa_code_callback:
                code = await mfa_code_callback()

            if code:
                log.info("Got MFA code, entering it...")
                for sel in mfa_input_selectors:
                    try:
                        inp = self.page.locator(sel)
                        if await inp.count() > 0 and await inp.first.is_visible():
                            await inp.first.fill(code)
                            log.info("Filled MFA code into %s", sel)
                            break
                    except Exception:
                        continue

                # Click verify button
                for btn_sel in ["button:has-text('Verify')", "input[type='submit']",
                                "button#save", "input#save"]:
                    try:
                        btn = self.page.locator(btn_sel)
                        if await btn.count() > 0 and await btn.first.is_visible():
                            await btn.first.click()
                            log.info("Clicked verify button")
                            break
                    except Exception:
                        continue

                await asyncio.sleep(5)
                if await self._is_on_lightning():
                    log.info("MFA completed successfully")
                    return True
                if await self._is_on_login_page():
                    log.error("MFA failed -- wrong code or redirected to login")
                    return False
                # Code might have been wrong, clear it to try callback again
                code = None

            await asyncio.sleep(5)
            if await self._is_on_lightning():
                log.info("MFA completed")
                return True
            if await self._is_on_login_page():
                log.error("MFA failed -- redirected to login")
                return False
            if i % 4 == 3:
                log.info("Waiting for MFA code... (%ds)", (i + 1) * 5)

        log.error("MFA timeout (5 minutes)")
        await self._screenshot("mfa_timeout")
        return False

    async def ensure_logged_in(self) -> bool:
        """Check if we're logged into Salesforce. Tries current page first, then navigates."""
        log.info("Checking session...")

        # Check current URL first -- if already on Lightning, we're good
        current_url = self.page.url
        if "lightning" in current_url and "login" not in current_url.lower():
            try:
                await self.page.wait_for_selector(
                    "button:has-text('Search'), nav[aria-label='Main'], one-app-nav-bar", timeout=10000
                )
                log.info("Session active -- already on Lightning")
                return True
            except PlaywrightTimeout:
                log.info("On Lightning URL but elements not found, will navigate to verify")

        # If on Microsoft SSO page, session is expired
        if self._is_ms_sso_page():
            log.info("On Microsoft SSO page -- session expired, need re-login")
            return False

        # Navigate to home to verify session
        try:
            await self.page.goto(f"{self.instance_url}/lightning/page/home", wait_until="domcontentloaded")
        except Exception as e:
            log.warning("Navigation failed: %s, retrying...", e)
            await asyncio.sleep(2)
            await self.page.goto(f"{self.instance_url}/lightning/page/home", wait_until="domcontentloaded")
        await asyncio.sleep(3)

        if await self._is_on_login_page():
            log.info("Not logged in, need credentials")
            return False

        # Wait up to 30s for Lightning to fully load
        try:
            await self.page.wait_for_selector(
                "button:has-text('Search'), nav[aria-label='Main'], one-app-nav-bar", timeout=30000
            )
            log.info("Session active -- Lightning loaded")
            return True
        except PlaywrightTimeout:
            pass

        # Maybe still loading -- check URL
        url = self.page.url
        if "lightning" in url and "login" not in url.lower():
            log.info("Session likely active (on Lightning URL), waiting more...")
            await asyncio.sleep(5)
            return True

        log.info("Session unclear")
        return not await self._is_on_login_page()

    # ──────────────────────────────────────────────
    # Search
    # ──────────────────────────────────────────────

    async def search_record(self, name: str, object_prefix: str = "003") -> list[dict]:
        """Search for a record. object_prefix: 003=Contact, 001=Account, 00T=Task, 006=Opportunity."""
        search_name = name
        for prefix in ("Dr. ", "Mr. ", "Mrs. ", "Ms. ", "Prof. "):
            if search_name.startswith(prefix):
                search_name = search_name[len(prefix):]
                break

        log.info("Searching '%s' (object prefix: %s)", search_name, object_prefix)

        async def _do():
            # Always navigate to home to get a clean Lightning page
            await _human_delay(0.5, 1.5)
            await self.page.goto(f"{self.instance_url}/lightning/page/home", wait_until="domcontentloaded")
            await asyncio.sleep(5)

            # Try multiple ways to find the search button
            search_btn = None
            for strategy, fn in [
                ("role button", lambda: self.page.get_by_role("button", name="Search")),
                ("aria-label", lambda: self.page.locator("button[aria-label='Search']")),
                ("search icon", lambda: self.page.locator("button.slds-button:has(lightning-icon)")),
                ("global search", lambda: self.page.locator("[class*='search'] button, [class*='Search'] button").first),
                ("search input direct", lambda: self.page.locator("input[placeholder*='Search'], input[type='search']").first),
            ]:
                try:
                    el = fn()
                    if await el.count() > 0 and await el.first.is_visible():
                        search_btn = el.first
                        log.info("Found search via: %s", strategy)
                        break
                except Exception:
                    continue

            if search_btn is None:
                log.error("Could not find search button")
                await self._screenshot("no_search_button")
                raise PlaywrightTimeout("Search button not found")

            await search_btn.click()

            search_input = self.page.get_by_role("searchbox", name="Search...")
            await search_input.wait_for(state="visible", timeout=5000)
            await search_input.fill(search_name)
            await search_input.press("Enter")

            await self.page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(3)

            try:
                await self.page.wait_for_selector("a[data-refid='recordId'], table[role='grid']", timeout=10000)
            except PlaywrightTimeout:
                await asyncio.sleep(3)

            results = []
            seen = set()

            links = self.page.get_by_role("link", name=search_name)
            count = await links.count()
            if count == 0:
                links = self.page.locator("a[data-refid='recordId']")
                count = await links.count()

            for i in range(count):
                link = links.nth(i)
                try:
                    text = await link.text_content()
                    href = await link.get_attribute("href")
                except Exception:
                    continue
                if not text or not href:
                    continue
                if f"/lightning/r/{object_prefix}" not in href:
                    continue
                full = href if href.startswith("http") else f"{self.instance_url}{href}"
                if full in seen:
                    continue
                seen.add(full)
                results.append({"name": text.strip(), "url": full})

            log.info("Found %d result(s)", len(results))
            return results

        return await self._retry(_do)

    async def search_contact(self, name: str) -> list[dict]:
        return await self.search_record(name, "003")

    async def search_and_resolve_contact(self, contact_name: str) -> str | None:
        """Search for a contact by name and return its URL, or None if not found."""
        matches = await self.search_contact(contact_name)
        if not matches:
            log.warning("Contact '%s' not found in Salesforce", contact_name)
            return None
        if len(matches) > 1:
            log.warning("Multiple matches for '%s', using first: %s", contact_name, matches[0]["name"])
        return matches[0]["url"]

    async def search_account(self, name: str) -> list[dict]:
        return await self.search_record(name, "001")

    async def search_opportunity(self, name: str) -> list[dict]:
        return await self.search_record(name, "006")

    async def search_and_resolve_opportunity(self, opp_name: str) -> str | None:
        """Search for an opportunity by name and return its URL, or None."""
        matches = await self.search_opportunity(opp_name)
        if not matches:
            log.warning("Opportunity '%s' not found in Salesforce", opp_name)
            return None
        if len(matches) > 1:
            log.warning("Multiple matches for '%s', using first: %s", opp_name, matches[0]["name"])
        return matches[0]["url"]

    async def check_session_health(self) -> dict:
        """Check if session is healthy. Returns status dict for monitoring.

        Returns: {"healthy": bool, "reason": str, "url": str}
        """
        try:
            current_url = self.page.url
            # If on Lightning, quick check
            if "lightning" in current_url and "login" not in current_url.lower():
                try:
                    await self.page.wait_for_selector(
                        "button:has-text('Search'), nav[aria-label='Main']", timeout=5000
                    )
                    return {"healthy": True, "reason": "on_lightning", "url": current_url}
                except PlaywrightTimeout:
                    pass

            # Navigate to home to check
            await self.page.goto(f"{self.instance_url}/lightning/page/home", wait_until="domcontentloaded")
            await asyncio.sleep(3)

            if self._is_ms_sso_page():
                return {"healthy": False, "reason": "session_expired_ms_sso", "url": self.page.url}
            if await self._is_on_login_page():
                return {"healthy": False, "reason": "session_expired_sf_login", "url": self.page.url}

            try:
                await self.page.wait_for_selector(
                    "button:has-text('Search'), nav[aria-label='Main']", timeout=15000
                )
                return {"healthy": True, "reason": "lightning_loaded", "url": self.page.url}
            except PlaywrightTimeout:
                return {"healthy": False, "reason": "lightning_timeout", "url": self.page.url}

        except Exception as e:
            return {"healthy": False, "reason": f"error: {e}", "url": ""}

    # ──────────────────────────────────────────────
    # LOG A CALL (proven working)
    # ──────────────────────────────────────────────

    async def log_call(self, contact_url: str, entry_data: dict) -> bool:
        """Log a Call on a Contact or Opportunity record.

        entry_data keys: subject, description, activity_type (optional picklist value)
        contact_url can be a Contact URL or an Opportunity URL.
        """
        log.info("Logging call: %s", entry_data.get("subject", "")[:60])

        async def _do():
            await _human_delay(0.5, 1.5)
            await self.page.goto(contact_url, wait_until="domcontentloaded")
            await asyncio.sleep(3)
            await self._close_any_dialog()

            # Look for "Log a Call" button -- may be in activity tab or action bar
            log_btn = None
            for strategy, fn in [
                ("exact button", lambda: self.page.get_by_role("button", name="Log a Call", exact=True)),
                ("action menu", lambda: self.page.locator("a[title='Log a Call'], button[title='Log a Call']").first),
                ("activity tab link", lambda: self.page.locator("a:has-text('Log a Call')").first),
            ]:
                try:
                    el = fn()
                    if await el.count() > 0 and await el.first.is_visible():
                        log_btn = el.first if hasattr(el, 'first') else el
                        log.info("Found 'Log a Call' via: %s", strategy)
                        break
                except Exception:
                    continue

            if not log_btn:
                # Try scrolling down to find it in the activity section
                await self.page.evaluate("window.scrollBy(0, 500)")
                await asyncio.sleep(1)
                log_btn = self.page.get_by_role("button", name="Log a Call", exact=True)

            await log_btn.wait_for(state="visible", timeout=20000)
            await _human_delay(0.3, 0.8)
            await log_btn.click()

            dialog = await self._find_visible_dialog(["Log a Call", "Log Activity", "New Task"])
            if not dialog:
                dialog = self.page.get_by_role("dialog", name="Log a Call")
                await dialog.wait_for(state="visible", timeout=15000)
            await asyncio.sleep(2)

            # Subject
            subject_el = await self._find_input(dialog, "Subject")
            if subject_el:
                await subject_el.click()
                await subject_el.fill("")
                await asyncio.sleep(0.2)
                await subject_el.fill(entry_data["subject"])
                await subject_el.press("Tab")
                await _human_delay(0.3, 0.8)
                log.info("Subject filled")

            # Type picklist (Jeff's BSci has ~50 activity types)
            activity_type = entry_data.get("activity_type")
            if activity_type:
                await _human_delay(0.3, 0.6)
                await self._select_picklist(dialog, "Type", activity_type)

            # Comments / Description
            comments = await self._find_textarea(dialog)
            if comments:
                await _human_delay(0.3, 0.8)
                await comments.click()
                await asyncio.sleep(0.5)
                await comments.fill(entry_data["description"])
                await asyncio.sleep(0.5)
                val = ""
                try:
                    val = await comments.input_value()
                except Exception:
                    pass
                if len(val) < 10:
                    log.warning("Comments .fill() failed, using .type()")
                    await comments.click()
                    await comments.press("Control+a")
                    await comments.press("Backspace")
                    await comments.type(entry_data["description"][:3000], delay=2)
                log.info("Comments filled")

            await _human_delay(0.5, 1.0)
            return await self._click_save_and_wait(dialog)

        try:
            return await self._retry(_do)
        except Exception as e:
            log.error("log_call failed: %s", e)
            await self._screenshot("log_call_failed")
            return False

    # ──────────────────────────────────────────────
    # CREATE CONTACT
    # ──────────────────────────────────────────────

    async def create_contact(self, data: dict) -> bool:
        """Create a new Contact.
        data keys: first_name, last_name, account_name, title, phone, email, description
        """
        log.info("Creating contact: %s %s", data.get("first_name", ""), data.get("last_name", ""))

        async def _do():
            await self.page.goto(f"{self.instance_url}/lightning/o/Contact/new", wait_until="domcontentloaded")
            await asyncio.sleep(3)

            # Dismiss any guided tour popup
            dismiss = self.page.get_by_role("button", name="Dismiss")
            try:
                if await dismiss.count() > 0 and await dismiss.is_visible():
                    await dismiss.click()
                    await asyncio.sleep(0.5)
            except Exception:
                pass

            modal = self.page.get_by_role("dialog", name="New Contact")
            try:
                await modal.wait_for(state="visible", timeout=15000)
            except PlaywrightTimeout:
                # Fallback to generic visible dialog
                modal = self.page.locator("div.modal-container:visible, section[role='dialog']:visible").first
                await modal.wait_for(state="visible", timeout=5000)
            await asyncio.sleep(1)

            # Salutation
            if data.get("salutation"):
                await self._select_picklist(modal, "Salutation", data["salutation"])

            # Name fields
            await self._fill_field(modal, "First Name", data.get("first_name", ""))
            await self._fill_field(modal, "Last Name", data.get("last_name", ""))

            # Account -- lookup/combobox field
            if data.get("account_name"):
                await self._fill_lookup(modal, "Account Name", data["account_name"])

            await self._fill_field(modal, "Title", data.get("title", ""))
            await self._fill_field(modal, "Phone", data.get("phone", ""))
            await self._fill_field(modal, "Email", data.get("email", ""))
            await self._fill_field(modal, "Description", data.get("description", ""))

            return await self._click_save_and_wait(modal)

        try:
            return await self._retry(_do)
        except Exception as e:
            log.error("create_contact failed: %s", e)
            await self._screenshot("create_contact_failed")
            return False

    # ──────────────────────────────────────────────
    # CREATE ACCOUNT
    # ──────────────────────────────────────────────

    async def create_account(self, data: dict) -> bool:
        """Create a new Account.
        data keys: name, phone, website, description
        """
        log.info("Creating account: %s", data.get("name", ""))

        async def _do():
            await self.page.goto(f"{self.instance_url}/lightning/o/Account/new", wait_until="domcontentloaded")
            await asyncio.sleep(3)

            modal = await self._find_visible_dialog(["New Account", "New: Account", "Account"])
            if not modal:
                await self._screenshot("create_account_no_modal")
                raise PlaywrightTimeout("Account modal not found")
            await asyncio.sleep(1)

            await self._fill_field(modal, "Account Name", data.get("name", ""))
            await self._fill_field(modal, "Phone", data.get("phone", ""))
            await self._fill_field(modal, "Website", data.get("website", ""))
            await self._fill_field(modal, "Description", data.get("description", ""))

            return await self._click_save_and_wait(modal)

        try:
            return await self._retry(_do)
        except Exception as e:
            log.error("create_account failed: %s", e)
            await self._screenshot("create_account_failed")
            return False

    # ──────────────────────────────────────────────
    # CREATE TASK (follow-up / reminder)
    # ──────────────────────────────────────────────

    async def create_task(self, data: dict) -> bool:
        """Create a Task.
        data keys: subject, due_date (YYYY-MM-DD), description, contact_name, priority (Normal/High/Low)
        """
        log.info("Creating task: %s", data.get("subject", ""))

        async def _do():
            await self.page.goto(f"{self.instance_url}/lightning/o/Task/new", wait_until="domcontentloaded")
            await asyncio.sleep(3)

            modal = await self._find_visible_dialog(["New Task", "New: Task", "Task"])
            if not modal:
                await self._screenshot("create_task_no_modal")
                raise PlaywrightTimeout("Task modal not found")
            await asyncio.sleep(1)

            # Subject is a combobox in Task forms
            await self._fill_combobox_text(modal, "Subject", data.get("subject", ""))
            await self._fill_field(modal, "Due Date", _to_sf_date(data.get("due_date", "")))
            await self._fill_field(modal, "Description", data.get("description", ""))

            if data.get("priority"):
                await self._select_picklist(modal, "Priority", data["priority"])

            # Contact name lookup
            if data.get("contact_name"):
                await self._fill_lookup(modal, "Name", data["contact_name"])

            return await self._click_save_and_wait(modal)

        try:
            return await self._retry(_do)
        except Exception as e:
            log.error("create_task failed: %s", e)
            await self._screenshot("create_task_failed")
            return False

    # ──────────────────────────────────────────────
    # CREATE EVENT (meeting / calendar)
    # ──────────────────────────────────────────────

    async def create_event(self, data: dict) -> bool:
        """Create an Event / calendar entry.
        data keys: subject, start_date, start_time, end_date, end_time, description, contact_name
        """
        log.info("Creating event: %s", data.get("subject", ""))

        async def _do():
            await self.page.goto(f"{self.instance_url}/lightning/o/Event/new", wait_until="domcontentloaded")
            await asyncio.sleep(3)

            modal = await self._find_visible_dialog(["New Event", "New: Event", "Event"])
            if not modal:
                await self._screenshot("create_event_no_modal")
                raise PlaywrightTimeout("Event modal not found")
            await asyncio.sleep(1)

            await self._fill_combobox_text(modal, "Subject", data.get("subject", ""))
            await self._fill_field(modal, "Start Date", _to_sf_date(data.get("start_date", "")))
            await self._fill_field(modal, "End Date", _to_sf_date(data.get("end_date", "")))
            await self._fill_field(modal, "Description", data.get("description", ""))

            if data.get("contact_name"):
                await self._fill_lookup(modal, "Name", data["contact_name"])

            return await self._click_save_and_wait(modal)

        try:
            return await self._retry(_do)
        except Exception as e:
            log.error("create_event failed: %s", e)
            await self._screenshot("create_event_failed")
            return False

    # ──────────────────────────────────────────────
    # CREATE OPPORTUNITY
    # ──────────────────────────────────────────────

    async def create_opportunity(self, data: dict) -> bool:
        """Create an Opportunity.
        data keys: name, account_name, close_date, stage, amount, description
        """
        log.info("Creating opportunity: %s", data.get("name", ""))

        async def _do():
            await self.page.goto(f"{self.instance_url}/lightning/o/Opportunity/new", wait_until="domcontentloaded")
            await asyncio.sleep(3)

            modal = await self._find_visible_dialog(["New Opportunity", "New: Opportunity", "Opportunity"])
            if not modal:
                await self._screenshot("create_opp_no_modal")
                raise PlaywrightTimeout("Opportunity modal not found")
            await asyncio.sleep(1)

            await self._fill_field(modal, "Opportunity Name", data.get("name", ""))
            await self._fill_field(modal, "Close Date", _to_sf_date(data.get("close_date", "")))
            await self._fill_field(modal, "Amount", data.get("amount", ""))
            await self._fill_field(modal, "Description", data.get("description", ""))

            if data.get("stage"):
                await self._select_picklist(modal, "Stage", data["stage"])

            # Account Name on Opportunity opens Advanced Search -- handle specially
            if data.get("account_name"):
                await self._fill_lookup_with_adv_search(modal, "Account Name", data["account_name"])

            return await self._click_save_and_wait(modal)

        try:
            return await self._retry(_do)
        except Exception as e:
            log.error("create_opportunity failed: %s", e)
            await self._screenshot("create_opportunity_failed")
            return False

    # ──────────────────────────────────────────────
    # ADD NOTE to a record
    # ──────────────────────────────────────────────

    async def add_note(self, record_url: str, title: str, body: str) -> bool:
        """Add a Note to a record (contact, account, etc.)."""
        log.info("Adding note '%s' to %s", title[:40], record_url[-20:])

        async def _do():
            await self.page.goto(record_url, wait_until="domcontentloaded")
            await asyncio.sleep(3)

            # Look for Notes related list or "New Note" button
            new_note = self.page.get_by_role("button", name="New Note")
            if await new_note.count() == 0:
                # Try the related list
                new_note = self.page.locator("a:has-text('New Note')").first
            if await new_note.count() == 0:
                log.warning("No 'New Note' button found -- notes may not be enabled")
                return False

            await new_note.click()
            await asyncio.sleep(2)

            # Note editor
            title_input = self.page.locator("input[placeholder*='title'], input[placeholder*='Title']").first
            if await title_input.count() > 0:
                await title_input.fill(title)

            body_area = self.page.locator("div[contenteditable='true'], textarea").first
            if await body_area.count() > 0:
                await body_area.click()
                await body_area.type(body, delay=2)

            # Save
            done_btn = self.page.get_by_role("button", name="Done")
            if await done_btn.count() > 0:
                await done_btn.click()
            else:
                save_btn = self.page.get_by_role("button", name="Save")
                if await save_btn.count() > 0:
                    await save_btn.last.click()

            await asyncio.sleep(2)
            log.info("Note added")
            return True

        try:
            return await self._retry(_do)
        except Exception as e:
            log.error("add_note failed: %s", e)
            await self._screenshot("add_note_failed")
            return False

    # ──────────────────────────────────────────────
    # SCRAPE ORG LAYOUT
    # ──────────────────────────────────────────────

    async def _ensure_lightning_page(self) -> bool:
        """After a goto, verify we're on Lightning (not redirected to login).
        Returns True if on Lightning, False if stuck on login."""
        await asyncio.sleep(2)
        url = self.page.url.lower()
        if self._is_on_login_page():
            log.warning("Redirected to login page during scrape, session may not be ready")
            # Try navigating to home first to establish session
            await self.page.goto(f"{self.instance_url}/lightning/page/home", wait_until="domcontentloaded")
            await asyncio.sleep(5)
            if self._is_on_login_page():
                return False
        return True

    async def scrape_org_layout(self) -> dict:
        log.info("Scraping org layout...")
        layout = {"log_a_call": {}, "contacts": {}, "accounts": {}}

        # First, ensure we're actually on a Lightning page
        if not await self._ensure_lightning_page():
            log.error("Cannot scrape: not logged in to Salesforce")
            return layout

        # Log a Call
        try:
            await self.page.goto(f"{self.instance_url}/lightning/o/Contact/list", wait_until="domcontentloaded")
            await asyncio.sleep(3)
            if self._is_on_login_page():
                log.warning("Redirected to login during contact list scrape, skipping log_a_call")
                raise Exception("Login redirect")
            first = self.page.locator("a[data-refid='recordId']").first
            if await first.count() > 0:
                await first.click()
                await asyncio.sleep(3)
                btn = self.page.get_by_role("button", name="Log a Call", exact=True)
                if await btn.count() > 0:
                    await btn.click()
                    dialog = self.page.get_by_role("dialog", name="Log a Call")
                    await dialog.wait_for(state="visible", timeout=10000)
                    await asyncio.sleep(2)

                    fields = []
                    labels = dialog.locator("label")
                    for i in range(await labels.count()):
                        try:
                            t = await labels.nth(i).text_content()
                            if t and t.strip():
                                fields.append(t.strip())
                        except Exception:
                            continue

                    subject_vals = []
                    try:
                        combo = dialog.get_by_role("combobox", name="Subject")
                        if await combo.count() > 0:
                            await combo.click()
                            await asyncio.sleep(0.5)
                            opts = dialog.get_by_role("option")
                            for i in range(await opts.count()):
                                v = await opts.nth(i).text_content()
                                if v and v.strip():
                                    subject_vals.append(v.strip())
                            await combo.press("Escape")
                    except Exception:
                        pass

                    layout["log_a_call"] = {"fields": fields, "subject_picklist": subject_vals}
                    await self._close_any_dialog()
        except Exception as e:
            log.warning("Scrape log_a_call failed: %s", e)

        # Contact fields
        try:
            await self.page.goto(f"{self.instance_url}/lightning/o/Contact/new", wait_until="domcontentloaded")
            await asyncio.sleep(3)
            if self._is_on_login_page():
                log.warning("Redirected to login during contact new scrape, skipping contacts")
                raise Exception("Login redirect")
            labels = self.page.locator("label:visible")
            fields = []
            for i in range(await labels.count()):
                t = await labels.nth(i).text_content()
                if t and t.strip():
                    fields.append(t.strip())
            layout["contacts"] = {"fields": fields}
            cancel = self.page.get_by_role("button", name="Cancel")
            if await cancel.count() > 0:
                await cancel.first.click()
        except Exception as e:
            log.warning("Scrape contacts failed: %s", e)

        log.info("Scrape complete")
        return layout

    # ──────────────────────────────────────────────
    # Internal helpers for finding form elements
    # ──────────────────────────────────────────────

    async def _find_input(self, container, label: str):
        for strategy_name, fn in [
            ("combobox", lambda: container.get_by_role("combobox", name=label)),
            ("get_by_label", lambda: container.get_by_label(label, exact=False)),
            ("input visible", lambda: container.locator("input:visible").first),
        ]:
            try:
                el = fn()
                if await el.count() > 0 and await el.is_visible():
                    return el
            except Exception:
                continue
        return None

    async def _find_textarea(self, container):
        for fn in [
            lambda: container.locator("textarea").first,
            lambda: container.get_by_role("textbox", name="Comments"),
            lambda: self.page.locator("textarea:visible").first,
        ]:
            try:
                el = fn()
                if await el.count() > 0 and await el.is_visible():
                    return el
            except Exception:
                continue
        return None
