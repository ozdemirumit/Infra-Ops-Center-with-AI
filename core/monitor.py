"""
Autonomous Server Monitoring Module — Independent Scheduling Per Metric.

Each metric (disk, RAM, CPU, custom...) runs independently on its own interval.
Runs in the background with APScheduler, triggers IncidentManager on threshold violations.

Streamlit integration:
    from core.monitor import get_scheduler
    get_scheduler()
"""

import json
import threading
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Optional

from config.settings import settings
from logging_config.logger import get_logger

logger = get_logger("monitor")

_STATE_FILE = Path(__file__).resolve().parent.parent / "monitor_state.json"

# ─── Default Metric Definitions ──────────────────────────────────────────────

DEFAULT_HEALTH_CHECKS = [
    {
        "name": "disk",
        "label": "Disk Usage",
        "icon": "💾",
        "unit": "%",
        "cmd": "df / | awk 'NR==2{gsub(\"%\",\"\"); print $5}'",
        "threshold": 85,
        "compare": "gt",
        "severity": "critical",
        "interval_minutes": 30,
        "enabled": True,
    },
    {
        "name": "memory",
        "label": "RAM Usage",
        "icon": "🧠",
        "unit": "%",
        "cmd": "free | awk '/Mem:/{printf \"%.0f\", $3/$2*100}'",
        "threshold": 90,
        "compare": "gt",
        "severity": "warning",
        "interval_minutes": 15,
        "enabled": True,
    },
    {
        "name": "cpu_load",
        "label": "CPU Load (1min)",
        "icon": "⚡",
        "unit": "",
        "cmd": "cat /proc/loadavg | awk '{print $1}'",
        "threshold": 4.0,
        "compare": "gt",
        "severity": "warning",
        "interval_minutes": 10,
        "enabled": True,
    },
]

# Backward compatibility
HEALTH_CHECKS = DEFAULT_HEALTH_CHECKS


# ─── Data Models ──────────────────────────────────────────────────────

@dataclass
class MonitorResult:
    server_id: str
    server_name: str
    server_ip: str
    check_name: str
    check_label: str
    value: float
    threshold: float
    unit: str
    status: str           # "ok" | "warning" | "critical"
    severity: str
    checked_at: str
    incident_session_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class MonitorState:
    scheduler_running: bool = False
    checks_config: dict = field(default_factory=dict)
    results: list = field(default_factory=list)


# ─── Storage ────────────────────────────────────────────────────────

def load_state() -> MonitorState:
    from logging_config.atomic_io import atomic_read_json

    if not _STATE_FILE.exists():
        return _init_default_state()

    data = atomic_read_json(_STATE_FILE, default=None)
    if data is None:
        return _init_default_state()

    state = MonitorState()
    state.scheduler_running = data.get("scheduler_running", False)
    state.checks_config = data.get("checks_config", {})
    state.results = data.get("results", [])

    # Add default metrics if missing from config
    changed = False
    for hc in DEFAULT_HEALTH_CHECKS:
        if hc["name"] not in state.checks_config:
            state.checks_config[hc["name"]] = _check_to_config(hc)
            changed = True
    if changed:
        save_state(state)

    return state


def _init_default_state() -> MonitorState:
    """Creates the default state."""
    state = MonitorState()
    for hc in DEFAULT_HEALTH_CHECKS:
        state.checks_config[hc["name"]] = _check_to_config(hc)
    save_state(state)
    return state


def _check_to_config(hc: dict) -> dict:
    """Creates a config dict from a health check definition."""
    return {
        "label": hc.get("label", hc["name"]),
        "icon": hc.get("icon", "📊"),
        "unit": hc.get("unit", ""),
        "cmd": hc.get("cmd", ""),
        "threshold": hc.get("threshold", 0),
        "compare": hc.get("compare", "gt"),
        "severity": hc.get("severity", "warning"),
        "interval_minutes": hc.get("interval_minutes", 30),
        "enabled": hc.get("enabled", True),
        "last_run": None,
    }


def save_state(state: MonitorState):
    from logging_config.atomic_io import atomic_write_json
    try:
        data = {
            "scheduler_running": state.scheduler_running,
            "checks_config": state.checks_config,
            "results": [asdict(r) if not isinstance(r, dict) else r for r in state.results],
        }
        atomic_write_json(_STATE_FILE, data)
    except Exception as e:
        logger.error(f"Failed to save monitor state: {e}")


