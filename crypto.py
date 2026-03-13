"""
Dual-key Fernet encryption for the SF worker.

ENCRYPTION_KEY: Shared with Railway backend. Encrypts/decrypts CRM entry fields.
SF_ENCRYPTION_KEY: VPS-only. Encrypts/decrypts Salesforce credentials.
"""
import json
from cryptography.fernet import Fernet

_crm_fernet = None
_sf_fernet = None


def _get_crm_fernet() -> Fernet:
    global _crm_fernet
    if _crm_fernet is None:
        from config import ENCRYPTION_KEY
        key = ENCRYPTION_KEY.encode() if isinstance(ENCRYPTION_KEY, str) else ENCRYPTION_KEY
        _crm_fernet = Fernet(key)
    return _crm_fernet


def _get_sf_fernet() -> Fernet:
    global _sf_fernet
    if _sf_fernet is None:
        from config import SF_ENCRYPTION_KEY
        key = SF_ENCRYPTION_KEY.encode() if isinstance(SF_ENCRYPTION_KEY, str) else SF_ENCRYPTION_KEY
        _sf_fernet = Fernet(key)
    return _sf_fernet


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith("gAAAAA")


def encrypt_field(value) -> str:
    """Encrypt a value (str, list, or dict) using the CRM key."""
    if value is None:
        return value
    f = _get_crm_fernet()
    if isinstance(value, (list, dict)):
        plaintext = json.dumps(value)
    else:
        plaintext = str(value)
    return f.encrypt(plaintext.encode()).decode()


def decrypt_field(value, as_type: str = "str"):
    """Decrypt a value using the CRM key. Plaintext passes through unchanged."""
    if value is None:
        return None
    if not is_encrypted(value):
        return value
    f = _get_crm_fernet()
    plaintext = f.decrypt(value.encode()).decode()
    if as_type == "list":
        return json.loads(plaintext)
    if as_type == "dict":
        return json.loads(plaintext)
    return plaintext


def encrypt_dict(data: dict, field_specs: list) -> dict:
    """Encrypt specified fields in-place. Returns the dict."""
    for field_name, _ in field_specs:
        if field_name in data and data[field_name] is not None:
            data[field_name] = encrypt_field(data[field_name])
    return data


def decrypt_dict(data: dict, field_specs: list) -> dict:
    """Decrypt specified fields in-place. Returns the dict."""
    for field_name, field_type in field_specs:
        if field_name in data and data[field_name] is not None:
            data[field_name] = decrypt_field(data[field_name], field_type)
    return data


def encrypt_sf_credential(value: str) -> str:
    """Encrypt a Salesforce credential using the SF-only key."""
    f = _get_sf_fernet()
    return f.encrypt(value.encode()).decode()


def decrypt_sf_credential(value: str) -> str:
    """Decrypt a Salesforce credential using the SF-only key."""
    if not is_encrypted(value):
        return value
    f = _get_sf_fernet()
    return f.decrypt(value.encode()).decode()


# Field specs matching RepDrive backend's crypto.py CRM_ENTRY_FIELDS
CRM_ENTRY_FIELDS = [
    ("account_name", "str"),
    ("contact_name", "str"),
    ("other_people_mentioned", "list"),
    ("summary", "str"),
    ("key_details", "str"),
    ("action_items", "list"),
    ("next_steps", "list"),
    ("opportunities", "str"),
    # meeting_type excluded: has DB check constraint, kept as plaintext
]
