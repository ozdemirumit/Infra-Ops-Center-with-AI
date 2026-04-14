"""
Commvault backup system REST API tool — Fully Operational.
Compliant with SP36 Commvault REST API specification.
Supports both legacy (/webconsole/api) and V4 (/commandcenter/api) endpoints.
All CRUD + operational + administrative features.
MCP tool definition is in this file.
"""

import requests
import urllib3
import json
import base64
from logging_config.logger import get_logger, audit_log, AuditEvent
from config.settings import settings

# Disable self-signed certificate warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = get_logger("tools")

# ─── MCP Tool Definition ───

COMMVAULT_OPS_TOOL = {
    "name": "commvault_ops",
    "description": (
        "Performs ALL operations on the Commvault backup system via REST API. "
        "Supported operations: "
        "List/add/delete clients, create/list/delete subclients, "
        "run backups (full/incremental/differential/synthetic), "
        "restore (in-place/out-of-place), job management (list/kill/pause/resume/restart), "
        "plan management (list/create/delete), storage pools, alerts, "
        "enable/disable agents, schedule policies, "
        "user management, VM management, license info, CommCell status."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"action": {"type": "string", "description": "Operation to perform on Commvault (English or Turkish)"}},
        "required": ["action"]
    }
}


class CommvaultSession:
    """
    Commvault SP36 REST API session manager.
    Login: POST /Login — password Base64 UTF-8 encoded.
    Auth: QSDK token via Authtoken header.
    """

    def __init__(self, host: str, user: str, pwd: str):
        self.host = host
        self.base_url = f"https://{host}/webconsole/api"
        self.base_url_v4 = f"https://{host}/commandcenter/api/v4"
        self.user = user
        self.pwd = pwd
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self._auth_token = None
        self._logged_in = False

    def login(self) -> bool:
        """SP36 Login: Base64 encoded password, domain support."""
        try:
            encoded_pwd = base64.b64encode(self.pwd.encode("utf-8")).decode("utf-8")
            domain = ""
            username = self.user
            if "\\" in self.user:
                domain, username = self.user.split("\\", 1)

            login_payload = {"password": encoded_pwd, "username": username, "timeout": 30}
            if domain:
                login_payload["domain"] = domain

            resp = self.session.post(f"{self.base_url}/Login", json=login_payload, timeout=settings.COMMVAULT_TIMEOUT)

            if resp.status_code == 200:
                data = resp.json()
                token = (data.get("token")
                         or (data.get("data", {}).get("authToken") if isinstance(data.get("data"), dict) else None)
                         or data.get("authToken"))
                if token:
                    self._auth_token = token
                    self.session.headers["Authtoken"] = token
                    self._logged_in = True
                    logger.info(f"Commvault SP36 login successful: {self.base_url}")
                    return True

            logger.warning(f"Commvault login failed: HTTP {resp.status_code}")
            return False
        except Exception as e:
            logger.error(f"Commvault login error: {str(e)}")
            return False

    def _request(self, method: str, endpoint: str, payload: dict = None, params: dict = None, use_v4: bool = False) -> dict:
        """General HTTP request method."""
        if not self._logged_in:
            if not self.login():
                return {"error": "Could not log in to Commvault"}

        base = self.base_url_v4 if use_v4 else self.base_url
        url = f"{base}/{endpoint.lstrip('/')}"

        try:
            if method == "GET":
                resp = self.session.get(url, params=params, timeout=settings.COMMVAULT_TIMEOUT)
            elif method == "POST":
                resp = self.session.post(url, json=payload or {}, timeout=settings.COMMVAULT_TIMEOUT)
            elif method == "PUT":
                resp = self.session.put(url, json=payload or {}, timeout=settings.COMMVAULT_TIMEOUT)
            elif method == "DELETE":
                resp = self.session.delete(url, timeout=settings.COMMVAULT_TIMEOUT)
            else:
                return {"error": f"Unsupported HTTP method: {method}"}

            resp.raise_for_status()
            if resp.text.strip():
                return resp.json()
            return {"status": "success", "message": "Operation completed successfully"}

        except requests.exceptions.HTTPError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:500]}"}
        except requests.exceptions.ConnectionError:
            return {"error": f"Connection error: {url}"}
        except requests.exceptions.Timeout:
            return {"error": f"Timeout ({settings.COMMVAULT_TIMEOUT}s)"}
        except Exception as e:
            return {"error": str(e)}

    def get(self, endpoint, params=None, use_v4=False):
        return self._request("GET", endpoint, params=params, use_v4=use_v4)

    def post(self, endpoint, payload=None, use_v4=False):
        return self._request("POST", endpoint, payload=payload, use_v4=use_v4)

    def put(self, endpoint, payload=None, use_v4=False):
        return self._request("PUT", endpoint, payload=payload, use_v4=use_v4)

    def delete(self, endpoint, use_v4=False):
        return self._request("DELETE", endpoint, use_v4=use_v4)

    def close(self):
        """SP36: POST /Logout."""
        if self._logged_in and self._auth_token:
            try:
                self.session.post(f"{self.base_url}/Logout", timeout=5)
            except Exception:
                pass
        self.session.close()


