import os
import sys

# Generate test keys before any imports touch config
from cryptography.fernet import Fernet

os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["SF_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["SUPABASE_URL"] = "https://test.supabase.co"
os.environ["SUPABASE_SERVICE_KEY"] = "test"

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from crypto import (
    encrypt_field, decrypt_field, encrypt_dict, decrypt_dict,
    encrypt_sf_credential, decrypt_sf_credential,
    CRM_ENTRY_FIELDS, is_encrypted,
)


class TestCrmEncryption:
    def test_encrypt_decrypt_string(self):
        encrypted = encrypt_field("hello world")
        assert encrypted != "hello world"
        assert is_encrypted(encrypted)
        assert decrypt_field(encrypted, "str") == "hello world"

    def test_encrypt_decrypt_list(self):
        original = ["item1", "item2"]
        encrypted = encrypt_field(original)
        assert is_encrypted(encrypted)
        result = decrypt_field(encrypted, "list")
        assert result == original

    def test_plaintext_fallback(self):
        assert decrypt_field("plain text", "str") == "plain text"
        assert decrypt_field(None, "str") is None

    def test_encrypt_decrypt_dict(self):
        data = {"account_name": "Acme Corp", "summary": "Test meeting", "id": "123"}
        encrypted = encrypt_dict(data, CRM_ENTRY_FIELDS)
        assert is_encrypted(encrypted["account_name"])
        assert is_encrypted(encrypted["summary"])
        assert encrypted["id"] == "123"

        decrypted = decrypt_dict(encrypted, CRM_ENTRY_FIELDS)
        assert decrypted["account_name"] == "Acme Corp"
        assert decrypted["summary"] == "Test meeting"


class TestSfCredentialEncryption:
    def test_encrypt_decrypt_sf_credential(self):
        encrypted = encrypt_sf_credential("my-sf-password")
        assert encrypted != "my-sf-password"
        assert is_encrypted(encrypted)
        assert decrypt_sf_credential(encrypted) == "my-sf-password"

    def test_sf_key_differs_from_crm_key(self):
        sf_encrypted = encrypt_sf_credential("password123")
        crm_encrypted = encrypt_field("password123")
        assert sf_encrypted != crm_encrypted
