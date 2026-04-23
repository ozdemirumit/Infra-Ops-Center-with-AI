"""
Input validators and error-message helpers.

Centralizes validation for device names, IPs, SSH commands, and custom tool inputs.
"""

import re
from ipaddress import ip_address

MAX_DEVICE_NAME_LEN = 80
MAX_COMMAND_LEN = 10000
MAX_USERNAME_LEN = 64


def validate_device_name(name: str) -> tuple[bool, str]:
    """Returns (is_valid, error_message)."""
    if not name or not name.strip():
        return False, "Device name is required."
    name = name.strip()
    if len(name) > MAX_DEVICE_NAME_LEN:
        return False, f"Device name too long (max {MAX_DEVICE_NAME_LEN} characters)."
    if any(c in name for c in ["/", "\\", "\x00", "<", ">"]):
        return False, "Device name contains invalid characters."
    return True, ""


def validate_ip_or_hostname(value: str) -> tuple[bool, str]:
    """Accept IPv4/IPv6 address or DNS hostname."""
    if not value or not value.strip():
        return False, "IP or hostname is required."
    value = value.strip()

    # Try IP first
    try:
        ip_address(value)
        return True, ""
    except ValueError:
        pass

    # Otherwise validate as hostname
    if len(value) > 253:
        return False, "Hostname too long."
    if not re.match(r"^[a-zA-Z0-9](?:[a-zA-Z0-9\-\.]*[a-zA-Z0-9])?$", value):
        return False, f"'{value}' is not a valid IP address or hostname."
    return True, ""


def validate_username(user: str) -> tuple[bool, str]:
    if not user or not user.strip():
        return False, "Username is required."
    user = user.strip()
    if len(user) > MAX_USERNAME_LEN:
        return False, f"Username too long (max {MAX_USERNAME_LEN} characters)."
    if not re.match(r"^[a-zA-Z0-9_\-\.]+$", user):
        return False, "Username contains invalid characters."
    return True, ""


def validate_ssh_command(cmd: str) -> tuple[bool, str]:
    """Basic sanity checks on SSH command before execution."""
    if not cmd or not cmd.strip():
        return False, "Command is empty."
    if len(cmd) > MAX_COMMAND_LEN:
        return False, f"Command too long (max {MAX_COMMAND_LEN} characters)."
    if "\x00" in cmd:
        return False, "Command contains null bytes (not allowed)."
    return True, ""


def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename to prevent path traversal.
    Returns a safe basename-only version.
    """
    import os
    # Remove any path components
    name = os.path.basename(filename)
    # Strip dangerous characters
    name = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", name)
    # Prevent hidden files / traversal
    name = name.lstrip(".")
    return name[:200] or "unnamed"