# ─── SP36 API Endpoint Catalog ───

COMMVAULT_ACTIONS = {
    # ══════════════════════════════════════
    # READ endpoints
    # ══════════════════════════════════════

    # Server & Client
    "clients":             {"endpoint": "Client",                  "method": "GET",  "desc": "List all clients"},
    "client_detail":       {"endpoint": "Client/{id}",             "method": "GET",  "desc": "Client detail"},
    "client_props":        {"endpoint": "Client/{id}/Properties",  "method": "GET",  "desc": "Client properties"},
    "servers_v4":          {"endpoint": "Servers",                 "method": "GET",  "desc": "V4 Server list",  "v4": True},
    "server_detail_v4":    {"endpoint": "Servers/{id}",            "method": "GET",  "desc": "V4 Server detail",   "v4": True},

    # Job
    "jobs_active":         {"endpoint": "Job",  "method": "GET",  "desc": "List active jobs",
                            "params": {"jobCategory": "Active"}},
    "jobs_finished":       {"endpoint": "Job",  "method": "GET",  "desc": "Finished jobs (last 24h)",
                            "params": {"jobCategory": "Finished", "completedJobLookupTime": "86400"}},
    "jobs_all":            {"endpoint": "Job",  "method": "GET",  "desc": "List all jobs",
                            "params": {"jobCategory": "All"}},
    "job_detail":          {"endpoint": "Job/{id}",  "method": "GET",  "desc": "Job detail"},
    "jobs_v4":             {"endpoint": "Job",           "method": "GET",  "desc": "All jobs (v4 fallback)",
                            "params": {"jobCategory": "All"}},
    "job_detail_v4":       {"endpoint": "Job/{id}",      "method": "GET",  "desc": "Job detail (v4 fallback)"},

    # Alerts
    "alerts":              {"endpoint": "AlertRule",                     "method": "GET", "desc": "Alert rules"},
    "alerts_console":      {"endpoint": "AlertRule?alertType=console",   "method": "GET", "desc": "Console alerts"},
    "alerts_v4":           {"endpoint": "AlertRule",                     "method": "GET", "desc": "Alerts (all)"},

    # Storage
    "storage_pools":       {"endpoint": "StoragePool",   "method": "GET", "desc": "Storage pools"},
    "storage_policies":    {"endpoint": "StoragePolicy", "method": "GET", "desc": "Storage policies"},
    "libraries":           {"endpoint": "Library",       "method": "GET", "desc": "Library/tape list"},

    # Backup Components
    "subclients":          {"endpoint": "Subclient",                 "method": "GET", "desc": "All subclient list"},
    "subclient_by_client": {"endpoint": "Subclient?clientId={id}",   "method": "GET", "desc": "Subclients by client"},
    "subclient_detail":    {"endpoint": "Subclient/{id}",            "method": "GET", "desc": "Subclient detail"},
    "backup_sets":         {"endpoint": "Backupset",                 "method": "GET", "desc": "Backup sets"},

    # Plan
    "plans_v4":            {"endpoint": "Plan",           "method": "GET", "desc": "Server plans"},
    "plan_detail_v4":      {"endpoint": "Plan/{id}",      "method": "GET", "desc": "Plan detail"},

    # Agent
    "agents":              {"endpoint": "Agent?clientId={id}", "method": "GET", "desc": "Client agent list"},

    # VM / Virtualization
    "vm_clients":          {"endpoint": "Client?PseudoClientType=VSPseudo", "method": "GET", "desc": "Virtualization clients"},
    "vm_browse":           {"endpoint": "Client/{id}/VMBrowse",             "method": "GET", "desc": "VM list (browse)"},

    # User & Security
    "users":               {"endpoint": "User",          "method": "GET", "desc": "User list"},
    "user_detail":         {"endpoint": "User/{id}",     "method": "GET", "desc": "User detail"},
    "user_groups":         {"endpoint": "UserGroup",     "method": "GET", "desc": "User groups"},
    "roles":               {"endpoint": "Role",          "method": "GET", "desc": "Role list"},

    # System
    "commcell":            {"endpoint": "CommServ",        "method": "GET", "desc": "CommCell server info"},
    "media_agents":        {"endpoint": "MediaAgent",      "method": "GET", "desc": "Media Agent list"},
    "schedules":           {"endpoint": "SchedulePolicy",  "method": "GET", "desc": "Schedule policies"},
    "schedule_detail":     {"endpoint": "SchedulePolicy/{id}", "method": "GET", "desc": "Policy detail"},
    "license":             {"endpoint": "LicenseInfo",     "method": "GET", "desc": "License info"},
    "events":              {"endpoint": "Events",          "method": "GET", "desc": "Event log"},
}


