"""
Unit tests for auth/vault.py — encrypted credential store.
"""

import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Use a throwaway encryption key for tests
from cryptography.fernet import Fernet
os.environ["DEVICE_ENCRYPTION_KEY"] = Fernet.generate_key().decode()


def _fresh_vault(monkeypatch=None):
    """Point the vault at a fresh temp file before each test."""
    from auth import vault
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json").name
    vault._VAULT_FILE = Path(tmp)
    return Path(tmp)


def test_set_and_get():
    from auth import vault
    _fresh_vault()
    vault.set_secret("api_keys", "test", "supersecret123")
    assert vault.get_secret("api_keys", "test") == "supersecret123"


def test_missing_returns_empty():
    from auth import vault
    _fresh_vault()
    assert vault.get_secret("api_keys", "does_not_exist") == ""


def test_has_secret():
    from auth import vault
    _fresh_vault()
    vault.set_secret("devices", "server01", "pwd")
    assert vault.has_secret("devices", "server01")
    assert not vault.has_secret("devices", "nope")


def test_delete_secret():
    from auth import vault
    _fresh_vault()
    vault.set_secret("api_keys", "deleteme", "val")
    assert vault.delete_secret("api_keys", "deleteme")
    assert not vault.has_secret("api_keys", "deleteme")
    # Deleting again returns False
    assert not vault.delete_secret("api_keys", "deleteme")


def test_list_secrets_never_returns_value():
    from auth import vault
    _fresh_vault()
    vault.set_secret("api_keys", "sensitive", "should_not_leak_xxx")
    rows = vault.list_secrets()
    assert any(r["name"] == "sensitive" for r in rows)
    for r in rows:
        # Verify the raw value is not in any field
        for v in r.values():
            assert "should_not_leak_xxx" not in str(v)


def test_list_categories():
    from auth import vault
    _fresh_vault()
    vault.set_secret("api_keys", "a", "1")
    vault.set_secret("api_keys", "b", "2")
    vault.set_secret("devices", "x", "3")
    cats = {c["category"]: c["count"] for c in vault.list_categories()}
    assert cats.get("api_keys") == 2
    assert cats.get("devices") == 1


def test_category_filter():
    from auth import vault
    _fresh_vault()
    vault.set_secret("api_keys", "openai", "k1")
    vault.set_secret("devices", "srv01", "k2")
    rows = vault.list_secrets(category="api_keys")
    assert len(rows) == 1
    assert rows[0]["name"] == "openai"


def test_vault_resolves_in_http_backend_helper():
    from auth import vault
    from tools.registry import _resolve_vault_refs
    _fresh_vault()
    vault.set_secret("api_keys", "github", "ghp_abc123")
    resolved = _resolve_vault_refs("Bearer ${vault:api_keys/github}")
    assert resolved == "Bearer ghp_abc123"


def test_vault_resolves_unknown_to_empty():
    from tools.registry import _resolve_vault_refs
    resolved = _resolve_vault_refs("Bearer ${vault:api_keys/does_not_exist}")
    assert resolved == "Bearer "


def test_unchanged_when_no_placeholder():
    from tools.registry import _resolve_vault_refs
    assert _resolve_vault_refs("hello world") == "hello world"
    assert _resolve_vault_refs("") == ""


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
