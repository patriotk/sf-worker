"""
SF Worker: polls Supabase for CRM entries and pushes them to Salesforce.

Entry point: python worker.py
"""
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime

import config
from supabase_client import (
    get_next_sending_entry, claim_entry, mark_sent, mark_failed,
    mark_retry, decrypt_entry, get_user_sf_profile, get_sf_credentials,
    update_profile_session, reset_stuck_entries, get_profiles_needing_setup,
    save_org_layout, get_mfa_code, clear_mfa_code,
)
from browser import SalesforceBot
from mapper import map_to_salesforce

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("worker")

# Bot pool: user_id -> {"bot": SalesforceBot, "last_used": float}
_bot_pool: dict[str, dict] = {}
_active_count = 0
_shutdown = False


async def get_or_create_bot(profile: dict) -> SalesforceBot:
    """Get existing bot for user or create a new one."""
    user_id = profile["user_id"]

    if user_id in _bot_pool:
        _bot_pool[user_id]["last_used"] = asyncio.get_event_loop().time()
        return _bot_pool[user_id]["bot"]

    # Wait for a slot if pool is full
    while len(_bot_pool) >= config.MAX_CONCURRENT_BROWSERS:
        await _evict_idle_bot()
        if len(_bot_pool) >= config.MAX_CONCURRENT_BROWSERS:
            await asyncio.sleep(1)

    sf_username, _ = get_sf_credentials(profile)
    bot = SalesforceBot(
        instance_url=profile["sf_instance_url"],
        username=sf_username,
        headless=True,
    )
    await bot.start()

    _bot_pool[user_id] = {
        "bot": bot,
        "last_used": asyncio.get_event_loop().time(),
    }
    return bot


async def _evict_idle_bot():
    """Close the oldest idle bot to free a slot."""
    if not _bot_pool:
        return
    oldest_id = min(_bot_pool, key=lambda uid: _bot_pool[uid]["last_used"])
    bot_entry = _bot_pool.pop(oldest_id)
    try:
        await bot_entry["bot"].close()
    except Exception:
        pass
    log.info("Evicted idle bot for user %s", oldest_id)


async def cleanup_idle_bots():
    """Close bots idle longer than BOT_IDLE_TIMEOUT."""
    now = asyncio.get_event_loop().time()
    to_evict = [
        uid for uid, entry in _bot_pool.items()
        if now - entry["last_used"] > config.BOT_IDLE_TIMEOUT
    ]
    for uid in to_evict:
        bot_entry = _bot_pool.pop(uid)
        try:
            await bot_entry["bot"].close()
        except Exception:
            pass
        log.info("Closed idle bot for user %s", uid)


async def process_entry(entry: dict):
    """Process a single CRM entry: decrypt, resolve contact, push to SF."""
    global _active_count
    _active_count += 1
    entry_id = entry["id"]
    user_id = entry["user_id"]
    retry_count = entry.get("retry_count") or 0

    try:
        profile = await get_user_sf_profile(user_id)
        if not profile:
            await mark_failed(entry_id, "No Salesforce connection configured", retry_count + 1)
            log.error("Entry %s: user %s has no SF profile", entry_id, user_id)
            return

        if not profile.get("session_valid"):
            await mark_failed(entry_id, "Salesforce session expired. Please re-authenticate in Settings.", retry_count + 1)
            return

        # Decrypt entry
        decrypted = decrypt_entry(entry)
        contact_name = decrypted.get("contact_name")
        if not contact_name:
            await mark_failed(entry_id, "No contact name in entry", retry_count + 1)
            return

        # Get or create browser bot
        bot = await get_or_create_bot(profile)

        # Ensure logged in
        if not await bot.ensure_logged_in():
            sf_user, sf_pass = get_sf_credentials(profile)
            async def mfa_callback():
                return await get_mfa_code(profile["id"])
            logged_in = await bot.login(
                sf_user, sf_pass,
                mfa_code_callback=mfa_callback,
                verification_email=profile.get("verification_email"),
            )
            del sf_pass
            if not logged_in:
                await update_profile_session(profile["id"], valid=False, needs_mfa=True)
                await mark_failed(entry_id, "Salesforce login failed. MFA may be required.", retry_count + 1)
                return
            await clear_mfa_code(profile["id"])

        # Resolve contact URL
        contact_url = await bot.search_and_resolve_contact(contact_name)
        if not contact_url:
            await mark_failed(entry_id, f"Contact '{contact_name}' not found in Salesforce", retry_count + 1)
            return

        # Map entry to SF fields
        org_layout = profile.get("org_layout")
        mapped = map_to_salesforce(decrypted, org_layout)

        # Push to Salesforce
        success = await bot.log_call(contact_url, mapped)
        if success:
            await mark_sent(entry_id)
            await update_profile_session(profile["id"], valid=True)
            log.info("Entry %s: pushed to Salesforce", entry_id)
        else:
            new_retry = retry_count + 1
            if new_retry >= config.MAX_RETRIES:
                await mark_failed(entry_id, "Salesforce UI save failed", new_retry)
            else:
                await mark_retry(entry_id, "Salesforce UI save failed, will retry", new_retry)

    except Exception as e:
        log.exception("Entry %s: unexpected error", entry_id)
        new_retry = retry_count + 1
        if new_retry >= config.MAX_RETRIES:
            await mark_failed(entry_id, f"Unexpected error: {e}", new_retry)
        else:
            await mark_retry(entry_id, f"Error: {e}", new_retry)
    finally:
        _active_count -= 1