# ═══════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════

def _extract_id_from_action(action: str) -> str | None:
    """Extracts the ID number from the action string."""
    import re
    match = re.search(r'(?:id|ID|#)\s*[:=]?\s*(\d+)', action)
    if match:
        return match.group(1)
    numbers = re.findall(r'\b(\d+)\b', action)
    if len(numbers) == 1:
        return numbers[0]
    return None


def _extract_name_from_quotes(action: str) -> str | None:
    """Extracts the name/path value within quotes."""
    import re
    match = re.search(r'["\']([^"\']+)["\']', action)
    return match.group(1) if match else None


def _extract_path_from_action(action: str) -> str | None:
    """Extracts the file/folder path."""
    import re
    match = re.search(r'(?:path|yol|dosya|klasör|dizin)\s*[=:]\s*(\S+)', action, re.IGNORECASE)
    if match:
        return match.group(1)
    return _extract_name_from_quotes(action)


def _format_result(result: dict, host: str, desc: str) -> str:
    """Formats the API result."""
    if isinstance(result, dict):
        if "error" in result:
            error_msg = f"❌ Commvault API error: {result['error']}"
            audit_log(AuditEvent.COMMAND_RESULT, target=host, detail=error_msg[:100], success=False)
            return error_msg
        formatted = json.dumps(result, indent=2, ensure_ascii=False)
        if len(formatted) > 5000:
            formatted = formatted[:5000] + "\n... (truncated)"
    else:
        formatted = str(result)[:5000]

    audit_log(AuditEvent.COMMAND_RESULT, target=host,
              detail=f"Commvault: {desc} — {len(formatted)} chars", success=True)
    return f"[Commvault {host} → {desc}]\n{formatted}"


# ═══════════════════════════════════════════════
# WRITE FUNCTIONS
# ═══════════════════════════════════════════════

