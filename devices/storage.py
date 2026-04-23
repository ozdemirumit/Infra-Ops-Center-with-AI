"""
Device storage module.
Stores device information encrypted in a JSON file.
Passwords are encrypted with Fernet (AES-128-CBC).
"""

import json
import os
import uuid
from pathlib import Path
from cryptography.fernet import Fernet
from config.settings import settings

# Data file path
DATA_DIR = Path(__file__).parent
DEVICES_FILE = DATA_DIR / "devices.json"

# Device types — dynamically retrieved from MCP Tool Registry
# Fallback: uses static values if registry is not available
_FALLBACK_DEVICE_TYPES = {
    "linux": {"label": "🐧 Linux Server", "icon": "🐧"},
    "esxi": {"label": "☁️ VMware ESXi", "icon": "☁️"},
    "router": {"label": "🌐 Router", "icon": "🌐"},
    "switch": {"label": "🔌 Switch", "icon": "🔌"},
    "deco": {"label": "📶 Deco Mesh", "icon": "📶"},
    "commvault": {"label": "💾 Commvault", "icon": "💾"},
    "windows": {"label": "🪟 Windows Server", "icon": "🪟"},
}


def _get_dynamic_device_types() -> dict:
    """Gets device types from MCP Tool Registry, falls back to static values."""
    try:
        from tools.registry import get_device_types
        dt = get_device_types()
        if dt:
            return dt
    except Exception as e:
        import logging
        logging.getLogger("devices").debug(
            f"Falling back to default device types (registry unavailable): {e}"
        )
    return _FALLBACK_DEVICE_TYPES


class _DeviceTypesProxy(dict):
    """Lazy dict — fetches current device types from registry on each access."""
    def __init__(self):
        super().__init__()

    def _refresh(self):
        self.clear()
        self.update(_get_dynamic_device_types())

    def __iter__(self):
        self._refresh()
        return super().__iter__()

    def __len__(self):
        self._refresh()
        return super().__len__()

    def items(self):
        self._refresh()
        return super().items()

    def keys(self):
        self._refresh()
        return super().keys()

    def values(self):
        self._refresh()
        return super().values()

    def __getitem__(self, key):
        self._refresh()
        return super().__getitem__(key)

    def __contains__(self, key):
        self._refresh()
        return super().__contains__(key)

    def get(self, key, default=None):
        self._refresh()
        return super().get(key, default)


DEVICE_TYPES = _DeviceTypesProxy()


