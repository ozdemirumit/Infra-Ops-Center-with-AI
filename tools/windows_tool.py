"""
Windows Server command execution tool.
Connects to remote Windows servers via OpenSSH (subprocess) or Paramiko fallback.
MCP tool definition is in this file.
"""

import subprocess
import paramiko
from config.settings import settings
from logging_config.logger import get_logger, audit_log, AuditEvent

logger = get_logger("tools")

# ─── MCP Tool Definition ───

WINDOWS_OPS_TOOL = {
    "name": "windows_ops",
    "description": (
        "Runs PowerShell commands on registered Windows servers (SSH/OpenSSH). "
        "If 'target_host' is specified, runs ONLY on that server (IP or hostname). "
        "If not specified, runs on ALL registered Windows servers. "
        "If the command is specific to a certain server, always specify 'target_host'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "PowerShell command to run"
            },
            "target_host": {
                "type": "string",
                "description": (
                    "Target server IP address or hostname. "
                    "If not specified, the command runs on ALL Windows servers simultaneously."
                )
            }
        },
        "required": ["command"]
    }
}


# ─── Command Execution via OpenSSH (Key-based auth) ───

def _execute_via_openssh(host: str, user: str, command: str) -> str:
    """
    Runs commands using Windows' built-in OpenSSH client (ssh.exe).
    Requires SSH key-based authentication.
    """
    ssh_command = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=NUL",
        "-o", f"ConnectTimeout={settings.SSH_TIMEOUT}",
        f"{user}@{host}",
        command
    ]

    result = subprocess.run(
        ssh_command,
        capture_output=True,
        text=True,
        timeout=settings.SSH_EXEC_TIMEOUT,
        stdin=subprocess.DEVNULL
    )

    # If return code is not 0, throw exception to fall back to Paramiko
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, ssh_command,
            output=result.stdout, stderr=result.stderr
        )

    output = result.stdout + result.stderr
    return output[:settings.SSH_OUTPUT_LIMIT]


# ─── Command Execution via Paramiko (Password auth) ───

def _execute_via_paramiko(host: str, user: str, pwd: str, command: str) -> str:
    """
    Runs commands using the Paramiko SSH library.
    Supports password-based authentication.
    """
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

    return (output + error)[:settings.SSH_OUTPUT_LIMIT]


# ─── Main Function ───

def execute_windows_command(host: str, user: str, pwd: str, command: str) -> str:
    """
    Connects to a remote Windows server via SSH and runs a PowerShell command.
    Tries OpenSSH (key-based) first, falls back to Paramiko on failure.

    Args:
        host: Target Windows server IP address
        user: SSH username
        pwd: SSH password (for Paramiko fallback)
        command: PowerShell command to run

    Returns:
        Command output (string)
    """
    logger.info(f"Windows SSH connection starting: {host} (user={user})")
    audit_log(
        AuditEvent.COMMAND_EXECUTE,
        target=host,
        detail=f"Windows SSH command: {command[:100]}",
        extra={"tool": "windows_ops", "user_host": user}
    )

    try:
        # Try native OpenSSH first (key-based auth)
        try:
            result = _execute_via_openssh(host, user, command)
            logger.info(f"OpenSSH successful: {host}")
        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.CalledProcessError) as ssh_err:
            logger.warning(f"OpenSSH failed ({host}), falling back to Paramiko: {ssh_err}")
            result = _execute_via_paramiko(host, user, pwd, command)
            logger.info(f"Paramiko successful: {host}")

        final = result.strip() if result.strip() else "✅ Windows SSH operation successful, output is empty."

        logger.info(f"Windows SSH successful: {host} | output={len(final)} chars")
        audit_log(
            AuditEvent.COMMAND_RESULT,
            target=host,
            detail=f"Windows SSH result: {len(final)} chars",
            success=True,
            extra={"output_length": len(final)}
        )
        return final

    except Exception as e:
        error_msg = f"❌ Windows SSH Error ({host}): {str(e)}"
        logger.error(error_msg)
        audit_log(
            AuditEvent.COMMAND_RESULT,
            target=host,
            detail=f"Windows SSH error: {str(e)[:100]}",
            success=False
        )
        return error_msg