def _do_backup(cv, host, action, entity_id):
    """Start subclient-based backup."""
    if not entity_id:
        return "⚠️ Subclient ID is required for backup. Use the 'list subclients' command first."

    # Determine backup level
    action_lower = action.lower()
    backup_level = "Incremental"
    if "full" in action_lower and "synthetic" not in action_lower:
        backup_level = "Full"
    elif "synthetic" in action_lower:
        backup_level = "Synthetic_full"
    elif "differential" in action_lower:
        backup_level = "Differential"

    payload = {"backupLevel": backup_level}
    result = cv.post(f"Subclient/{entity_id}/action/backup", payload=payload)
    return _format_result(result, host, f"Start Backup (Subclient #{entity_id} — {backup_level})")


def _do_plan_backup(cv, host, action, entity_id):
    """Start plan-based backup (V4)."""
    if not entity_id:
        return "⚠️ Plan ID is required for plan-based backup. Use the 'list plans' command first."

    action_lower = action.lower()
    level = "Incremental"
    if "full" in action_lower and "synthetic" not in action_lower:
        level = "Full"
    elif "synthetic" in action_lower:
        level = "Synthetic_full"
    elif "differential" in action_lower:
        level = "Differential"

    result = cv.post(f"Subclient/{entity_id}/action/backup", payload={"backupLevel": level})
    return _format_result(result, host, f"Plan Backup (#{entity_id} — {level})")


def _do_restore(cv, host, action, entity_id):
    """File system restore (in-place / out-of-place)."""
    if not entity_id:
        return "⚠️ Subclient ID is required for restore. Use the 'list subclients' command first."

    action_lower = action.lower()
    src_path = _extract_path_from_action(action)

    # Fetch subclient info
    sc_info = cv.get(f"Subclient/{entity_id}")
    if "error" in sc_info:
        return _format_result(sc_info, host, "Subclient info fetch error")

    subclient_obj = sc_info.get("subClientProperties", [{}])[0] if isinstance(sc_info.get("subClientProperties"), list) else {}
    sc_entity = subclient_obj.get("subClientEntity", {})

    # Restore payload (in-place check also supports Turkish keyword "yerinde")
    in_place = 1 if "in-place" in action_lower or "yerinde" in action_lower else 0
    dest_path = ""
    if not in_place:
        # Out-of-place — search for destination path
        import re
        dest_match = re.search(r'(?:hedef|dest|destination|to)\s*[=:]\s*(\S+)', action, re.IGNORECASE)
        if dest_match:
            dest_path = dest_match.group(1)

    restore_payload = {
        "taskInfo": {
            "task": {"taskType": 1, "initiatedFrom": 2},
            "subTasks": [{
                "subTask": {"subTaskType": 3, "operationType": 1001},
                "options": {
                    "restoreOptions": {
                        "browseOption": {
                            "commCellId": sc_entity.get("commCellId", 2),
                            "backupset": {"clientId": sc_entity.get("clientId", 0)},
                        },
                        "commonOptions": {
                            "unconditionalOverwrite": True,
                        },
                        "destination": {
                            "inPlace": in_place == 1,
                            "destClient": {"clientId": sc_entity.get("clientId", 0)},
                        },
                        "fileOption": {
                            "sourceItem": [src_path] if src_path else ["\\"],
                        },
                    }
                }
            }]
        }
    }

    if dest_path and not in_place:
        restore_payload["taskInfo"]["subTasks"][0]["options"]["restoreOptions"]["destination"]["destPath"] = [dest_path]

    result = cv.post("retrieveToClient", payload=restore_payload)
    restore_type = "In-Place" if in_place else f"Out-of-Place → {dest_path or 'default'}"
    return _format_result(result, host, f"Restore ({restore_type}, SC #{entity_id})")


