"""
Incident Manager.

Receives monitor threshold violations and creates automatic incident sessions.
Does not create a new incident if one is already open for the same server+metric.
"""

import logging
from datetime import datetime
from typing import Optional

from logging_config.logger import get_logger

logger = get_logger("incident_manager")


def handle_alert(alert, current_state) -> Optional[str]:
    """
    Creates an incident session for a monitor threshold violation.

    Args:
        alert:         MonitorResult object (status="warning"/"critical")
        current_state: MonitorState — used to check for existing incidents

    Returns:
        Created session_id (str) or None (if already open / error)
    """
    # Is there an open incident for the same server + metric?
    existing = _find_existing_incident(alert, current_state)
    if existing:
        logger.info(
            f"Incident already open: {alert.server_name}/{alert.check_name} "
            f"-> session {existing}"
        )
        return existing

    # Task title
    label = f"{alert.server_name} — {alert.check_label} %{alert.value:.1f} (threshold: %{alert.threshold})"
    title = f"🚨 Automatic: {label}"
    description = (
        f"Detected by autonomous monitoring.\n"
        f"Server: {alert.server_name} ({alert.server_ip})\n"
        f"Metric: {alert.check_label} = {alert.value}{alert.unit}\n"
        f"Threshold: {alert.threshold}{alert.unit}\n"
        f"Severity: {alert.severity}\n"
        f"Detection time: {alert.checked_at}"
    )

    # Create session
    try:
        from sessions.storage import create_session
        from devices.storage import DeviceStorage

        # Find the server connection
        servers = DeviceStorage.get_by_type("linux")
        server = next((s for s in servers if s["id"] == alert.server_id), None)

        connections = {}
        if server:
            connections["linux"] = {
                "ip": server["ip"],
                "user": server["user"],
                "password": server["password"],
                "name": server["name"],
                "hostname": server.get("hostname", ""),
            }

        session = create_session(title=title, connections=connections)
        session_id = session["id"]
        logger.info(f"Incident session created: {session_id} — {title}")

    except Exception as e:
        logger.error(f"Failed to create incident session: {e}")
        return None

    # Prefer a workflow if one is registered for this metric+severity;
    # otherwise fall back to the free-form headless agent prompt.
    handled_by_workflow = _maybe_run_workflow(alert, connections, session_id)
    if not handled_by_workflow:
        try:
            prompt = _build_remediation_prompt(alert)
            _run_headless(prompt, connections, session_id)
        except Exception as e:
            logger.error(f"Headless agent error: {e}")

    return session_id


def _maybe_run_workflow(alert, connections: dict, session_id: str) -> bool:
    """
    Look for an incident-triggered workflow whose trigger matches this alert.
    Returns True if a workflow was launched.
    """
    try:
        from core.workflow import list_workflows, load_workflow, WorkflowEngine
        for meta in list_workflows():
            if meta.get("errors"):
                continue
            trig = meta.get("trigger", {}) or {}
            if trig.get("type") != "incident":
                continue
            # Match metric (optional) and severity (optional)
            if trig.get("metric") and trig["metric"] != alert.check_name:
                continue
            if trig.get("severity") and trig["severity"] != alert.severity:
                continue

            wf = load_workflow(meta["name"])
            inputs = {
                "server_name": alert.server_name,
                "server_ip": alert.server_ip,
                "server_id": alert.server_id,
                "metric": alert.check_name,
                "value": alert.value,
                "threshold": alert.threshold,
                "unit": alert.unit,
                "severity": alert.severity,
            }
            engine = WorkflowEngine()
            # Run in background so handle_alert returns fast
            import threading
            threading.Thread(
                target=engine.start,
                kwargs=dict(
                    workflow=wf, inputs=inputs, connections=connections,
                    session_id=session_id, triggered_by=f"incident:{alert.check_name}",
                ),
                daemon=True, name=f"wf-{meta['name'][:20]}",
            ).start()
            logger.info(
                f"Workflow '{meta['name']}' launched for "
                f"{alert.check_name}/{alert.severity}"
            )
            return True
    except Exception as e:
        logger.warning(f"Workflow lookup failed: {e}")
    return False


