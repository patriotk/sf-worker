"""
All Supabase operations for the SF worker.
Uses SUPABASE_SERVICE_KEY for admin access (bypasses RLS).
"""
import logging
from datetime import datetime, timezone, timedelta
from supabase import create_client

import config
from crypto import decrypt_dict, decrypt_sf_credential, CRM_ENTRY_FIELDS

log = logging.getLogger("supabase_client")

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)
    return _client


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Payload builders (tested independently) ---

def _build_claim_payload() -> dict:
    return {"processing_started_at": _utcnow()}


def _build_sent_payload() -> dict:
    return {
        "status": "sent",
        "sf_pushed_at": _utcnow(),
        "send_method": "direct_push",
        "error_message": None,
        "processing_started_at": None,
    }


def _build_failed_payload(error: str, retry_count: int) -> dict:
    return {
        "status": "send_failed",
        "error_message": error,
        "retry_count": retry_count,
        "processing_started_at": None,
    }


def _build_retry_payload(error: str, retry_count: int) -> dict:
    return {
        "status": "sending",
        "error_message": error,
        "retry_count": retry_count,
        "processing_started_at": None,
    }


# --- Entry operations ---

async def get_next_sending_entry() -> dict | None:
    """Fetch the oldest entry with status='sending' and retry_count < MAX_RETRIES."""
    try:
        result = (
            _get_client()
            .table("crm_entries")
            .select("*")
            .eq("status", "sending")
            .lt("retry_count", config.MAX_RETRIES)
            .order("created_at")
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0]
        return None
    except Exception as e:
        log.error("Failed to poll entries: %s", e)
        return None


async def claim_entry(entry_id: str):
    """Mark entry as being processed (set processing_started_at)."""
    _get_client().table("crm_entries").update(
        _build_claim_payload()
    ).eq("id", entry_id).execute()


async def mark_sent(entry_id: str):
    """Mark entry as successfully pushed to Salesforce."""
    _get_client().table("crm_entries").update(
        _build_sent_payload()
    ).eq("id", entry_id).execute()


async def mark_failed(entry_id: str, error: str, retry_count: int):
    """Mark entry as permanently failed (retry_count >= MAX_RETRIES)."""
    _get_client().table("crm_entries").update(
        _build_failed_payload(error, retry_count)
    ).eq("id", entry_id).execute()


async def mark_retry(entry_id: str, error: str, retry_count: int):
    """Mark entry for retry (increment retry_count, reset to 'sending')."""
    _get_client().table("crm_entries").update(
        _build_retry_payload(error, retry_count)
    ).eq("id", entry_id).execute()


def decrypt_entry(entry: dict) -> dict:
    """Decrypt CRM entry fields in-place."""
    return decrypt_dict(entry, CRM_ENTRY_FIELDS)


# --- User SF profile operations ---

async def get_user_sf_profile(user_id: str) -> dict | None:
    """Fetch user's Salesforce connection profile."""
    result = (
        _get_client()
        .table("user_sf_profiles")
        .select("*")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]
    return None


def get_sf_credentials(profile: dict) -> tuple[str, str]:
    """Decrypt SF username and password from profile. Returns (username, password)."""
    username = decrypt_sf_credential(profile["sf_username"])
    password = decrypt_sf_credential(profile["sf_password"])
    return username, password


async def update_profile_session(profile_id: str, valid: bool, needs_mfa: bool = False):
    """Update session_valid and needs_mfa flags."""
    _get_client().table("user_sf_profiles").update({
        "session_valid": valid,
        "needs_mfa": needs_mfa,
        "last_used_at": _utcnow(),
    }).eq("id", profile_id).execute()


async def save_org_layout(profile_id: str, layout: dict):
    """Save scraped org layout."""
    _get_client().table("user_sf_profiles").update({
        "org_layout": layout,
    }).eq("id", profile_id).execute()


async def get_profiles_needing_setup() -> list[dict]:
    """Find profiles where org_layout is null (need initial scrape)."""
    result = (
        _get_client()
        .table("user_sf_profiles")
        .select("*")
        .is_("org_layout", "null")
        .eq("needs_mfa", False)
        .execute()
    )
    return result.data or []


# --- Watchdog ---

async def reset_stuck_entries():
    """Reset entries stuck in processing for too long."""
    threshold = datetime.now(timezone.utc) - timedelta(seconds=config.STUCK_THRESHOLD)
    threshold_str = threshold.isoformat()

    result = (
        _get_client()
        .table("crm_entries")
        .select("id, retry_count")
        .eq("status", "sending")
        .not_.is_("processing_started_at", "null")
        .lt("processing_started_at", threshold_str)
        .execute()
    )

    for entry in (result.data or []):
        new_retry = (entry.get("retry_count") or 0) + 1
        if new_retry >= config.MAX_RETRIES:
            await mark_failed(entry["id"], "Worker timeout (stuck in processing)", new_retry)
            log.warning("Entry %s permanently failed after %d retries", entry["id"], new_retry)
        else:
            await mark_retry(entry["id"], "Worker timeout, will retry", new_retry)
            log.warning("Entry %s reset for retry (attempt %d)", entry["id"], new_retry)