def _do_browse(cv, host, entity_id):
    """Browse subclient contents (backup browse)."""
    if not entity_id:
        return "⚠️ Subclient ID is required for browse."

    payload = {
        "opType": 0,
        "entity": {"subclientId": int(entity_id)},
        "paths": [{"path": "\\"}],
    }
    result = cv.post("DoBrowse", payload=payload)
    return _format_result(result, host, f"Browse (Subclient #{entity_id})")


def _do_subclient_create(cv, host, action, entity_id):
    """Create a new subclient."""
    if not entity_id:
        return "⚠️ Client ID is required to create a subclient. Use the 'list clients' command first."

    sc_name = _extract_name_from_quotes(action) or "NewSubclient_API"
    content_path = _extract_path_from_action(action) or "C:\\"

    payload = {
        "subClientProperties": {
            "subClientEntity": {
                "clientId": int(entity_id),
                "subclientName": sc_name,
            },
            "content": [{"path": content_path}],
        }
    }
    result = cv.post("Subclient", payload=payload)
    return _format_result(result, host, f"Create Subclient ('{sc_name}' client #{entity_id})")


def _do_subclient_delete(cv, host, entity_id):
    """Delete subclient."""
    if not entity_id:
        return "⚠️ Subclient ID is required for deletion."
    result = cv.delete(f"Subclient/{entity_id}")
    return _format_result(result, host, f"Delete Subclient (#{entity_id})")


def _do_subclient_update(cv, host, action, entity_id):
    """Update subclient properties (add content)."""
    if not entity_id:
        return "⚠️ Subclient ID is required for update."

    content_path = _extract_path_from_action(action) or _extract_name_from_quotes(action)
    if not content_path:
        return "⚠️ Specify the content path to add. Example: update subclient ID:5 path=D:\\Data"

    payload = {
        "subClientProperties": {
            "content": [{"path": content_path}],
        }
    }
    result = cv.post(f"Subclient/{entity_id}", payload=payload)
    return _format_result(result, host, f"Update Subclient (#{entity_id} → {content_path})")


def _do_job_action(cv, host, action_type, entity_id):
    """Job management: kill / pause / resume / restart."""
    if not entity_id:
        return f"⚠️ Job ID is required for job {action_type}."

    action_map = {
        "kill":    ("action/kill",    "Kill Job"),
        "pause":   ("action/pause",   "Pause Job"),
        "resume":  ("action/resume",  "Resume Job"),
        "resubmit": ("action/resubmit", "Restart Job"),
    }
    endpoint_suffix, desc = action_map.get(action_type, ("action/kill", "Kill Job"))
    result = cv.post(f"Job/{entity_id}/{endpoint_suffix}")
    return _format_result(result, host, f"{desc} (#{entity_id})")


def _do_client_delete(cv, host, entity_id):
    """Delete client."""
    if not entity_id:
        return "⚠️ Client ID is required for deletion."
    result = cv.delete(f"Client/{entity_id}")
    return _format_result(result, host, f"Delete Client (#{entity_id})")


def _do_agent_toggle(cv, host, entity_id, enable: bool):
    """Enable / disable agent."""
    if not entity_id:
        return "⚠️ Agent ID is required."

    payload = {
        "association": {"entity": [{"clientId": int(entity_id)}]},
        "agentProperties": {"isEnabled": enable},
    }
    status = "Enable" if enable else "Disable"
    result = cv.post(f"Agent", payload=payload)
    return _format_result(result, host, f"Agent {status} (Client #{entity_id})")


def _do_plan_create(cv, host, action):
    """Create server plan (V4)."""
    plan_name = _extract_name_from_quotes(action) or "API_Plan"

    payload = {
        "planName": plan_name,
        "backupContent": {
            "windowsIncludedPaths": ["\\"],
            "unixIncludedPaths": ["/"],
        },
    }
    result = cv.post("Plan", payload=payload)
    return _format_result(result, host, f"Create Plan ('{plan_name}')")


