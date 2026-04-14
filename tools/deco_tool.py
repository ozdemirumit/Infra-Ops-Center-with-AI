"""
TP-Link Deco Mesh Wi-Fi web API tool.
Connects to the Deco web interface via http://tplinkdeco.net/.
Can manage connected devices, node status, guest network.
MCP tool definition is in this file.
"""

import requests
import urllib3
import json
import hashlib
import base64
from logging_config.logger import get_logger, audit_log, AuditEvent

# Disable self-signed certificate warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = get_logger("tools")

# ─── MCP Tool Definition ───

DECO_OPS_TOOL = {
    "name": "deco_ops",
    "description": (
        "Makes web API requests on TP-Link Deco Mesh Wi-Fi system. "
        "List connected devices, node status, enable/disable guest network, "
        "Wi-Fi settings, firmware info and more."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"action": {"type": "string", "description": "Operation to perform on Deco"}},
        "required": ["action"]
    }
}


class DecoSession:
    """TP-Link Deco web interface session manager."""

    # Deco is always accessed through this address
    DECO_URL = "http://tplinkdeco.net"

    def __init__(self, host: str, user: str, pwd: str):
        self.base_url = self.DECO_URL
        self.fallback_url = f"http://{host}" if host else None
        self.user = user
        self.pwd = pwd
        self.session = requests.Session()
        self.session.verify = False
        self.session.timeout = 15
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
            "Referer": self.base_url,
        })
        self._stok = None
        self._logged_in = False

    def login(self) -> bool:
        """Logs into the Deco web interface."""
        try:
            # Deco login — TP-Link devices typically use password hash + stok token
            pwd_encoded = base64.b64encode(self.pwd.encode()).decode()

            login_payload = {
                "method": "do",
                "login": {
                    "password": pwd_encoded
                }
            }

            resp = self.session.post(
                f"{self.base_url}/cgi-bin/luci/;stok=/login?form=login",
                json=login_payload,
                timeout=10
            )

            if resp.status_code == 200:
                try:
                    data = resp.json()
                    if "stok" in str(data):
                        # Extract stok token
                        self._stok = data.get("stok", "")
                        self._logged_in = True
                        logger.info(f"Deco login successful: {self.base_url}")
                        return True
                except (json.JSONDecodeError, ValueError):
                    pass

            # Alternative login endpoints (tplinkdeco.net + IP fallback)
            alt_payloads = [
                {"username": self.user, "password": self.pwd},
                {"password": hashlib.md5(self.pwd.encode()).hexdigest()},
            ]
            alt_endpoints = [
                f"{self.base_url}/",
                f"{self.base_url}/cgi-bin/luci",
                f"{self.base_url}/login",
            ]
            # Also try via IP
            if self.fallback_url and self.fallback_url != self.base_url:
                alt_endpoints.extend([
                    f"{self.fallback_url}/",
                    f"{self.fallback_url}/cgi-bin/luci/;stok=/login?form=login",
                ])

            for endpoint in alt_endpoints:
                for payload in alt_payloads:
                    try:
                        resp = self.session.post(endpoint, json=payload, timeout=8)
                        if resp.status_code == 200 and self.session.cookies:
                            self._logged_in = True
                            logger.info(f"Deco login successful (alt): {self.base_url}")
                            return True
                    except Exception:
                        continue

            logger.warning(f"Deco login failed: {self.base_url}")
            return False

        except Exception as e:
            logger.error(f"Deco login error: {str(e)}")
            return False

    def api_call(self, method: str, params: dict = None) -> dict:
        """Sends a request to the Deco API."""
        if not self._logged_in:
            if not self.login():
                return {"error": "Failed to log in to Deco"}

        try:
            stok_path = f"/stok={self._stok}" if self._stok else ""
            url = f"{self.base_url}/cgi-bin/luci/;{stok_path}/admin/network?form={method}"

            payload = {"method": "get"}
            if params:
                payload.update(params)

            resp = self.session.post(url, json=payload, timeout=15)

            try:
                return resp.json()
            except (json.JSONDecodeError, ValueError):
                return {"raw_response": resp.text[:2000]}

        except Exception as e:
            return {"error": str(e)}

    def get_page(self, path: str) -> str:
        """Fetches a web page from Deco."""
        if not self._logged_in:
            if not self.login():
                return "❌ Failed to log in to Deco."

        try:
            url = f"{self.base_url}/{path.lstrip('/')}"
            resp = self.session.get(url, timeout=15)
            return resp.text
        except Exception as e:
            return f"❌ Failed to fetch page ({path}): {str(e)}"

    def close(self):
        """Closes the session."""
        self.session.close()


