"""
LDAP / Active Directory authentication.

Connects to an LDAP server (e.g. Active Directory) and authenticates users
via simple bind. Supports group-based role mapping (LDAP groups → admin/viewer).

Configuration is stored in ldap_config.json (atomic write).
Falls back gracefully if ldap3 library is not installed.
"""

import os
from pathlib import Path
from typing import Optional
from datetime import datetime

from logging_config.logger import get_logger, audit_log, AuditEvent
from logging_config.atomic_io import atomic_read_json, atomic_write_json

logger = get_logger("ldap_auth")

_CONFIG_FILE = Path(__file__).resolve().parent.parent / "ldap_config.json"

# Default config
DEFAULT_CONFIG = {
    "enabled": False,
    "server": "",                       # ldap://dc.example.com  or  ldaps://dc.example.com:636
    "port": 389,                        # 389 plain · 636 SSL
    "use_ssl": False,
    "use_tls": False,                   # StartTLS over 389
    "base_dn": "",                      # DC=example,DC=com
    "user_dn_template": "",             # CN={username},OU=Users,DC=example,DC=com  (optional)
    "user_search_filter": "(sAMAccountName={username})",   # AD default; OpenLDAP: (uid={username})
    "user_search_base": "",             # OU=Users,DC=example,DC=com (defaults to base_dn)
    "bind_dn": "",                      # Service account DN for search bind (optional)
    "bind_password": "",                # Service account password
    "admin_groups": [],                 # ["CN=Admins,OU=Groups,DC=example,DC=com"]
    "viewer_groups": [],                # ["CN=Users,OU=Groups,DC=example,DC=com"]
    "default_role": "viewer",           # When user matches no group
    "attribute_username": "sAMAccountName",
    "attribute_email": "mail",
    "attribute_displayname": "displayName",
    "connect_timeout": 5,
}


def get_config() -> dict:
    """Returns current LDAP config (merged with defaults)."""
    saved = atomic_read_json(_CONFIG_FILE, default={})
    config = dict(DEFAULT_CONFIG)
    config.update(saved)
    return config


def save_config(config: dict) -> None:
    """Persist LDAP config to disk."""
    # Sanity: ensure all default keys exist
    full = dict(DEFAULT_CONFIG)
    full.update(config)
    atomic_write_json(_CONFIG_FILE, full)
    logger.info("LDAP config updated")


def is_ldap_enabled() -> bool:
    return get_config().get("enabled", False)


def is_available() -> bool:
    """Returns True if the ldap3 library is importable."""
    try:
        import ldap3  # noqa: F401
        return True
    except ImportError:
        return False


# ─── Core authentication ─────────────────────────────────────────────

def authenticate(username: str, password: str) -> Optional[dict]:
    """
    Authenticate against LDAP/AD.

    Returns:
        dict with user info on success:
            {"username", "display_name", "email", "role", "groups"}
        None on failure.
    """
    if not is_available():
        logger.warning("ldap3 library not installed — `pip install ldap3`")
        return None

    config = get_config()
    if not config.get("enabled"):
        return None

    try:
        from ldap3 import Server, Connection, ALL, SUBTREE, Tls
        import ssl
    except ImportError:
        logger.error("ldap3 import failed")
        return None

    server_uri = config.get("server", "").strip()
    if not server_uri:
        logger.error("LDAP server not configured")
        return None

    try:
        # Build server
        tls_config = None
        if config.get("use_ssl") or config.get("use_tls"):
            tls_config = Tls(validate=ssl.CERT_NONE)  # Skip cert validation for self-signed

        server = Server(
            server_uri,
            port=config.get("port", 389),
            use_ssl=config.get("use_ssl", False),
            tls=tls_config,
            get_info=ALL,
            connect_timeout=config.get("connect_timeout", 5),
        )

        # ─── Step 1: Determine user DN ────────────────────────────
        user_dn = None

        if config.get("user_dn_template"):
            # Direct DN template (faster, no search needed)
            user_dn = config["user_dn_template"].format(username=username)
        else:
            # Search bind: connect with service account, find user DN
            bind_dn = config.get("bind_dn", "")
            bind_pwd = config.get("bind_password", "")

            search_conn = Connection(
                server,
                user=bind_dn or None,
                password=bind_pwd or None,
                auto_bind=False,
            )
            if config.get("use_tls"):
                search_conn.start_tls()
            if not search_conn.bind():
                logger.error(f"LDAP service bind failed: {search_conn.result}")
                return None

            search_base = config.get("user_search_base") or config.get("base_dn", "")
            search_filter = config["user_search_filter"].format(username=_escape_filter(username))

            search_conn.search(
                search_base=search_base,
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=[
                    config.get("attribute_username", "sAMAccountName"),
                    config.get("attribute_email", "mail"),
                    config.get("attribute_displayname", "displayName"),
                    "memberOf",
                ],
            )

            if not search_conn.entries:
                logger.warning(f"LDAP user not found: {username}")
                search_conn.unbind()
                return None

            entry = search_conn.entries[0]
            user_dn = entry.entry_dn
            user_groups = list(entry.memberOf.values) if hasattr(entry, "memberOf") else []
            display_name = str(getattr(entry, config.get("attribute_displayname", "displayName"), username))
            email = str(getattr(entry, config.get("attribute_email", "mail"), ""))
            search_conn.unbind()

        # ─── Step 2: Bind as user (validates password) ───────────
        user_conn = Connection(
            server,
            user=user_dn,
            password=password,
            auto_bind=False,
        )
        if config.get("use_tls"):
            user_conn.start_tls()

        if not user_conn.bind():
            logger.warning(f"LDAP user bind failed for {username}: {user_conn.result.get('description', '')}")
            return None

        # ─── Step 3: If dn_template was used, fetch attrs now ────
        if config.get("user_dn_template"):
            user_conn.search(
                search_base=user_dn,
                search_filter="(objectClass=*)",
                search_scope="BASE",
                attributes=[
                    config.get("attribute_email", "mail"),
                    config.get("attribute_displayname", "displayName"),
                    "memberOf",
                ],
            )
            if user_conn.entries:
                entry = user_conn.entries[0]
                user_groups = list(entry.memberOf.values) if hasattr(entry, "memberOf") else []
                display_name = str(getattr(entry, config.get("attribute_displayname", "displayName"), username))
                email = str(getattr(entry, config.get("attribute_email", "mail"), ""))
            else:
                user_groups, display_name, email = [], username, ""

        user_conn.unbind()

        # ─── Step 4: Determine role from groups ──────────────────
        role = _resolve_role(user_groups, config)

        logger.info(f"LDAP login successful: {username} → role={role}")
        return {
            "username": username,
            "display_name": display_name or username,
            "email": email,
            "role": role,
            "groups": user_groups,
            "dn": user_dn,
        }

    except Exception as e:
        logger.error(f"LDAP authentication error: {type(e).__name__}: {e}")
        return None