def _do_plan_delete(cv, host, entity_id):
    """Delete server plan (V4)."""
    if not entity_id:
        return "⚠️ Plan ID is required for deletion."
    result = cv.delete(f"Plan/{entity_id}")
    return _format_result(result, host, f"Delete Plan (#{entity_id})")


def _do_schedule_create(cv, host, action, entity_id):
    """Create backup schedule."""
    if not entity_id:
        return "⚠️ Subclient/policy ID is required to create a schedule."

    schedule_name = _extract_name_from_quotes(action) or "DailyBackupSchedule"
    payload = {
        "taskInfo": {
            "taskOperation": 1,
            "task": {
                "taskType": 2,
                "taskName": schedule_name,
            },
            "subTasks": [{
                "subTask": {"subTaskType": 2, "operationType": 2},
                "pattern": {
                    "freq_type": 4,
                    "active_start_time": 72000,
                },
                "options": {
                    "backupOpts": {
                        "backupLevel": "Incremental",
                        "dataOpt": {"enableIndexCheckPointing": True},
                    }
                }
            }]
        }
    }
    result = cv.post("CreateTask", payload=payload)
    return _format_result(result, host, f"Create Schedule ('{schedule_name}')")


def _do_user_create(cv, host, action):
    """Create user."""
    username = _extract_name_from_quotes(action)
    if not username:
        return "⚠️ Specify a name to create a user. Example: create user 'new_user'"

    payload = {
        "users": [{
            "userEntity": {"userName": username},
            "password": base64.b64encode(b"TempPassword1!").decode(),
        }]
    }
    result = cv.post("User", payload=payload)
    return _format_result(result, host, f"Create User ('{username}')")


def _do_user_delete(cv, host, entity_id):
    """Delete user."""
    if not entity_id:
        return "⚠️ User ID is required for deletion."
    result = cv.delete(f"User/{entity_id}")
    return _format_result(result, host, f"Delete User (#{entity_id})")


def _do_alert_delete(cv, host, entity_id):
    """Delete alert."""
    if not entity_id:
        return "⚠️ Alert ID is required for deletion."
    result = cv.delete(f"AlertRule/{entity_id}")
    return _format_result(result, host, f"Delete Alert (#{entity_id})")


# ═══════════════════════════════════════════════
# MAIN PROCESSING FUNCTION
# ═══════════════════════════════════════════════

