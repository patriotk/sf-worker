import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mapper import map_to_salesforce, build_description, _resolve_activity_type


SAMPLE_ENTRY = {
    "meeting_type": "Sales Call",
    "account_name": "General Hospital",
    "contact_name": "Chen Walker",
    "meeting_date": "2026-03-12",
    "summary": "Discussed product upgrade.",
    "key_details": "Budget approved for Q2.",
    "action_items": ["Send proposal", "Schedule demo"],
    "next_steps": ["Follow up in 2 weeks"],
    "opportunities": "Expansion opportunity",
    "other_people_mentioned": ["Dr. Smith"],
}

SAMPLE_LAYOUT = {
    "log_a_call": {
        "fields": ["Subject", "Comments", "Due Date Only"],
        "subject_picklist": ["Call", "Email", "Send Letter"],
    },
}


class TestMapToSalesforce:
    def test_basic_mapping(self):
        result = map_to_salesforce(SAMPLE_ENTRY, None)
        assert "subject" in result
        assert "description" in result
        assert "activity_type" in result
        assert "Sales Call" in result["subject"]
        assert "General Hospital" in result["subject"]

    def test_with_layout(self):
        result = map_to_salesforce(SAMPLE_ENTRY, SAMPLE_LAYOUT)
        assert "subject" in result
        assert "description" in result
        assert "activity_type" in result

    def test_empty_entry(self):
        result = map_to_salesforce({
            "meeting_type": "Call",
            "account_name": "",
            "contact_name": "",
            "meeting_date": "",
        }, None)
        assert result["subject"]
        assert result["activity_type"] == "Call"


class TestActivityTypeResolution:
    def test_call_mapping(self):
        assert _resolve_activity_type("Call", None) == "Call"
        assert _resolve_activity_type("phone", None) == "Call"

    def test_email_mapping(self):
        assert _resolve_activity_type("Email", None) == "Email"
        assert _resolve_activity_type("email", None) == "Email"

    def test_meeting_mapping(self):
        assert _resolve_activity_type("Meeting", None) == "Meeting"
        assert _resolve_activity_type("demo", None) == "Meeting"

    def test_lunch_dinner_mapping(self):
        assert _resolve_activity_type("lunch", None) == "Lunch/Dinner Meeting"
        assert _resolve_activity_type("dinner", None) == "Lunch/Dinner Meeting"

    def test_business_review_mapping(self):
        assert _resolve_activity_type("business review", None) == "Business Review"

    def test_default_fallback(self):
        assert _resolve_activity_type("", None) == "Call"
        assert _resolve_activity_type(None, None) == "Call"

    def test_with_org_layout_validation(self):
        layout = {"log_a_call": {"subject_picklist": ["Call", "Email", "Other"]}}
        assert _resolve_activity_type("call", layout) == "Call"
        # "Meeting" not in picklist -> falls back to "Other"
        assert _resolve_activity_type("meeting", layout) == "Other"

    def test_case_insensitive_match(self):
        layout = {"log_a_call": {"subject_picklist": ["call", "EMAIL", "Other"]}}
        assert _resolve_activity_type("Call", layout) == "call"
        assert _resolve_activity_type("email", layout) == "EMAIL"


class TestBuildDescription:
    def test_includes_summary(self):
        desc = build_description(SAMPLE_ENTRY)
        assert "Discussed product upgrade." in desc

    def test_includes_action_items(self):
        desc = build_description(SAMPLE_ENTRY)
        assert "Send proposal" in desc
        assert "Schedule demo" in desc

    def test_omits_empty_fields(self):
        entry = {"meeting_type": "Call", "account_name": "Test", "contact_name": "Test", "meeting_date": ""}
        desc = build_description(entry)
        assert "Action Items" not in desc
        assert "Next Steps" not in desc

    def test_includes_other_people(self):
        desc = build_description(SAMPLE_ENTRY)
        assert "Dr. Smith" in desc
