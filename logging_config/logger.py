"""
Central logging module.
Syslog (RFC 5424) standard formatting.
Audit trail recording all operations.
"""

import logging
import logging.handlers
import os
import json
from datetime import datetime, timezone
from pathlib import Path
from enum import Enum

# Log directory
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Log files
APP_LOG_FILE = LOG_DIR / "app.log"
AUDIT_LOG_FILE = LOG_DIR / "audit.log"
PROXY_LOG_FILE = LOG_DIR / "proxy.log"
TOOLS_LOG_FILE = LOG_DIR / "tools.log"


# ─── Syslog Formatter (RFC 5424 compatible) ───

class SyslogFormatter(logging.Formatter):
    """
    Syslog standard compliant log format.
    Format: <timestamp> <hostname> <app_name> <severity> <msg_id> - <message>
    """

    FACILITY = "local0"
    APP_NAME = "ai-ops-center"
    HOSTNAME = os.environ.get("COMPUTERNAME", "localhost")

    SEVERITY_MAP = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERR",
        logging.CRITICAL: "CRIT",
    }

    def format(self, record):
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        severity = self.SEVERITY_MAP.get(record.levelno, "INFO")
        module = record.name or "-"
        msg_id = getattr(record, "msg_id", "-")

        # Structured data
        structured = ""
        extra_data = getattr(record, "extra_data", None)
        if extra_data:
            structured = f" [{json.dumps(extra_data, ensure_ascii=False)}]"

        message = record.getMessage()
        if record.exc_info:
            message += f"\n{self.formatException(record.exc_info)}"

        return (
            f"{timestamp} {self.HOSTNAME} {self.APP_NAME} "
            f"{severity} {module} {msg_id}{structured} - {message}"
        )


# ─── Logger Factory ───

def _create_handler(filepath, max_bytes=5_000_000, backup_count=5):
    """Creates a RotatingFileHandler (max 5MB, 5 backups)."""
    handler = logging.handlers.RotatingFileHandler(
        filepath, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    handler.setFormatter(SyslogFormatter())
    return handler


def _create_console_handler():
    """Console handler (for development)."""
    handler = logging.StreamHandler()
    handler.setFormatter(SyslogFormatter())
    handler.setLevel(logging.WARNING)
    return handler


# Create main loggers once
_loggers = {}


def get_logger(name: str) -> logging.Logger:
    """
    Returns a logger by module name.
    Each module writes to its own log file and to app.log.

    Args:
        name: Logger name ("proxy", "tools", "auth", "core", "app")

    Returns:
        Configured logger
    """
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(f"ai_ops.{name}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # Always write to app.log
    logger.addHandler(_create_handler(APP_LOG_FILE))

    # Module-specific log file
    if name == "proxy":
        logger.addHandler(_create_handler(PROXY_LOG_FILE))
    elif name in ("tools", "ssh", "switch", "deco"):
        logger.addHandler(_create_handler(TOOLS_LOG_FILE))

    # Console (WARNING and above only)
    logger.addHandler(_create_console_handler())

    _loggers[name] = logger
    return logger


# ─── Audit Logger ───

class AuditEvent(str, Enum):
    """Audit event types."""
    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGIN_FAILURE = "LOGIN_FAILURE"
    LOGOUT = "LOGOUT"
    COMMAND_EXECUTE = "COMMAND_EXECUTE"
    COMMAND_RESULT = "COMMAND_RESULT"
    AI_REQUEST = "AI_REQUEST"
    AI_RESPONSE = "AI_RESPONSE"
    DEVICE_ADD = "DEVICE_ADD"
    DEVICE_UPDATE = "DEVICE_UPDATE"
    DEVICE_DELETE = "DEVICE_DELETE"
    DATA_FILTERED = "DATA_FILTERED"
    CHAT_CLEAR = "CHAT_CLEAR"
    ERROR = "ERROR"


# Audit logger writes to a separate file
_audit_logger = logging.getLogger("ai_ops.audit")
_audit_logger.setLevel(logging.INFO)
_audit_logger.propagate = False
_audit_logger.addHandler(_create_handler(AUDIT_LOG_FILE, max_bytes=10_000_000, backup_count=10))
_audit_logger.addHandler(_create_handler(APP_LOG_FILE))


def audit_log(
    event: AuditEvent,
    user: str = "-",
    target: str = "-",
    detail: str = "",
    success: bool = True,
    extra: dict = None,
):
    """
    Creates an audit log entry.

    Args:
        event: Event type (AuditEvent enum)
        user: User performing the action
        target: Target (device IP, tool name, etc.)
        detail: Description
        success: Was the operation successful?
        extra: Additional structured data

    Example output:
        2026-03-01T16:00:00.000Z DESKTOP ai-ops-center INFO ai_ops.audit AUDIT
        [{"event":"COMMAND_EXECUTE","user":"admin","target":"192.168.1.11","success":true}]
        - SSH command executed: uptime
    """
    audit_data = {
        "event": event.value,
        "user": user,
        "target": target,
        "success": success,
    }
    if extra:
        audit_data.update(extra)

    record = _audit_logger.makeRecord(
        name="ai_ops.audit",
        level=logging.INFO,
        fn="",
        lno=0,
        msg=detail,
        args=(),
        exc_info=None,
    )
    record.msg_id = "AUDIT"
    record.extra_data = audit_data
    _audit_logger.handle(record)