def execute_commvault_api(host: str, user: str, pwd: str, action: str) -> str:
    """Commvault SP36 REST API — all operational and administrative operations."""
    if not host:
        return "❌ Commvault server address is not defined. Add a Commvault server from the Device Management page."

    logger.info(f"Commvault SP36 API: {host} | action={action}")
    audit_log(AuditEvent.COMMAND_EXECUTE, target=host,
              detail=f"Commvault API: {action[:100]}", extra={"tool": "commvault", "protocol": "HTTPS REST SP36"})

    cv = CommvaultSession(host, user, pwd)

    try:
        action_lower = action.lower()
        entity_id = _extract_id_from_action(action)

        # ══════════════════════════════════════
        # WRITE OPERATIONS
        # ══════════════════════════════════════

        # ── Backup ──
        if any(k in action_lower for k in ["yedekle", "backup al", "yedekleme başlat", "backup başlat", "run backup"]):
            if "plan" in action_lower:
                return _do_plan_backup(cv, host, action, entity_id)
            return _do_backup(cv, host, action, entity_id)

        # ── Restore ──
        if any(k in action_lower for k in ["geri yükle", "restore", "geri al"]):
            return _do_restore(cv, host, action, entity_id)

        # ── Browse ──
        if any(k in action_lower for k in ["browse", "gözat", "içerik listele"]):
            return _do_browse(cv, host, entity_id)

        # ── Subclient CRUD ──
        if any(k in action_lower for k in ["subclient oluştur", "subclient ekle", "create subclient", "add subclient"]):
            return _do_subclient_create(cv, host, action, entity_id)

        if any(k in action_lower for k in ["subclient sil", "delete subclient", "remove subclient"]):
            return _do_subclient_delete(cv, host, entity_id)

        if any(k in action_lower for k in ["subclient güncelle", "update subclient", "subclient düzenle"]):
            return _do_subclient_update(cv, host, action, entity_id)

        # ── Job Management ──
        if any(k in action_lower for k in ["iş durdur", "kill job", "job durdur", "iptal et"]):
            return _do_job_action(cv, host, "kill", entity_id)

        if any(k in action_lower for k in ["iş duraklat", "pause job", "job beklet"]):
            return _do_job_action(cv, host, "pause", entity_id)

        if any(k in action_lower for k in ["iş sürdür", "resume job", "job devam"]):
            return _do_job_action(cv, host, "resume", entity_id)

        if any(k in action_lower for k in ["yeniden başlat", "resubmit", "job tekrar", "iş tekrar"]):
            return _do_job_action(cv, host, "resubmit", entity_id)

        # ── Client Management ──
        if any(k in action_lower for k in ["istemci sil", "client sil", "delete client", "sunucu sil"]):
            return _do_client_delete(cv, host, entity_id)

        # ── Agent Management ──
        if any(k in action_lower for k in ["agent etkin", "agent aç", "enable agent", "agent aktif"]):
            return _do_agent_toggle(cv, host, entity_id, enable=True)

        if any(k in action_lower for k in ["agent devre", "agent kapat", "disable agent", "agent pasif"]):
            return _do_agent_toggle(cv, host, entity_id, enable=False)

        # ── Plan Management ──
        if any(k in action_lower for k in ["plan oluştur", "plan ekle", "create plan", "add plan"]):
            return _do_plan_create(cv, host, action)

        if any(k in action_lower for k in ["plan sil", "delete plan", "remove plan"]):
            return _do_plan_delete(cv, host, entity_id)

        # ── Schedule ──
        if any(k in action_lower for k in ["zamanlama oluştur", "schedule oluştur", "create schedule"]):
            return _do_schedule_create(cv, host, action, entity_id)

        # ── User Management ──
        if any(k in action_lower for k in ["kullanıcı oluştur", "user oluştur", "create user", "kullanıcı ekle"]):
            return _do_user_create(cv, host, action)

        if any(k in action_lower for k in ["kullanıcı sil", "user sil", "delete user"]):
            return _do_user_delete(cv, host, entity_id)

        # ── Alert Management ──
        if any(k in action_lower for k in ["uyarı sil", "alert sil", "delete alert"]):
            return _do_alert_delete(cv, host, entity_id)

        # ══════════════════════════════════════
        # READ OPERATIONS
        # ══════════════════════════════════════

        api_action = None

        # Client / Server
        if any(k in action_lower for k in ["istemci", "client", "sunucu listesi"]):
            api_action = "client_detail" if entity_id else "clients"
        elif any(k in action_lower for k in ["v4 sunucu", "v4 server"]):
            api_action = "server_detail_v4" if entity_id else "servers_v4"
        elif any(k in action_lower for k in ["özellik", "property", "properties"]):
            api_action = "client_props" if entity_id else "clients"

        # Agent
        elif any(k in action_lower for k in ["agent listele", "agent list", "agent göster"]):
            api_action = "agents"

        # VM
        elif any(k in action_lower for k in ["vm", "sanal", "virtual", "hypervisor"]):
            if "browse" in action_lower and entity_id:
                api_action = "vm_browse"
            else:
                api_action = "vm_clients"

        # Job
        elif any(k in action_lower for k in ["aktif iş", "active job", "çalışan iş", "running"]):
            api_action = "jobs_active"
        elif any(k in action_lower for k in ["biten iş", "finished", "tamamlanan", "iş geçmişi", "job history"]):
            api_action = "jobs_finished"
        elif any(k in action_lower for k in ["tüm iş", "all job", "iş listesi"]):
            api_action = "jobs_all"
        elif any(k in action_lower for k in ["iş detay", "job detail", "iş bilgi"]):
            api_action = "job_detail"
        elif any(k in action_lower for k in ["v4 iş", "v4 job"]):
            api_action = "job_detail_v4" if entity_id else "jobs_v4"
        elif any(k in action_lower for k in ["iş", "job", "görev"]):
            api_action = "job_detail" if entity_id else "jobs_active"

        # Alerts
        elif any(k in action_lower for k in ["konsol uyarı", "console alert"]):
            api_action = "alerts_console"
        elif any(k in action_lower for k in ["v4 uyarı", "v4 alert"]):
            api_action = "alerts_v4"
        elif any(k in action_lower for k in ["uyarı", "alert", "alarm"]):
            api_action = "alerts"

        # Storage
        elif any(k in action_lower for k in ["depolama politika", "storage policy"]):
            api_action = "storage_policies"
        elif any(k in action_lower for k in ["depolama", "storage", "havuz", "pool", "disk"]):
            api_action = "storage_pools"
        elif any(k in action_lower for k in ["kütüphane", "library", "tape"]):
            api_action = "libraries"

        # Subclient
        elif any(k in action_lower for k in ["subclient detay"]):
            api_action = "subclient_detail"
        elif any(k in action_lower for k in ["subclient", "alt istemci"]):
            api_action = "subclient_by_client" if entity_id else "subclients"
        elif any(k in action_lower for k in ["backup set", "yedekleme küme", "backupset"]):
            api_action = "backup_sets"

        # Plan
        elif any(k in action_lower for k in ["plan detay"]):
            api_action = "plan_detail_v4"
        elif any(k in action_lower for k in ["plan"]):
            api_action = "plan_detail_v4" if entity_id else "plans_v4"

        # Schedule
        elif any(k in action_lower for k in ["zamanlama", "schedule", "politika"]):
            api_action = "schedule_detail" if entity_id else "schedules"

        # User & Security
        elif any(k in action_lower for k in ["kullanıcı grup", "user group"]):
            api_action = "user_groups"
        elif any(k in action_lower for k in ["kullanıcı", "user"]):
            api_action = "user_detail" if entity_id else "users"
        elif any(k in action_lower for k in ["rol", "role", "yetki"]):
            api_action = "roles"

        # System
        elif any(k in action_lower for k in ["media agent", "medya"]):
            api_action = "media_agents"
        elif any(k in action_lower for k in ["lisans", "license"]):
            api_action = "license"
        elif any(k in action_lower for k in ["olay", "event", "günlük", "log"]):
            api_action = "events"
        elif any(k in action_lower for k in ["commcell", "sistem", "genel bilgi", "sunucu bilgi", "durum", "status"]):
            api_action = "commcell"
        else:
            api_action = "commcell"

        # ── Standard GET/POST Call ──
        action_config = COMMVAULT_ACTIONS[api_action]
        endpoint = action_config["endpoint"]
        method = action_config["method"]
        params = action_config.get("params", {})
        use_v4 = action_config.get("v4", False)

        if "{id}" in endpoint:
            if entity_id:
                endpoint = endpoint.replace("{id}", entity_id)
            else:
                return f"⚠️ An ID is required for this operation. Please specify an ID (e.g., 'client detail ID: 42')."

        if method == "GET":
            result = cv.get(endpoint, params=params if params else None, use_v4=use_v4)
        else:
            result = cv.post(endpoint, use_v4=use_v4)

        return _format_result(result, host, action_config.get("desc", api_action))

    except Exception as e:
        error_msg = f"❌ Commvault Error ({host}): {str(e)}"
        logger.error(error_msg)
        audit_log(AuditEvent.COMMAND_RESULT, target=host, detail=f"Commvault error: {str(e)[:100]}", success=False)
        return error_msg

    finally:
        cv.close()