# ─── Helper Functions ───────────────────────────────────────────────

def _find_existing_incident(alert, state) -> Optional[str]:
    """Is there an open incident session for the same server+metric?"""
    try:
        from sessions.storage import list_sessions, STATUS_ACTIVE
        for sess in list_sessions():
            if sess.get("status") != STATUS_ACTIVE:
                continue
            title = sess.get("title", "")
            # Title check: same server + metric
            if alert.server_name in title and alert.check_label in title:
                return sess["id"]
    except Exception:
        pass
    return None


def _build_remediation_prompt(alert) -> str:
    """Generates an appropriate remediation command prompt for a threshold violation."""
    prompts = {
        "disk": (
            f"Disk usage on {alert.server_name} is {alert.value:.0f}% "
            f"(threshold: {alert.threshold}%). "
            "First analyze disk usage (df -h, du -sh), "
            "clean up unnecessary log files, tmp directory, and old packages. "
            "Then check disk status again and provide a summary."
        ),
        "memory": (
            f"RAM usage on {alert.server_name} is {alert.value:.0f}% "
            f"(threshold: {alert.threshold}%). "
            "List memory-consuming processes (ps aux --sort=-%mem | head -20), "
            "clear cache if necessary, and report the status."
        ),
        "cpu_load": (
            f"CPU load on {alert.server_name} is {alert.value:.2f} "
            f"(threshold: {alert.threshold}). "
            "Identify high CPU processes (top -bn1 | head -20), "
            "determine the root cause and report."
        ),
    }
    return prompts.get(alert.check_name, f"Issue detected with {alert.check_label} on {alert.server_name}. Investigate and report.")


def trigger_ai_investigation(check_name: str, server_name: str = None) -> Optional[str]:
    """
    AI investigation triggered by the user.
    Runs AI for the specified metric and creates an incident session.

    Args:
        check_name: Metric name (disk, memory, cpu_load, custom...)
        server_name: Specific server (None means worst result from all)

    Returns:
        Created session_id or None
    """
    from core.monitor import load_state, get_checks_config

    state = load_state()
    config = get_checks_config()
    cfg = config.get(check_name)
    if not cfg:
        logger.error(f"Unknown metric: {check_name}")
        return None

    # Find the worst result
    results = [r for r in state.results if r.get("check_name") == check_name]
    if server_name:
        results = [r for r in results if r.get("server_name") == server_name]

    if not results:
        logger.warning(f"No results found for '{check_name}'")
        return None

    # Select the result with the worst status
    worst = max(results, key=lambda r: {"ok": 0, "warning": 1, "critical": 2, "error": 3}.get(r.get("status", "ok"), 0))

    # Create a MonitorResult-like object (for handle_alert)
    from dataclasses import dataclass

    @dataclass
    class _AlertProxy:
        server_id: str
        server_name: str
        server_ip: str
        check_name: str
        check_label: str
        value: float
        threshold: float
        unit: str
        status: str
        severity: str
        checked_at: str

    alert = _AlertProxy(
        server_id=worst.get("server_id", ""),
        server_name=worst.get("server_name", ""),
        server_ip=worst.get("server_ip", ""),
        check_name=worst.get("check_name", check_name),
        check_label=cfg.get("label", check_name),
        value=worst.get("value", 0),
        threshold=cfg.get("threshold", 0),
        unit=cfg.get("unit", ""),
        status=worst.get("status", "warning"),
        severity=cfg.get("severity", "warning"),
        checked_at=worst.get("checked_at", ""),
    )

    return handle_alert(alert, state)


def _run_headless(prompt: str, connections: dict, session_id: str):
    """
    Runs the agent loop without Streamlit.
    Uses logging instead of st.* calls.
    """
    import threading

    def _worker():
        try:
            from core.headless_loop import run_headless_loop
            run_headless_loop(prompt, connections, session_id)
        except Exception as e:
            logger.error(f"Failed to run headless agent: {e}")

    t = threading.Thread(target=_worker, daemon=True, name=f"incident-{session_id[:8]}")
    t.start()
    logger.info(f"Headless agent thread started: {session_id}")