# ─── Config CRUD ─────────────────────────────────────────────────────

def get_checks_config() -> dict:
    """Returns all metric configurations."""
    return load_state().checks_config


def update_check_config(
    name: str,
    threshold: float = None,
    interval_minutes: int = None,
    severity: str = None,
    enabled: bool = None,
) -> None:
    """Updates the configuration of a single metric."""
    state = load_state()
    if name not in state.checks_config:
        logger.warning(f"Unknown metric: {name}")
        return

    cfg = state.checks_config[name]
    if threshold is not None:
        cfg["threshold"] = threshold
    if interval_minutes is not None:
        cfg["interval_minutes"] = max(1, interval_minutes)
    if severity is not None:
        cfg["severity"] = severity
    if enabled is not None:
        cfg["enabled"] = enabled

    save_state(state)
    logger.info(f"Metric '{name}' updated: {cfg}")

    # Update scheduler
    _reschedule_check(name, cfg)


def add_custom_check(
    name: str,
    label: str,
    cmd: str,
    threshold: float,
    unit: str = "",
    compare: str = "gt",
    severity: str = "warning",
    interval_minutes: int = 30,
    icon: str = "📊",
) -> None:
    """Adds a new custom metric."""
    state = load_state()
    if name in state.checks_config:
        raise ValueError(f"A metric named '{name}' already exists.")

    state.checks_config[name] = {
        "label": label,
        "icon": icon,
        "unit": unit,
        "cmd": cmd,
        "threshold": threshold,
        "compare": compare,
        "severity": severity,
        "interval_minutes": interval_minutes,
        "enabled": True,
        "last_run": None,
    }
    save_state(state)
    logger.info(f"Custom metric added: {name}")

    # Add scheduler job
    _schedule_check(name, state.checks_config[name])


def remove_custom_check(name: str) -> None:
    """Removes a custom metric (default metrics cannot be removed)."""
    default_names = {hc["name"] for hc in DEFAULT_HEALTH_CHECKS}
    if name in default_names:
        raise ValueError(f"'{name}' is a default metric and cannot be removed. You can disable it instead.")

    state = load_state()
    if name in state.checks_config:
        del state.checks_config[name]
        # Also remove results
        state.results = [r for r in state.results if r.get("check_name") != name]
        save_state(state)

        # Remove scheduler job
        _remove_check_job(name)
        logger.info(f"Custom metric removed: {name}")


def get_results_for_check(check_name: str) -> list:
    """Returns results for a specific metric."""
    state = load_state()
    return [r for r in state.results if r.get("check_name") == check_name]


# ─── Health Checker ──────────────────────────────────────────────────

class HealthChecker:
    """Runs health checks on registered Linux servers."""

    def run_check(self, server: dict, check_config: dict, check_name: str) -> MonitorResult:
        """Runs a single check on a single server."""
        try:
            from tools.ssh_tool import execute_ssh_command
            raw = execute_ssh_command(
                server["ip"], server["user"], server["password"],
                check_config["cmd"]
            ).strip()
            value = float(raw)
        except ValueError:
            return MonitorResult(
                server_id=server["id"], server_name=server["name"], server_ip=server["ip"],
                check_name=check_name, check_label=check_config.get("label", check_name),
                value=0.0, threshold=check_config["threshold"], unit=check_config.get("unit", ""),
                status="error", severity=check_config.get("severity", "warning"),
                checked_at=datetime.now().isoformat(), error=f"Could not parse value: {raw!r}",
            )
        except Exception as e:
            return MonitorResult(
                server_id=server["id"], server_name=server["name"], server_ip=server["ip"],
                check_name=check_name, check_label=check_config.get("label", check_name),
                value=0.0, threshold=check_config["threshold"], unit=check_config.get("unit", ""),
                status="error", severity=check_config.get("severity", "warning"),
                checked_at=datetime.now().isoformat(), error=str(e),
            )

        compare = check_config.get("compare", "gt")
        exceeded = (value > check_config["threshold"]) if compare == "gt" else (value < check_config["threshold"])
        status = check_config["severity"] if exceeded else "ok"

        return MonitorResult(
            server_id=server["id"], server_name=server["name"], server_ip=server["ip"],
            check_name=check_name, check_label=check_config.get("label", check_name),
            value=value, threshold=check_config["threshold"], unit=check_config.get("unit", ""),
            status=status, severity=check_config.get("severity", "warning"),
            checked_at=datetime.now().isoformat(),
        )

    def check_single_metric(self, check_name: str):
        """Checks a single metric across all servers (scheduler job function)."""
        state = load_state()
        cfg = state.checks_config.get(check_name)
        if not cfg or not cfg.get("enabled", True):
            return

        logger.info(f"Monitor: '{check_name}' check starting...")

        try:
            from devices.storage import DeviceStorage
            servers = DeviceStorage.get_by_type("linux")
        except Exception as e:
            logger.error(f"Monitor: Failed to get server list: {e}")
            return

        if not servers:
            return

        new_results = []
        for server in servers:
            result = self.run_check(server, cfg, check_name)
            logger.info(
                f"Monitor: {server['name']} / {check_name} = "
                f"{result.value}{result.unit} [{result.status}]"
            )

            if result.status in ("warning", "critical"):
                try:
                    from core.incident_manager import handle_alert
                    session_id = handle_alert(result, state)
                    result.incident_session_id = session_id
                except Exception as e:
                    logger.error(f"Monitor: Failed to create incident: {e}")

            new_results.append(asdict(result))

        # Update existing results (replace those for this metric)
        state = load_state()
        other_results = [r for r in state.results if r.get("check_name") != check_name]
        state.results = other_results + new_results
        cfg["last_run"] = datetime.now().isoformat()
        state.checks_config[check_name] = cfg
        save_state(state)
        logger.info(f"Monitor: '{check_name}' check completed — {len(new_results)} results.")

    def check_all_servers(self):
        """Backward compatibility — checks all active metrics."""
        state = load_state()
        for check_name, cfg in state.checks_config.items():
            if cfg.get("enabled", True):
                self.check_single_metric(check_name)