def _escape_filter(s: str) -> str:
    """Escape special chars in LDAP filter strings."""
    replacements = {"\\": r"\5c", "*": r"\2a", "(": r"\28", ")": r"\29", "\x00": r"\00"}
    for k, v in replacements.items():
        s = s.replace(k, v)
    return s


def _resolve_role(user_groups: list, config: dict) -> str:
    """Map LDAP groups to app role (admin / viewer)."""
    admin_groups = [g.lower() for g in config.get("admin_groups", []) if g]
    viewer_groups = [g.lower() for g in config.get("viewer_groups", []) if g]

    user_groups_lower = [str(g).lower() for g in user_groups]

    for ag in admin_groups:
        if any(ag in g or g in ag for g in user_groups_lower):
            return "admin"
    for vg in viewer_groups:
        if any(vg in g or g in vg for g in user_groups_lower):
            return "viewer"

    return config.get("default_role", "viewer")


# ─── Test helper (for UI "Test Connection" button) ──────────────────

def test_connection(config: dict = None) -> tuple[bool, str]:
    """Test LDAP server connectivity without authenticating any user."""
    if not is_available():
        return False, "ldap3 library not installed. Run: pip install ldap3"

    config = config or get_config()
    if not config.get("server"):
        return False, "Server URI is empty."

    try:
        from ldap3 import Server, Connection, ALL, Tls
        import ssl

        tls_config = None
        if config.get("use_ssl") or config.get("use_tls"):
            tls_config = Tls(validate=ssl.CERT_NONE)

        server = Server(
            config["server"],
            port=config.get("port", 389),
            use_ssl=config.get("use_ssl", False),
            tls=tls_config,
            get_info=ALL,
            connect_timeout=config.get("connect_timeout", 5),
        )

        bind_dn = config.get("bind_dn", "")
        bind_pwd = config.get("bind_password", "")

        conn = Connection(server, user=bind_dn or None, password=bind_pwd or None, auto_bind=False)
        if config.get("use_tls"):
            conn.start_tls()

        if conn.bind():
            info = server.info if server.info else None
            conn.unbind()
            return True, f"✅ Connected. {('Vendor: ' + str(info.vendor_name)) if info and info.vendor_name else 'Bind successful.'}"
        else:
            return False, f"Bind failed: {conn.result.get('description', conn.result)}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def test_user_search(username: str, config: dict = None) -> tuple[bool, str, dict]:
    """Test searching for a specific user (without authenticating)."""
    if not is_available():
        return False, "ldap3 not installed", {}

    config = config or get_config()

    try:
        from ldap3 import Server, Connection, ALL, SUBTREE, Tls
        import ssl

        tls_config = None
        if config.get("use_ssl") or config.get("use_tls"):
            tls_config = Tls(validate=ssl.CERT_NONE)

        server = Server(
            config["server"],
            port=config.get("port", 389),
            use_ssl=config.get("use_ssl", False),
            tls=tls_config,
            get_info=ALL,
            connect_timeout=config.get("connect_timeout", 5),
        )

        conn = Connection(
            server,
            user=config.get("bind_dn") or None,
            password=config.get("bind_password") or None,
            auto_bind=False,
        )
        if config.get("use_tls"):
            conn.start_tls()
        if not conn.bind():
            return False, f"Service bind failed: {conn.result}", {}

        search_base = config.get("user_search_base") or config.get("base_dn", "")
        search_filter = config["user_search_filter"].format(username=_escape_filter(username))

        conn.search(
            search_base=search_base,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=["sAMAccountName", "uid", "mail", "displayName", "memberOf"],
        )

        if not conn.entries:
            conn.unbind()
            return False, f"User '{username}' not found.", {}

        entry = conn.entries[0]
        result = {
            "dn": entry.entry_dn,
            "displayName": str(getattr(entry, "displayName", "")),
            "mail": str(getattr(entry, "mail", "")),
            "groups": list(entry.memberOf.values) if hasattr(entry, "memberOf") else [],
        }
        conn.unbind()
        return True, f"✅ Found user.", result

    except Exception as e:
        return False, f"{type(e).__name__}: {e}", {}
