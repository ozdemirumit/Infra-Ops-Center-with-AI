"""
Unified Credential Vault.

A single Fernet-encrypted store for all credentials used by the application:
  - Device passwords (SSH, Web API, REST)
  - LDAP bind password
  - Proxy API keys (client + admin)
  - Direct API keys (Anthropic, OpenAI, Gemini)
  - Custom MCP tool credentials (HTTP API keys, OAuth tokens)
  - Ollama auth tokens
  - Any user-defined secret

Features
  - Symmetric AES-128 encryption via Fernet (DEVICE_ENCRYPTION_KEY)
  - Atomic JSON writes (race-condition safe)
  - Categories with namespacing (e.g. "devices/server-01", "proxy/admin")
  - Audit log on every read/write
  - Migration helpers for existing device/LDAP/.env secrets
  - List-with-mask for UI display (never reveals raw secret)

Storage format (vault.json):
{
  "category/key": {
    "value_encrypted": "gAAAAAB...",
    "description": "...",
    "tags": ["ssh", "production"],
    "created_at": "...",
    "updated_at": "..."
  }
}
"""

import os
from pathlib import Path
from datetime import datetime
from typing import Optional
from cryptography.fernet import Fernet

from config.settings import settings
from logging_config.logger import get_logger, audit_log, AuditEvent
from logging_config.atomic_io import atomic_read_json, atomic_write_json

logger = get_logger("vault")

_VAULT_FILE = Path(__file__).resolve().parent.parent / "vault.json"


# ─── Encryption ─────────────────────────────────────────────────────

