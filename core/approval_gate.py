"""
System-wide approval gate.

Every destructive tool call routes through this gate before execution.
In an interactive Streamlit session the gate queues the call for human
review and returns a placeholder; the UI surfaces a popup with the full
context (tool, target, command/path, body) and Approve/Reject buttons.

In headless contexts (workflows, incident manager, MCP-driven runs) the
gate trusts the surrounding control (workflow's wait_approval step,
allow_destructive flags). It can be forced on with ENV
APPROVAL_GATE_FORCE=true if desired.

Public API:
    is_destructive(tool_name, tool_input) -> bool
    request_approval(tool_name, tool_input, ...) -> dict | None
        # None when no approval needed / not interactive
        # dict { "status": "waiting", "approval_id": "..." } when queued
        # dict { "status": "approved", ... } when already approved
        # dict { "status": "rejected", ... } when already rejected
    list_pending() -> list[dict]
    decide(approval_id, approved, note) -> dict
"""

import os
import re
import uuid
from datetime import datetime
from typing import Optional

from logging_config.logger import get_logger

logger = get_logger("approval_gate")


# ─── Destructive-action detection ────────────────────────────────────

# SSH/shell keywords. Anything that mutates state on a Linux/Windows host.
_SSH_DESTRUCTIVE = [
    "rm ", "rm-", "rmdir", "del ", "format ", "mkfs", "dd if=",
    ":>", " >", " >>",
    "reboot", "shutdown", "poweroff", "halt", "init 0", "init 6",
    "systemctl stop", "systemctl restart", "systemctl disable",
    "systemctl start", "systemctl enable", "service stop",
    "service restart", "service start",
    "kill ", "killall", "pkill", "taskkill", "stop-service", "start-service",
    "restart-service",
    "apt install", "apt remove", "apt-get install", "apt-get remove",
    "apt purge", "apt-get purge", "apt upgrade", "apt-get upgrade",
    "apt update", "apt-get update",
    "yum install", "yum remove", "yum update",
    "dnf install", "dnf remove", "dnf upgrade",
    "pip install", "pip uninstall", "npm install", "npm uninstall",
    "drop ", "truncate ", "delete from", "alter table",
    "useradd", "userdel", "usermod", "passwd ", "chpasswd",
    "iptables", "nft ", "ufw enable", "ufw disable", "ufw allow",
    "ufw deny", "firewall-cmd",
    "mount ", "umount", "swapoff", "swapon",
    "chmod 777", "chmod -r", "chown -r",
    "git push", "git reset --hard", "git clean -f",
    "mv ", "cp -r ", "rsync --delete",
    "set-itemproperty", "remove-item", "stop-computer", "restart-computer",
    "remove-aduser", "set-aduser", "new-aduser",
    "new-localuser", "remove-localuser",
    "invoke-restmethod -method delete",
    "invoke-restmethod -method put",
    "invoke-restmethod -method post",
]

# Switch / router CLI write verbs
_NETWORK_DESTRUCTIVE = [
    "configure terminal", "conf t", "no ", "shutdown", "no shutdown",
    "write memory", "write erase", "reload", "switchport",
    "vlan ", "interface ",
]

# Tool-input action keywords (commvault/custom MCPs use natural language)
_ACTION_DESTRUCTIVE = [
    "delete ", "remove ", "create ", "add ", "update ", "modify ",
    "kill ", "stop ", "restart ", "start ", "enable ", "disable ",
    "backup ", "restore ", "run backup", "run_backup", "schedule ",
    "yedekle", "geri yükle", "sil", "ekle", "oluştur", "güncelle",
    "durdur", "başlat",
]


def _normalise(s) -> str:
    return (str(s) if s is not None else "").lower()


def is_destructive(tool_name: str, tool_input: dict) -> bool:
    """
    Return True iff the call is judged to write/change state on a system.

    The check is conservative — when uncertain, we err on the side of
    asking for approval. Read-only operations (listing, status,
    search_api, list_actions) are explicitly safe-listed.
    """
    if not isinstance(tool_input, dict):
        return True

    action = _normalise(tool_input.get("action"))
    command = _normalise(tool_input.get("command"))
    method = _normalise(tool_input.get("method"))
    path = _normalise(tool_input.get("path"))

    # ── Explicit safe-list ──
    safe_actions = {"search_api", "list_actions", "list", "status",
                    "list_runs", "help"}
    if action in safe_actions or command in safe_actions:
        return False

    # workflow_ops decide actions need approval (approve/reject is a write)
    if tool_name == "workflow_ops":
        if action in ("run",):
            # run respects dry_run inside the workflow itself
            return False
        if action in ("approve", "reject", "cancel"):
            return True
        return False

    # ── HTTP raw passthrough ──
    if action == "raw" and method:
        if method in ("post", "put", "delete", "patch"):
            return True
        return False  # GET raw → read-only

    # ── Per-tool SSH/CLI text scan ──
    text = f"{command} {action}".strip()
    if not text:
        # Empty input → likely a malformed call; treat as destructive so the
        # operator sees it.
        return True

    haystack = text
    for kw in _SSH_DESTRUCTIVE:
        if kw in haystack:
            return True

    if tool_name in ("switch_ops", "router_ops"):
        for kw in _NETWORK_DESTRUCTIVE:
            if kw in haystack:
                return True

    # Natural-language action keywords (commvault, custom MCPs)
    for kw in _ACTION_DESTRUCTIVE:
        if kw in haystack:
            return True

    # Anything matching "list*" or "show*" or "get*" → likely read-only.
    if re.match(r"^(list|show|get|fetch|describe|status|view|read|info)\b", haystack):
        return False

    # Default: be cautious — unknown commands get a popup.
    # But if the only content looks like a shell read command, allow it.
    if re.match(r"^\s*(ls|cat|grep|tail|head|du|df|free|uptime|whoami|"
                r"netstat|ss |ps |top|iostat|vmstat|date|hostname|uname)\b",
                haystack):
        return False
    return True