# ─── Scheduler ───────────────────────────────────────────────────────

_scheduler_instance = None
_scheduler_lock = threading.Lock()


def _get_scheduler_instance():
    """Returns the scheduler singleton."""
    global _scheduler_instance
    return _scheduler_instance


def _schedule_check(check_name: str, cfg: dict):
    """Adds/updates a scheduler job for a single metric."""
    scheduler = _get_scheduler_instance()
    if not scheduler:
        return

    try:
        from apscheduler.triggers.interval import IntervalTrigger

        checker = HealthChecker()
        job_id = f"health_check_{check_name}"

        if cfg.get("enabled", True):
            scheduler.add_job(
                func=checker.check_single_metric,
                args=[check_name],
                trigger=IntervalTrigger(minutes=cfg.get("interval_minutes", 30)),
                id=job_id,
                replace_existing=True,
                next_run_time=None,
            )
            logger.info(f"Scheduler: '{check_name}' job added ({cfg.get('interval_minutes', 30)}min)")
        else:
            _remove_check_job(check_name)
    except Exception as e:
        logger.error(f"Scheduler job error ({check_name}): {e}")


def _reschedule_check(check_name: str, cfg: dict):
    """Updates the scheduler when metric configuration changes."""
    _schedule_check(check_name, cfg)


def _remove_check_job(check_name: str):
    """Removes a scheduler job."""
    scheduler = _get_scheduler_instance()
    if not scheduler:
        return
    job_id = f"health_check_{check_name}"
    try:
        scheduler.remove_job(job_id)
        logger.info(f"Scheduler: '{check_name}' job removed")
    except Exception:
        pass


def get_scheduler(interval_minutes: int = 30):
    """
    Starts APScheduler and creates a separate job for each active metric.
    Called only once via st.cache_resource.
    """
    global _scheduler_instance

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning("APScheduler is not installed.")
        return None

    with _scheduler_lock:
        scheduler = BackgroundScheduler(
            daemon=True,
            job_defaults={"coalesce": True, "max_instances": 1},
        )
        _scheduler_instance = scheduler

        # Separate job for each active metric
        state = load_state()
        for check_name, cfg in state.checks_config.items():
            if cfg.get("enabled", True):
                _schedule_check(check_name, cfg)

        scheduler.start()

        state.scheduler_running = True
        save_state(state)

        logger.info(f"Monitor Scheduler started ({len(state.checks_config)} metrics)")
        return scheduler


def run_check_now(check_name: str = None) -> list[dict]:
    """
    Manual trigger. If check_name is provided, only that metric is checked;
    otherwise all active metrics are checked.
    """
    checker = HealthChecker()
    if check_name:
        checker.check_single_metric(check_name)
    else:
        checker.check_all_servers()
    return load_state().results
