"""
TP-Link TL-SG1016PE Switch web API tool.
Connects to the switch web interface via HTTP session.
Can retrieve port status, PoE settings, VLAN information.
MCP tool definition is in this file.
"""

import requests
import urllib3
import hashlib
from logging_config.logger import get_logger, audit_log, AuditEvent

# Disable self-signed certificate warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = get_logger("tools")

# ─── MCP Tool Definition ───

SWITCH_OPS_TOOL = {
    "name": "switch_ops",
    "description": (
        "Makes web interface requests on TP-Link TL-SG1016PE Switch. "
        "Can retrieve port status, PoE settings, VLAN information, statistics. "
        "Use commands like 'check port status', 'get PoE info', etc."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"action": {"type": "string", "description": "Operation to perform on the switch"}},
        "required": ["action"]
    }
}


class SwitchSession:
    """TP-Link Switch web interface session manager."""

    def __init__(self, host: str, user: str, pwd: str):
        self.base_url = f"http://{host}"
        self.user = user
        self.pwd = pwd
        self.session = requests.Session()
        self.session.verify = False
        self.session.timeout = 15
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/html, */*",
            "Referer": self.base_url,
        })
        self._logged_in = False

    def login(self) -> bool:
        """Logs into the switch web interface."""
        try:
            # TP-Link switches typically use MD5 hash for login
            pwd_hash = hashlib.md5(self.pwd.encode()).hexdigest()

            login_data = {
                "username": self.user,
                "password": pwd_hash,
                "logon": "Login",
            }

            # Alternative: some models accept plain text password
            resp = self.session.post(
                f"{self.base_url}/logon.cgi",
                data=login_data,
                allow_redirects=True,
                timeout=10
            )

            if resp.status_code == 200:
                # Cookie check — successful login sets a cookie
                if self.session.cookies or "logout" in resp.text.lower() or "main" in resp.text.lower():
                    self._logged_in = True
                    logger.info(f"Switch login successful: {self.base_url}")
                    return True

            # Alternative login endpoint
            resp = self.session.post(
                f"{self.base_url}/",
                data={"username": self.user, "password": self.pwd},
                allow_redirects=True,
                timeout=10
            )
            if resp.status_code == 200 and self.session.cookies:
                self._logged_in = True
                logger.info(f"Switch login successful (alt): {self.base_url}")
                return True

            logger.warning(f"Switch login failed: {self.base_url}")
            return False

        except Exception as e:
            logger.error(f"Switch login error: {str(e)}")
            return False

    def get_page(self, path: str) -> str:
        """Fetches a page/endpoint from the switch."""
        if not self._logged_in:
            if not self.login():
                return "❌ Could not log in to the switch."

        try:
            url = f"{self.base_url}/{path.lstrip('/')}"
            resp = self.session.get(url, timeout=15)
            return resp.text
        except Exception as e:
            return f"❌ Could not fetch page ({path}): {str(e)}"

    def close(self):
        """Closes the session."""
        try:
            self.session.get(f"{self.base_url}/logoff.cgi", timeout=5)
        except Exception:
            pass
        self.session.close()


# ─── Main Function ───

# Known endpoints (TP-Link SG series)
SWITCH_ENDPOINTS = {
    "port": "PortStatisticsRpm.htm",
    "poe": "PoePowerRpm.htm",
    "vlan": "Vlan8021QRpm.htm",
    "system": "SystemInfoRpm.htm",
    "status": "StatusRpm.htm",
    "mirror": "PortMirrorRpm.htm",
    "trunk": "TrunkRpm.htm",
    "qos": "QoSBasicRpm.htm",
    "igmp": "IgmpSnoopingRpm.htm",
}


def execute_web_command(host: str, user: str, pwd: str, action: str) -> str:
    """
    Connects to the switch web interface via HTTP and performs the operation.

    Args:
        host: Switch IP address
        user: Web interface username
        pwd: Web interface password
        action: Operation to perform
    """
    if not host:
        return "❌ Switch IP address is not defined. Add a Switch from the Device Management page."

    logger.info(f"Switch web request: {host} | action={action}")
    audit_log(
        AuditEvent.COMMAND_EXECUTE,
        target=host,
        detail=f"Switch web: {action[:100]}",
        extra={"tool": "switch", "protocol": "HTTP"}
    )

    sw = SwitchSession(host, user, pwd)

    try:
        # Determine which endpoint to use
        action_lower = action.lower()
        endpoint = None
        
        if any(k in action_lower for k in ["port", "durum", "status", "istatistik"]):
            endpoint = SWITCH_ENDPOINTS.get("port", SWITCH_ENDPOINTS["status"])
        elif any(k in action_lower for k in ["poe", "güç", "power"]):
            endpoint = SWITCH_ENDPOINTS["poe"]
        elif any(k in action_lower for k in ["vlan", "sanal ağ"]):
            endpoint = SWITCH_ENDPOINTS["vlan"]
        elif any(k in action_lower for k in ["sistem", "system", "bilgi", "info"]):
            endpoint = SWITCH_ENDPOINTS["system"]
        elif any(k in action_lower for k in ["mirror", "ayna"]):
            endpoint = SWITCH_ENDPOINTS["mirror"]
        elif any(k in action_lower for k in ["trunk", "link aggregation"]):
            endpoint = SWITCH_ENDPOINTS["trunk"]
        elif any(k in action_lower for k in ["qos", "kalite"]):
            endpoint = SWITCH_ENDPOINTS["qos"]
        else:
            endpoint = SWITCH_ENDPOINTS["status"]

        result = sw.get_page(endpoint)

        # Extract plain text from HTML (basic parse)
        import re
        # Remove script and style tags
        clean = re.sub(r'<script[^>]*>.*?</script>', '', result, flags=re.DOTALL)
        clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.DOTALL)
        # Remove HTML tags
        clean = re.sub(r'<[^>]+>', ' ', clean)
        # Remove multiple whitespace
        clean = re.sub(r'\s+', ' ', clean).strip()

        # Truncate if too long
        if len(clean) > 3000:
            clean = clean[:3000] + "\n... (truncated)"

        if not clean or len(clean) < 20:
            clean = f"Switch [{host}] responded but content could not be parsed. Endpoint: {endpoint}"

        audit_log(
            AuditEvent.COMMAND_RESULT, target=host,
            detail=f"Switch result: {len(clean)} chars", success=True
        )
        return f"[Switch {host} → {endpoint}]\n{clean}"

    except Exception as e:
        error_msg = f"❌ Switch Error ({host}): {str(e)}"
        logger.error(error_msg)
        audit_log(AuditEvent.COMMAND_RESULT, target=host, detail=f"Switch error: {str(e)[:100]}", success=False)
        return error_msg

    finally:
        sw.close()