def _get_cipher():
    """Returns a Fernet cipher instance."""
    key = settings.DEVICE_ENCRYPTION_KEY
    if not key:
        raise ValueError(
            "DEVICE_ENCRYPTION_KEY is not defined in the .env file. "
            "To generate: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode())


def _encrypt(text: str) -> str:
    """Encrypts text."""
    if not text:
        return ""
    return _get_cipher().encrypt(text.encode()).decode()


def _decrypt(token: str) -> str:
    """Decrypts encrypted text. Returns empty string on failure (logged)."""
    if not token:
        return ""
    try:
        return _get_cipher().decrypt(token.encode()).decode()
    except Exception as e:
        import logging
        logging.getLogger("devices").warning(
            f"Decryption failed (key mismatch or corrupt data): {type(e).__name__}"
        )
        return ""


def _load_devices() -> list:
    """Loads the device list from the JSON file (atomic read)."""
    from logging_config.atomic_io import atomic_read_json
    return atomic_read_json(DEVICES_FILE, default=[])


def _save_devices(devices: list):
    """Writes the device list to the JSON file (atomic write)."""
    from logging_config.atomic_io import atomic_write_json
    atomic_write_json(DEVICES_FILE, devices)


class DeviceStorage:
    """Device CRUD operations."""

    @staticmethod
    def list_all() -> list:
        """Returns all devices (passwords hidden, inventory included)."""
        devices = _load_devices()
        result = []
        for d in devices:
            result.append({
                "id": d["id"],
                "name": d["name"],
                "hostname": d.get("hostname", ""),
                "type": d["type"],
                "ip": d["ip"],
                "user": d["user"],
                "has_password": bool(d.get("encrypted_password")),
                "os": d.get("os", ""),
                "cpu": d.get("cpu", ""),
                "ram": d.get("ram", ""),
                "disk": d.get("disk", ""),
                "location": d.get("location", ""),
                "role": d.get("role", ""),
                "status": d.get("status", "active"),
                "notes": d.get("notes", "")
            })
        return result

    @staticmethod
    def get_by_id(device_id: str) -> dict | None:
        """Returns a device by ID (password decrypted)."""
        devices = _load_devices()
        for d in devices:
            if d["id"] == device_id:
                return {
                    "id": d["id"],
                    "name": d["name"],
                    "hostname": d.get("hostname", ""),
                    "type": d["type"],
                    "ip": d["ip"],
                    "user": d["user"],
                    "password": _decrypt(d.get("encrypted_password", "")),
                    "os": d.get("os", ""),
                    "cpu": d.get("cpu", ""),
                    "ram": d.get("ram", ""),
                    "disk": d.get("disk", ""),
                    "location": d.get("location", ""),
                    "role": d.get("role", ""),
                    "status": d.get("status", "active"),
                    "notes": d.get("notes", "")
                }
        return None

    @staticmethod
    def get_by_type(device_type: str) -> list:
        """Returns devices by type (passwords decrypted)."""
        devices = _load_devices()
        result = []
        for d in devices:
            if d["type"] == device_type:
                result.append({
                    "id": d["id"],
                    "name": d["name"],
                    "hostname": d.get("hostname", ""),
                    "type": d["type"],
                    "ip": d["ip"],
                    "user": d["user"],
                    "password": _decrypt(d.get("encrypted_password", "")),
                    "os": d.get("os", ""),
                    "cpu": d.get("cpu", ""),
                    "ram": d.get("ram", ""),
                    "disk": d.get("disk", ""),
                    "location": d.get("location", ""),
                    "role": d.get("role", ""),
                    "status": d.get("status", "active"),
                    "notes": d.get("notes", "")
                })
        return result

    @staticmethod
    def add(name: str, device_type: str, ip: str, user: str, password: str, **kwargs) -> str:
        """Adds a new device. Inventory fields can be passed via kwargs."""
        devices = _load_devices()
        device_id = str(uuid.uuid4())[:8]
        new_device = {
            "id": device_id,
            "name": name,
            "type": device_type,
            "ip": ip,
            "user": user,
            "encrypted_password": _encrypt(password),
        }

        # Add inventory fields
        inventory_fields = ["hostname", "os", "cpu", "ram", "disk", "location", "role", "status", "notes"]
        for field in inventory_fields:
            if field in kwargs:
                new_device[field] = kwargs[field]

        devices.append(new_device)
        _save_devices(devices)
        return device_id

    @staticmethod
    def update(device_id: str, name: str, device_type: str, ip: str, user: str, password: str = None, **kwargs):
        """Updates device/inventory. Password is not changed if None."""
        devices = _load_devices()
        for d in devices:
            if d["id"] == device_id:
                d["name"] = name
                d["type"] = device_type
                d["ip"] = ip
                d["user"] = user
                if password is not None:
                    d["encrypted_password"] = _encrypt(password)

                # Update inventory fields
                inventory_fields = ["hostname", "os", "cpu", "ram", "disk", "location", "role", "status", "notes"]
                for field in inventory_fields:
                    if field in kwargs:
                        d[field] = kwargs[field]

                break
        _save_devices(devices)

    @staticmethod
    def delete(device_id: str):
        """Deletes a device."""
        devices = _load_devices()
        devices = [d for d in devices if d["id"] != device_id]
        _save_devices(devices)

    @staticmethod
    def search_by_hostname(hostname: str) -> dict | None:
        """Returns the first server matching by full or partial hostname."""
        if not hostname:
            return None

        devices = DeviceStorage.list_all()
        # Exact match (priority)
        for d in devices:
            d_host = d.get("hostname", "")
            d_name = d.get("name", "")
            if (d_host and d_host.lower() == hostname.lower()) or (d_name and d_name.lower() == hostname.lower()):
                return DeviceStorage.get_by_id(d["id"])

        # Partial match
        for d in devices:
            d_host = d.get("hostname", "")
            d_name = d.get("name", "")
            if (d_host and hostname.lower() in d_host.lower()) or (d_name and hostname.lower() in d_name.lower()):
                return DeviceStorage.get_by_id(d["id"])

        return None

    @staticmethod
    def get_connections() -> dict:
        """
        Creates a connection dictionary for the agent loop.
        Uses the first device of each type. Leaves empty if no device exists.
        """
        connections = {}
        for dtype in DEVICE_TYPES:
            devices_of_type = DeviceStorage.get_by_type(dtype)
            if devices_of_type:
                d = devices_of_type[0]
                connections[dtype] = {"ip": d["ip"], "user": d["user"], "pwd": d["password"]}
            else:
                connections[dtype] = {"ip": "", "user": "", "pwd": ""}
        return connections

    @staticmethod
    def get_connections_for_selected(selected: dict) -> dict:
        """
        Creates a connection dictionary based on the user's selected device IDs.

        Args:
            selected: {"linux": "device_id", "esxi": "device_id", ...}
        """
        connections = {}
        for dtype in DEVICE_TYPES:
            device_id = selected.get(dtype)
            if device_id:
                d = DeviceStorage.get_by_id(device_id)
                if d:
                    connections[dtype] = {
                        "ip": d["ip"],
                        "user": d["user"],
                        "pwd": d["password"],
                        "name": d.get("name", ""),
                        "hostname": d.get("hostname", ""),
                        "role": d.get("role", ""),
                        "os": d.get("os", ""),
                    }
                    continue
            connections[dtype] = {"ip": "", "user": "", "pwd": "", "name": "", "hostname": ""}
        return connections
