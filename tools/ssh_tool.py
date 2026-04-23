"""
SSH command execution tool.
Standard SSH connection for Linux, ESXi, and Router.
MCP tool definition is in this file.

Includes a connection pool that reuses SSH sessions for up to POOL_TTL seconds
to eliminate the 3-5 second handshake overhead on consecutive commands.
"""

import paramiko
import threading
import time
from config.settings import settings
from logging_config.logger import get_logger, audit_log, AuditEvent

logger = get_logger("tools")

# ─── SSH Connection Pool ───
# Reuses active SSH sessions per (host, user) for POOL_TTL seconds.
POOL_TTL = 300  # 5 minutes — drop idle connections
_pool_lock = threading.Lock()
_pool: dict = {}  # (host, user) -> {client, password_hash, last_used}


def _pool_key(host: str, user: str) -> tuple:
    return (host, user)


def _pool_get(host: str, user: str, pwd: str):
    """Returns an alive SSHClient if pooled, else None."""
    key = _pool_key(host, user)
    with _pool_lock:
        entry = _pool.get(key)
        if not entry:
            return None
        # Check TTL
        if time.time() - entry["last_used"] > POOL_TTL:
            try:
                entry["client"].close()
            except Exception:
                pass
            _pool.pop(key, None)
            return None
        # Check password hasn't changed
        pwd_hash = hash(pwd)
        if entry["pwd_hash"] != pwd_hash:
            try:
                entry["client"].close()
            except Exception:
                pass
            _pool.pop(key, None)
            return None
        # Probe: is transport still active?
        try:
            transport = entry["client"].get_transport()
            if transport is None or not transport.is_active():
                _pool.pop(key, None)
                return None
        except Exception:
            _pool.pop(key, None)
            return None
        entry["last_used"] = time.time()
        return entry["client"]


def _pool_put(host: str, user: str, pwd: str, client):
    """Store a fresh SSH client in the pool."""
    key = _pool_key(host, user)
    with _pool_lock:
        _pool[key] = {
            "client": client,
            "pwd_hash": hash(pwd),
            "last_used": time.time(),
        }


def _pool_evict(host: str, user: str):
    """Force-evict a pool entry (e.g. after error)."""
    key = _pool_key(host, user)
    with _pool_lock:
        entry = _pool.pop(key, None)
        if entry:
            try:
                entry["client"].close()
            except Exception:
                pass


def close_all_ssh_connections():
    """Close all pooled SSH connections — call on app shutdown."""
    with _pool_lock:
        for entry in _pool.values():
            try:
                entry["client"].close()
            except Exception:
                pass
        _pool.clear()

# ─── MCP Tool Definitions ───

LINUX_OPS_TOOL = {
    "name": "linux_ops",
    "description": (
        "Runs SSH (Bash) commands on registered Linux (Ubuntu) servers. "
        "If 'target_host' is specified, runs ONLY on that server (IP or hostname). "
        "If not specified, runs on ALL registered Linux servers. "
        "If the command is specific to a certain server, always specify 'target_host'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Linux Bash command to run"
            },
            "target_host": {
                "type": "string",
                "description": (
                    "Target server IP address or hostname. "
                    "If not specified, the command runs on ALL Linux servers simultaneously."
                )
            }
        },
        "required": ["command"]
    }
}

ESXI_OPS_TOOL = {
    "name": "esxi_ops",
    "description": "Runs SSH (esxcli) commands on VMware ESXi.",
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string", "description": "ESXi command to run"}},
        "required": ["command"]
    }
}

ROUTER_OPS_TOOL = {
    "name": "router_ops",
    "description": "Performs SSH network analysis (ping, route) on TP-Link ER605 Router.",
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string", "description": "Router command to run"}},
        "required": ["command"]
    }
}


# ─── SSH Command Execution ───

def _friendly_ssh_error(exc: Exception, host: str) -> str:
    """Convert paramiko/socket exceptions to actionable error messages."""
    import socket
    msg = str(exc).lower()
    exc_name = type(exc).__name__

    if isinstance(exc, socket.timeout) or "timed out" in msg:
        return (f"❌ SSH timeout connecting to {host} (waited {settings.SSH_TIMEOUT}s). "
                f"Check: (1) server is reachable via `ping {host}`, "
                f"(2) SSH port 22 is open, (3) firewall rules.")
    if "authentication failed" in msg or "auth" in msg:
        return (f"❌ SSH authentication failed for {host}. "
                "Check username/password or update device credentials.")
    if "connection refused" in msg:
        return (f"❌ SSH connection refused by {host}. "
                "Verify SSH daemon is running (`systemctl status ssh`) and port 22 is exposed.")
    if "no route to host" in msg or "network is unreachable" in msg:
        return f"❌ Network unreachable — cannot route to {host}. Check VPN/gateway."
    if "host key" in msg:
        return (f"❌ SSH host key verification failed for {host}. "
                "The server's key may have changed. Remove old key from known_hosts.")
    if isinstance(exc, ConnectionResetError):
        return f"❌ SSH connection reset by {host}. Server may be restarting or overloaded."
    return f"❌ SSH error ({exc_name}) on {host}: {str(exc)[:200]}"


def execute_ssh_command(host: str, user: str, pwd: str, command: str) -> str:
    """
    Connects to a remote server via SSH and executes the command.
    Uses a connection pool to reuse sessions; falls back to new connection on miss/failure.
    Sensitive password is scrubbed from local scope after use.
    """
    logger.info(f"SSH exec: {host} (user={user})")
    audit_log(
        AuditEvent.COMMAND_EXECUTE,
        target=host,
        detail=f"SSH command: {command[:100]}",
        extra={"tool": "ssh", "user_host": user}
    )

    ssh = None
    from_pool = False
    try:
        # Try pool first
        ssh = _pool_get(host, user, pwd)
        if ssh is not None:
            from_pool = True
            logger.debug(f"SSH pool hit: {host}")
        else:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                host, username=user, password=pwd,
                timeout=settings.SSH_TIMEOUT,
                banner_timeout=settings.SSH_BANNER_TIMEOUT,
                allow_agent=False,
                look_for_keys=False,
            )
            _pool_put(host, user, pwd, ssh)

        # Clear password from local scope immediately after auth
        pwd = None

        stdin, stdout, stderr = ssh.exec_command(command, timeout=settings.SSH_EXEC_TIMEOUT)
        output = stdout.read().decode("utf-8", errors="replace")
        error = stderr.read().decode("utf-8", errors="replace")

        result = (output + error)[:settings.SSH_OUTPUT_LIMIT]
        final = result.strip() if result.strip() else "✅ SSH operation successful, output is empty."

        logger.info(f"SSH ok: {host} | pool={'hit' if from_pool else 'new'} | {len(final)} chars")
        audit_log(
            AuditEvent.COMMAND_RESULT,
            target=host,
            detail=f"SSH result: {len(final)} chars",
            success=True,
            extra={"output_length": len(final), "from_pool": from_pool}
        )
        return final

    except Exception as e:
        # On error, evict from pool (it might be stale)
        _pool_evict(host, user)
        error_msg = _friendly_ssh_error(e, host)
        logger.error(error_msg)
        audit_log(
            AuditEvent.COMMAND_RESULT,
            target=host,
            detail=f"SSH error: {str(e)[:100]}",
            success=False
        )
        return error_msg

    finally:
        # Clear password reference
        pwd = None
        # Do NOT close ssh here — pool keeps it alive
