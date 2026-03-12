"""
Manual integration test: run against the trial Salesforce org.
Usage: python test_integration.py

Requires .env with trial org credentials:
  SF_TEST_INSTANCE_URL=https://java-power-8395.lightning.force.com
  SF_TEST_USERNAME=...
  SF_TEST_PASSWORD=...
  SF_TEST_CONTACT=Chen Walker
"""
import asyncio
import logging
import os
from dotenv import load_dotenv

load_dotenv()

# Override config for local testing
os.environ.setdefault("PROFILES_DIR", "profiles")
os.environ.setdefault("ERRORS_DIR", "errors")

from browser import SalesforceBot
from mapper import map_to_salesforce

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("integration_test")


async def test_full_flow():
    instance_url = os.environ["SF_TEST_INSTANCE_URL"]
    username = os.environ["SF_TEST_USERNAME"]
    password = os.environ["SF_TEST_PASSWORD"]
    test_contact = os.environ.get("SF_TEST_CONTACT", "Chen Walker")

    log.info("=== Integration Test: Full Flow ===")
    log.info("Instance: %s", instance_url)
    log.info("User: %s", username)
    log.info("Contact: %s", test_contact)

    bot = SalesforceBot(instance_url, username=username, headless=False)
    await bot.start()

    try:
        # 1. Login
        log.info("--- Step 1: Login ---")
        if not await bot.ensure_logged_in():
            ok = await bot.login(username, password)
            assert ok, "Login failed"
        log.info("Logged in")

        # 2. Search contact
        log.info("--- Step 2: Search Contact ---")
        contact_url = await bot.search_and_resolve_contact(test_contact)
        assert contact_url, f"Contact '{test_contact}' not found"
        log.info("Found: %s", contact_url)

        # 3. Map entry
        log.info("--- Step 3: Map Entry ---")
        test_entry = {
            "meeting_type": "Sales Call",
            "account_name": "General Hospital",
            "contact_name": test_contact,
            "meeting_date": "2026-03-12",
            "summary": "Integration test from SF worker. Testing direct push flow.",
            "key_details": "Verified: login, search, map, push all working.",
            "action_items": ["Verify data in Salesforce"],
            "next_steps": ["Deploy to production"],
            "opportunities": "Direct push working",
            "other_people_mentioned": ["Test Bot"],
        }
        mapped = map_to_salesforce(test_entry, None)
        log.info("Subject: %s", mapped["subject"])

        # 4. Push to Salesforce
        log.info("--- Step 4: Log Call ---")
        success = await bot.log_call(contact_url, mapped)
        assert success, "Log call failed"
        log.info("Call logged successfully!")

        # 5. Test org scrape
        log.info("--- Step 5: Scrape Org Layout ---")
        layout = await bot.scrape_org_layout()
        log.info("Layout keys: %s", list(layout.keys()))
        for obj_type, obj_data in layout.items():
            fields = obj_data.get("fields", [])
            log.info("  %s: %d fields", obj_type, len(fields))

        log.info("=== ALL TESTS PASSED ===")

    finally:
        await bot.close()


if __name__ == "__main__":
    asyncio.run(test_full_flow())
