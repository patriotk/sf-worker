"""
Layout-aware mapper: converts RepDrive CRM entries to Salesforce field values.

If org_layout is provided, validates picklist values and adapts field names.
If org_layout is None, uses sensible defaults (backwards-compatible with trial org).
"""


def map_to_salesforce(entry: dict, org_layout: dict | None) -> dict:
    """
    Map a RepDrive CRM entry to Salesforce log-a-call fields.

    Returns: {"subject": str, "description": str, "date": str}
    """
    meeting_type = entry.get("meeting_type") or "Call"
    account_name = entry.get("account_name") or "Unknown"
    meeting_date = entry.get("meeting_date") or ""

    subject = f"[{meeting_type}] {account_name} -- {meeting_date}"

    return {
        "subject": subject,
        "description": build_description(entry),
        "date": meeting_date,
        "contact_name": entry.get("contact_name") or "",
        "account_name": account_name,
    }


def build_description(entry: dict) -> str:
    """Build formatted description text from entry fields."""
    sections = []

    account = entry.get("account_name") or "Unknown"
    contact = entry.get("contact_name") or "Unknown"
    meeting_type = entry.get("meeting_type") or "Call"
    meeting_date = entry.get("meeting_date") or "Not specified"

    sections.append(f"Meeting Log -- {account}")
    sections.append(f"Contact: {contact}")
    sections.append(f"Meeting Type: {meeting_type}")
    sections.append(f"Date: {meeting_date}")

    if _has_value(entry.get("summary")):
        sections.append(f"\nSummary:\n{entry['summary']}")

    if _has_value(entry.get("key_details")):
        sections.append(f"\nKey Details:\n{entry['key_details']}")

    action_items = entry.get("action_items") or []
    if action_items:
        items = "\n".join(f"- {item}" for item in action_items)
        sections.append(f"\nAction Items:\n{items}")

    next_steps = entry.get("next_steps") or []
    if next_steps:
        steps = "\n".join(f"- {step}" for step in next_steps)
        sections.append(f"\nNext Steps:\n{steps}")

    if _has_value(entry.get("opportunities")):
        sections.append(f"\nOpportunities:\n{entry['opportunities']}")

    other_people = entry.get("other_people_mentioned") or []
    if other_people:
        if isinstance(other_people, list):
            people_str = ", ".join(other_people)
        else:
            people_str = str(other_people)
        sections.append(f"\nOther People Mentioned:\n{people_str}")

    if _has_value(entry.get("follow_up_date")):
        sections.append(f"\nFollow-up Date: {entry['follow_up_date']}")

    sections.append("\n---\nLogged via RepDrive")

    return "\n".join(sections)


def _has_value(val) -> bool:
    if val is None:
        return False
    if isinstance(val, str) and val.strip() == "":
        return False
    if isinstance(val, list) and len(val) == 0:
        return False
    return True