# ─── Known Deco Operations ───

DECO_ACTIONS = {
    "client_list": {"form": "client_list", "desc": "Connected device list"},
    "device_list": {"form": "device_list", "desc": "Deco node list"},
    "wan": {"form": "wan", "desc": "WAN connection info"},
    "wireless": {"form": "wireless", "desc": "Wi-Fi settings"},
    "guest_network": {"form": "guest_network", "desc": "Guest network settings"},
    "firmware": {"form": "firmware", "desc": "Firmware info"},
    "status": {"form": "status", "desc": "General status"},
    "lan": {"form": "lan", "desc": "LAN settings"},
}


def execute_deco_api(host: str, user: str, pwd: str, action: str) -> str:
    """
    Connects to the Deco web interface via HTTP and performs the operation.

    Args:
        host: Deco IP address
        user: Web interface username
        pwd: Web interface password
        action: Operation to perform
    """
    if not host:
        return "❌ Deco IP address is not defined. Add a Deco from the Device Management page."

    logger.info(f"Deco API request: {host} | action={action}")
    audit_log(
        AuditEvent.COMMAND_EXECUTE,
        target=host,
        detail=f"Deco web: {action[:100]}",
        extra={"tool": "deco", "protocol": "HTTP"}
    )

    deco = DecoSession(host, user, pwd)

    try:
        action_lower = action.lower()
        api_form = None

        # Determine the appropriate API form from the command
        if any(k in action_lower for k in ["cihaz", "bağlı", "client", "connected", "device list"]):
            api_form = "client_list"
        elif any(k in action_lower for k in ["node", "mesh", "ünite", "device", "unit"]):
            api_form = "device_list"
        elif any(k in action_lower for k in ["misafir", "guest"]):
            api_form = "guest_network"
        elif any(k in action_lower for k in ["wifi", "wi-fi", "kablosuz", "wireless", "ssid"]):
            api_form = "wireless"
        elif any(k in action_lower for k in ["firmware", "güncelleme", "versiyon", "update", "version"]):
            api_form = "firmware"
        elif any(k in action_lower for k in ["wan", "internet", "dış ağ", "external"]):
            api_form = "wan"
        elif any(k in action_lower for k in ["lan", "yerel", "local"]):
            api_form = "lan"
        else:
            api_form = "status"

        # API call
        result = deco.api_call(api_form)

        # Format result
        if isinstance(result, dict):
            if "error" in result:
                error_msg = f"❌ Deco API error: {result['error']}"
                audit_log(AuditEvent.COMMAND_RESULT, target=host, detail=error_msg[:100], success=False)
                return error_msg

            formatted = json.dumps(result, indent=2, ensure_ascii=False)
            if len(formatted) > 3000:
                formatted = formatted[:3000] + "\n... (truncated)"
        else:
            formatted = str(result)[:3000]

        audit_log(
            AuditEvent.COMMAND_RESULT, target=host,
            detail=f"Deco result: {len(formatted)} characters ({api_form})",
            success=True
        )
        return f"[Deco {host} -> {api_form}]\n{formatted}"

    except Exception as e:
        error_msg = f"❌ Deco Error ({host}): {str(e)}"
        logger.error(error_msg)
        audit_log(AuditEvent.COMMAND_RESULT, target=host, detail=f"Deco error: {str(e)[:100]}", success=False)
        return error_msg

    finally:
        deco.close()