# ─── Pending-approval store (process-local, used by Streamlit) ───────

_PENDING: dict[str, dict] = {}


def request_approval(
    tool_name: str,
    tool_input: dict,
    *,
    target: str = "",
    interactive: Optional[bool] = None,
    rationale: str = "",
) -> Optional[dict]:
    """
    Decide whether this call needs an interactive approval popup.

    Returns:
        None                          — not destructive / not interactive
        {"status": "waiting", ...}    — popup must be shown; call is queued
        {"status": "approved", ...}   — already approved (replay path)
        {"status": "rejected", ...}   — already rejected

    The decision UI is owned by the calling page; this function only
    tracks state and dispatches.
    """
    if not is_destructive(tool_name, tool_input):
        return None

    if interactive is None:
        interactive = _running_in_streamlit()

    if not interactive and not _force_enabled():
        # Headless context — workflows / scheduled / MCP all have their own
        # wait_approval / allow_destructive gating.
        return None

    # Already-decided lookup — keyed by (tool_name, action, command, path, target)
    fingerprint = _fingerprint(tool_name, tool_input, target)
    for aid, entry in list(_PENDING.items()):
        if entry["fingerprint"] != fingerprint:
            continue
        if entry["status"] in ("approved", "rejected"):
            # Consume once — clear after returning so a fresh request will
            # re-queue rather than auto-approve forever.
            decision = dict(entry)
            del _PENDING[aid]
            return decision
        # Already queued for this exact call — return existing entry
        return entry

    approval_id = uuid.uuid4().hex[:12]
    entry = {
        "approval_id": approval_id,
        "status": "waiting",
        "tool_name": tool_name,
        "tool_input": dict(tool_input or {}),
        "target": target,
        "fingerprint": fingerprint,
        "rationale": rationale,
        "created_at": datetime.now().isoformat(),
        "decided_at": None,
        "note": "",
    }
    _PENDING[approval_id] = entry

    logger.info(
        f"[approval] queued {approval_id} for tool={tool_name} "
        f"target={target or '-'}  action={tool_input.get('action', '')[:60]}"
    )
    return entry


def list_pending() -> list[dict]:
    """All pending (waiting) approvals — newest first."""
    return sorted(
        (e for e in _PENDING.values() if e["status"] == "waiting"),
        key=lambda e: e["created_at"],
        reverse=True,
    )


def get_pending(approval_id: str) -> Optional[dict]:
    return _PENDING.get(approval_id)


def decide(approval_id: str, approved: bool, note: str = "") -> Optional[dict]:
    """Approve or reject a queued call. The next request_approval() with
    the same fingerprint will see the decision and proceed."""
    entry = _PENDING.get(approval_id)
    if not entry:
        return None
    entry["status"] = "approved" if approved else "rejected"
    entry["note"] = note
    entry["decided_at"] = datetime.now().isoformat()
    logger.info(
        f"[approval] {approval_id} {entry['status']} "
        f"({entry['tool_name']} / {entry['tool_input'].get('action', '')})"
    )
    return entry


def clear_all():
    """Drop every queued/decided entry. Used by tests and 'reset' UI."""
    _PENDING.clear()


# ─── Internals ──────────────────────────────────────────────────────

def _fingerprint(tool_name: str, tool_input: dict, target: str) -> str:
    """Stable identity for a destructive call so re-requests dedupe."""
    import json
    key = {
        "t": tool_name,
        "tgt": target,
        # Order matters less than content — sort_keys for determinism
        "i": tool_input,
    }
    return json.dumps(key, sort_keys=True, default=str)[:200]


def _running_in_streamlit() -> bool:
    """True iff the current thread is inside a Streamlit script run."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except Exception:
        return False


def _force_enabled() -> bool:
    return os.getenv("APPROVAL_GATE_FORCE", "").lower() in ("true", "1", "yes")