async def poll_loop():
    """Main loop: poll for entries and dispatch processing."""
    log.info("Poll loop started (interval=%ds, max_browsers=%d)",
             config.POLL_INTERVAL, config.MAX_CONCURRENT_BROWSERS)

    while not _shutdown:
        try:
            if _active_count >= config.MAX_CONCURRENT_BROWSERS:
                await asyncio.sleep(1)
                continue

            entry = await get_next_sending_entry()
            if entry:
                claimed = await claim_entry(entry["id"])
                if not claimed:
                    continue  # Another worker/iteration got it first
                log.info("Claimed entry %s (retry %d)", entry["id"], entry.get("retry_count", 0))
                asyncio.create_task(process_entry(entry))

        except Exception as e:
            log.exception("Poll loop error: %s", e)

        await asyncio.sleep(config.POLL_INTERVAL)


async def watchdog_loop():
    """Periodic check for stuck entries."""
    log.info("Watchdog started (interval=%ds, threshold=%ds)",
             config.WATCHDOG_INTERVAL, config.STUCK_THRESHOLD)

    while not _shutdown:
        try:
            await reset_stuck_entries()
        except Exception as e:
            log.exception("Watchdog error: %s", e)
        await asyncio.sleep(config.WATCHDOG_INTERVAL)


async def heartbeat_loop():
    """Write heartbeat timestamp to local file."""
    while not _shutdown:
        try:
            with open(config.HEARTBEAT_FILE, "w") as f:
                f.write(datetime.utcnow().isoformat())
        except Exception:
            pass
        await asyncio.sleep(60)


async def idle_cleanup_loop():
    """Periodically close idle bots."""
    while not _shutdown:
        await cleanup_idle_bots()
        await asyncio.sleep(60)


async def setup_loop():
    """Check for new SF profiles that need initial setup (login + org scrape)."""
    while not _shutdown:
        try:
            profiles = await get_profiles_needing_setup()
            for profile in profiles:
                if _active_count >= config.MAX_CONCURRENT_BROWSERS:
                    break
                log.info("Setting up profile for user %s", profile["user_id"])
                asyncio.create_task(setup_profile(profile))
        except Exception as e:
            log.exception("Setup loop error: %s", e)
        await asyncio.sleep(30)


async def setup_profile(profile: dict):
    """Initial setup: login, handle MFA, scrape org layout."""
    global _active_count
    _active_count += 1
    try:
        bot = await get_or_create_bot(profile)

        sf_user, sf_pass = get_sf_credentials(profile)
        if not await bot.ensure_logged_in():
            async def mfa_callback():
                return await get_mfa_code(profile["id"])

            logged_in = await bot.login(
                sf_user, sf_pass,
                mfa_code_callback=mfa_callback,
                verification_email=profile.get("verification_email"),
            )
            del sf_pass
            if not logged_in:
                await update_profile_session(profile["id"], valid=False, needs_mfa=True)
                # Remove bot from pool so next attempt gets a fresh browser
                user_id = profile["user_id"]
                if user_id in _bot_pool:
                    try:
                        await _bot_pool[user_id]["bot"].close()
                    except Exception:
                        pass
                    del _bot_pool[user_id]
                log.warning("User %s: login failed, may need MFA", profile["user_id"])
                return
        else:
            del sf_pass

        # Login succeeded — clear MFA code if one was used
        await clear_mfa_code(profile["id"])

        layout = await bot.scrape_org_layout()
        await save_org_layout(profile["id"], layout)
        await update_profile_session(profile["id"], valid=True)
        log.info("User %s: org layout scraped (%d object types)", profile["user_id"], len(layout))

    except Exception as e:
        log.exception("Setup failed for user %s: %s", profile["user_id"], e)
    finally:
        _active_count -= 1


async def main():
    global _shutdown
    log.info("=== SF Worker starting ===")
    log.info("Supabase: %s", config.SUPABASE_URL[:40] + "..." if config.SUPABASE_URL else "NOT SET")
    log.info("Profiles: %s", config.PROFILES_DIR)

    os.makedirs(config.PROFILES_DIR, exist_ok=True)
    os.makedirs(config.ERRORS_DIR, exist_ok=True)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: setattr(sys.modules[__name__], '_shutdown', True))
        except NotImplementedError:
            pass  # Windows

    await asyncio.gather(
        poll_loop(),
        watchdog_loop(),
        heartbeat_loop(),
        idle_cleanup_loop(),
        setup_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())
