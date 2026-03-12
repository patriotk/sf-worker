#!/usr/bin/env python3
"""One-time MFA login helper. Run with DISPLAY=:99 for VNC visibility."""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from browser import SalesforceBot

async def main():
    user_id = sys.argv[1] if len(sys.argv) > 1 else "test_user"
    print(f"Launching browser for user: {user_id}")
    print("Watch the VNC window at http://178.156.226.182:6080/vnc.html")
    print("Complete MFA in the browser, then press Enter here when done.")

    bot = SalesforceBot(user_id, headless=False)
    await bot.launch()
    await bot.ensure_logged_in()

    input("\nMFA done? Press Enter to close browser...")
    await bot.close()
    print("Browser closed. Profile saved.")

asyncio.run(main())
