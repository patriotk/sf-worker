"""
Seed demo accounts and contacts in Salesforce via browser automation.
Run inside the worker container: python seed_sf_demo.py
"""
import asyncio
import logging
from browser import SalesforceBot

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Demo accounts
ACCOUNTS = [
    {"name": "Methodist Hospital", "phone": "713-555-0100", "website": "methodisthealth.com",
     "description": "Large teaching hospital in Houston. Aquablation champion site."},
    {"name": "Cedars-Sinai Medical Center", "phone": "310-555-0200", "website": "cedars-sinai.org",
     "description": "Evaluation stage for Aquablation. High-volume urology program."},
    {"name": "Cleveland Clinic", "phone": "216-555-0300", "website": "clevelandclinic.org",
     "description": "Competitive displacement opportunity. Currently using Rezum."},
    {"name": "Mayo Clinic Jacksonville", "phone": "904-555-0400", "website": "mayoclinic.org",
     "description": "New territory. No prior BSci urology contact."},
    {"name": "Northside Hospital", "phone": "404-555-0500", "website": "northside.com",
     "description": "Existing customer in Atlanta. Renewal coming April 2026."},
]

# Demo contacts
CONTACTS = [
    {"salutation": "Dr.", "first_name": "James", "last_name": "Chen",
     "account_name": "Methodist Hospital", "title": "Chief of Urology",
     "phone": "713-555-0101", "email": "jchen@methodist.example.com",
     "description": "Key champion. Led Aquablation adoption. Publishes outcomes data."},
    {"salutation": "Dr.", "first_name": "Sarah", "last_name": "Mitchell",
     "account_name": "Methodist Hospital", "title": "Attending Urologist",
     "phone": "713-555-0102", "email": "smitchell@methodist.example.com",
     "description": "Clinical trial lead. Trained 5 other urologists on Aquablation."},
    {"first_name": "Tom", "last_name": "Rodriguez",
     "account_name": "Cedars-Sinai Medical Center", "title": "VP Surgical Services",
     "phone": "310-555-0201", "email": "trodriguez@cedars.example.com",
     "description": "Economic buyer. Focused on OR efficiency and reimbursement."},
    {"salutation": "Dr.", "first_name": "Michael", "last_name": "Park",
     "account_name": "Cleveland Clinic", "title": "BPH Program Director",
     "phone": "216-555-0301", "email": "mpark@ccf.example.com",
     "description": "Currently using Rezum. Open to evaluating Aquablation."},
    {"first_name": "Linda", "last_name": "Chen",
     "account_name": "Mayo Clinic Jacksonville", "title": "OR Manager",
     "phone": "904-555-0401", "email": "lchen@mayo.example.com",
     "description": "Logistics contact. Manages surgical scheduling."},
    {"salutation": "Dr.", "first_name": "David", "last_name": "Okonkwo",
     "account_name": "Northside Hospital", "title": "Department Chair, Urology",
     "phone": "404-555-0501", "email": "dokonkwo@northside.example.com",
     "description": "Renewal decision maker. Happy with outcomes, budget review pending."},
    {"first_name": "Jeff", "last_name": "LeFevre",
     "account_name": "Methodist Hospital", "title": "BSci Territory Manager",
     "phone": "832-555-0199", "email": "jlefevre@bsci.example.com",
     "description": "Our rep. Covers Houston/Gulf Coast urology territory."},
]


async def main():
    bot = SalesforceBot(
        instance_url="https://java-power-8395.lightning.force.com",
        username="patknick0-hfyz@force.com",
        headless=True,
    )
    await bot.start()

    try:
        # Login
        if not await bot.ensure_logged_in():
            from supabase_client import get_user_sf_profile, get_sf_credentials
            profile = await get_user_sf_profile("3463bf22-aff8-4b8c-a7a5-dcaea61ee56d")
            _, pw = get_sf_credentials(profile)
            if not await bot.login("patknick0-hfyz@force.com", pw):
                print("LOGIN FAILED")
                return
            del pw

        print(f"\n=== Creating {len(ACCOUNTS)} accounts ===")
        for acct in ACCOUNTS:
            ok = await bot.create_account(acct)
            status = "OK" if ok else "FAILED"
            print(f"  [{status}] {acct['name']}")

        print(f"\n=== Creating {len(CONTACTS)} contacts ===")
        for contact in CONTACTS:
            ok = await bot.create_contact(contact)
            status = "OK" if ok else "FAILED"
            print(f"  [{status}] {contact.get('first_name', '')} {contact['last_name']}")

        print("\nDone!")

    finally:
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
