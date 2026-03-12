import os
import sys

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("ENCRYPTION_KEY", "dGVzdGtleTE2Ynl0ZXNsb25nMTY=")
os.environ.setdefault("SF_ENCRYPTION_KEY", "c2Z0ZXN0a2V5MTZieXRlc2xvbmc=")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class TestStatusPayloads:
    """Verify the status update payloads are correct."""

    def test_build_claim_payload(self):
        from supabase_client import _build_claim_payload
        payload = _build_claim_payload()
        assert "processing_started_at" in payload
        assert payload["processing_started_at"] is not None

    def test_build_sent_payload(self):
        from supabase_client import _build_sent_payload
        payload = _build_sent_payload()
        assert payload["status"] == "sent"
        assert payload["sf_pushed_at"] is not None
        assert payload["send_method"] == "direct_push"
        assert payload["error_message"] is None

    def test_build_failed_payload(self):
        from supabase_client import _build_failed_payload
        payload = _build_failed_payload("Something broke", retry_count=2)
        assert payload["status"] == "send_failed"
        assert payload["error_message"] == "Something broke"
        assert payload["retry_count"] == 2
        assert payload["processing_started_at"] is None

    def test_build_retry_payload(self):
        from supabase_client import _build_retry_payload
        payload = _build_retry_payload("Timeout", retry_count=1)
        assert payload["status"] == "sending"
        assert payload["error_message"] == "Timeout"
        assert payload["retry_count"] == 1
        assert payload["processing_started_at"] is None
