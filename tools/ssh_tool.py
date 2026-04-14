"""
SSH command execution tool.
Standard SSH connection for Linux, ESXi, and Router.
MCP tool definition is in this file.
"""

import paramiko
from config.settings import settings
from logging_config.logger import get_logger, audit_log, AuditEvent

logger = get_logger("tools")

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

def execute_ssh_command(host: str, user: str, pwd: str, command: str) -> str:
    """
    Connects to a remote server via SSH and executes the command.
    """
    logger.info(f"SSH connection starting: {host} (user={user})")
    audit_log(
        AuditEvent.COMMAND_EXECUTE,
        target=host,
        detail=f"SSH command: {command[:100]}",
        extra={"tool": "ssh", "user_host": user}
    )

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            host, username=user, password=pwd,
            timeout=settings.SSH_TIMEOUT,
            banner_timeout=settings.SSH_BANNER_TIMEOUT
        )
        stdin, stdout, stderr = ssh.exec_command(command, timeout=settings.SSH_EXEC_TIMEOUT)

        output = stdout.read().decode("utf-8")
        error = stderr.read().decode("utf-8")
        ssh.close()

        result = (output + error)[:settings.SSH_OUTPUT_LIMIT]
        final = result.strip() if result.strip() else "✅ SSH operation successful, output is empty."

        logger.info(f"SSH successful: {host} | output={len(final)} chars")
        audit_log(
            AuditEvent.COMMAND_RESULT,
            target=host,
            detail=f"SSH result: {len(final)} chars",
            success=True,
            extra={"output_length": len(final)}
        )
        return final

    except Exception as e:
        error_msg = f"❌ SSH Error ({host}): {str(e)}"
        logger.error(error_msg)
        audit_log(
            AuditEvent.COMMAND_RESULT,
            target=host,
            detail=f"SSH error: {str(e)[:100]}",
            success=False
        )
        return error_msg