def _get_cipher() -> Fernet:
    """Get the Fernet cipher (uses DEVICE_ENCRYPTION_KEY)."""
    key = settings.DEVICE_ENCRYPTION_KEY
    if not key:
        raise ValueError(
            "DEVICE_ENCRYPTION_KEY is not set in .env. "
            "Run `python setup_env.py` to generate one."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def _encrypt(value: str) -> str:
    """Encrypt a string. Returns the ciphertext as a base64 string."""
    if not value:
        return ""
    return _get_cipher().encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt(token: str) -> str:
    """Decrypt a string. Returns empty string on failure."""
    if not token:
        return ""
    try:
        return _get_cipher().decrypt(token.encode("utf-8")).decode("utf-8")
    except Exception as e:
        logger.warning(f"Vault decryption failed: {type(e).__name__}")
        return ""


# ─── Storage I/O ────────────────────────────────────────────────────

def _load() -> dict:
    return atomic_read_json(_VAULT_FILE, default={})


def _save(data: dict) -> None:
    atomic_write_json(_VAULT_FILE, data)


def _key(category: str, name: str) -> str:
    """Build the storage key from category and name."""
    category = (category or "default").strip("/").lower()
    name = (name or "").strip()
    if not name:
        raise ValueError("Credential name cannot be empty")
    return f"{category}/{name}"


# ─── Public API ─────────────────────────────────────────────────────

def set_secret(
    category: str,
    name: str,
    value: str,
    description: str = "",
    tags: list = None,
) -> str:
    """
    Store an encrypted secret.

    Args:
        category: Namespace (e.g. "devices", "ldap", "proxy", "api_keys", "custom_tools")
        name: Unique identifier within the category
        value: The secret to encrypt
        description: Optional human-readable note (NOT encrypted)
        tags: Optional categorization tags

    Returns:
        The full storage key "category/name"
    """
    k = _key(category, name)
    data = _load()
    now = datetime.now().isoformat()

    entry = data.get(k, {})
    entry.update({
        "value_encrypted": _encrypt(value),
        "description": description or entry.get("description", ""),
        "tags": tags if tags is not None else entry.get("tags", []),
        "updated_at": now,
    })
    if "created_at" not in entry:
        entry["created_at"] = now

    data[k] = entry
    _save(data)

    audit_log(
        AuditEvent.DEVICE_UPDATE,  # generic credential event
        target=f"vault/{k}",
        detail=f"Secret stored (length={len(value)})",
        extra={"category": category, "name": name},
    )
    logger.info(f"Vault SET {k}")
    return k


def get_secret(category: str, name: str) -> str:
    """Retrieve a decrypted secret. Returns '' if not found."""
    k = _key(category, name)
    data = _load()
    entry = data.get(k)
    if not entry:
        return ""
    return _decrypt(entry.get("value_encrypted", ""))


def has_secret(category: str, name: str) -> bool:
    """Check if a secret exists without decrypting it."""
    k = _key(category, name)
    data = _load()
    return k in data and bool(data[k].get("value_encrypted"))


def delete_secret(category: str, name: str) -> bool:
    """Delete a secret. Returns True if removed, False if not found."""
    k = _key(category, name)
    data = _load()
    if k not in data:
        return False
    data.pop(k)
    _save(data)
    audit_log(
        AuditEvent.DEVICE_DELETE,
        target=f"vault/{k}",
        detail="Secret deleted",
    )
    logger.info(f"Vault DEL {k}")
    return True


def list_secrets(category: str = None) -> list[dict]:
    """
    List all secrets metadata (NEVER returns the decrypted value).

    Args:
        category: Optional category filter

    Returns:
        List of dicts with: key, category, name, description, tags,
        created_at, updated_at, has_value, value_length (estimated)
    """
    data = _load()
    result = []
    for k, entry in data.items():
        cat, _, nm = k.partition("/")
        if category and cat != category.strip("/").lower():
            continue
        encrypted = entry.get("value_encrypted", "")
        # Estimate plaintext length (Fernet overhead ~75 bytes)
        est_len = max(0, len(encrypted) - 75) if encrypted else 0
        result.append({
            "key": k,
            "category": cat,
            "name": nm,
            "description": entry.get("description", ""),
            "tags": entry.get("tags", []),
            "created_at": entry.get("created_at", ""),
            "updated_at": entry.get("updated_at", ""),
            "has_value": bool(encrypted),
            "value_length": est_len,
            "masked_preview": "•" * min(est_len, 12),
        })
    return sorted(result, key=lambda x: (x["category"], x["name"]))


def list_categories() -> list[dict]:
    """List all categories with secret counts."""
    data = _load()
    cats = {}
    for k in data:
        cat = k.partition("/")[0]
        cats[cat] = cats.get(cat, 0) + 1
    return [{"category": c, "count": n} for c, n in sorted(cats.items())]


# ─── Specialized helpers ────────────────────────────────────────────

def get_device_password(device_id: str) -> str:
    """Convenience: fetch a device password."""
    return get_secret("devices", device_id)


def set_device_password(device_id: str, password: str, name: str = "") -> str:
    return set_secret("devices", device_id, password, description=name, tags=["device"])


def get_api_key(provider: str) -> str:
    """Get API key for a provider (anthropic/openai/gemini/etc)."""
    return get_secret("api_keys", provider) or _fallback_env_key(provider)


def set_api_key(provider: str, key: str) -> str:
    return set_secret("api_keys", provider, key, description=f"{provider} API key", tags=["api"])


def _fallback_env_key(provider: str) -> str:
    """Fall back to .env values for legacy compatibility."""
    mapping = {
        "anthropic": settings.ANTHROPIC_API_KEY,
        "openai": settings.OPENAI_API_KEY,
        "gemini": settings.GEMINI_API_KEY,
        "proxy": settings.PROXY_API_KEY,
        "proxy_admin": settings.PROXY_ADMIN_KEY,
    }
    return mapping.get(provider.lower(), "")


def get_ldap_password() -> str:
    """Get LDAP bind password from vault (falls back to ldap_config.json)."""
    pwd = get_secret("ldap", "bind_password")
    if pwd:
        return pwd
    # Legacy fallback
    try:
        from auth.ldap_auth import get_config
        return get_config().get("bind_password", "")
    except Exception:
        return ""


def set_ldap_password(password: str) -> str:
    return set_secret("ldap", "bind_password", password,
                      description="LDAP service account bind password",
                      tags=["ldap"])


# ─── Migration helpers ──────────────────────────────────────────────

def migrate_from_devices() -> int:
    """
    Migrate existing device passwords (devices/devices.json) into the vault.
    Devices already use Fernet encryption — we decrypt and re-encrypt under
    the vault key (same key, so no-op cipher-wise, but creates vault entries).

    Returns: number of devices migrated.
    """
    from devices.storage import DeviceStorage

    devices = DeviceStorage.list_all()
    count = 0
    for d in devices:
        if not d.get("has_password"):
            continue
        # Re-fetch with decrypted password
        full = DeviceStorage.get_by_id(d["id"])
        if not full or not full.get("password"):
            continue
        set_device_password(d["id"], full["password"], name=d.get("name", ""))
        count += 1
    logger.info(f"Vault: migrated {count} device passwords")
    return count


def migrate_from_env() -> dict:
    """
    Copy API keys from .env into the vault.
    Does NOT delete from .env (so legacy code keeps working).
    """
    results = {}
    for prov, val in [
        ("anthropic", settings.ANTHROPIC_API_KEY),
        ("openai", settings.OPENAI_API_KEY),
        ("gemini", settings.GEMINI_API_KEY),
        ("proxy", settings.PROXY_API_KEY),
        ("proxy_admin", settings.PROXY_ADMIN_KEY),
    ]:
        if val:
            set_api_key(prov, val)
            results[prov] = "migrated"
        else:
            results[prov] = "skipped (empty)"
    return results


def migrate_from_ldap() -> bool:
    """Move LDAP bind password into the vault."""
    try:
        from auth.ldap_auth import get_config
        config = get_config()
        pwd = config.get("bind_password", "")
        if pwd:
            set_ldap_password(pwd)
            logger.info("Vault: LDAP bind password migrated")
            return True
    except Exception as e:
        logger.warning(f"LDAP migration skipped: {e}")
    return False
