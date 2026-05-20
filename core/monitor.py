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

# Backend types — how a metric value is collected
BACKEND_SSH = "ssh"           # Run SSH command on Linux/Windows servers
BACKEND_MCP = "mcp_tool"      # Invoke any MCP tool (linux_ops, switch_ops, custom, etc.)
BACKEND_HTTP = "http_get"     # Simple HTTP GET + JSON path extraction
BACKEND_PING = "ping"         # ICMP ping count / latency

# Comparison operators
COMPARE_OPS = ["gt", "lt", "gte", "lte", "eq", "ne", "contains", "not_contains", "regex"]

DEFAULT_HEALTH_CHECKS = [
    {
        "name": "disk",
        "label": "Disk Usage",
        "icon": "💾",
        "unit": "%",
        "backend": BACKEND_SSH,
        "cmd": "df / | awk 'NR==2{gsub(\"%\",\"\"); print $5}'",
        "device_type": "linux",
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
        "backend": BACKEND_SSH,
        "cmd": "free | awk '/Mem:/{printf \"%.0f\", $3/$2*100}'",
        "device_type": "linux",
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
        "backend": BACKEND_SSH,
        "cmd": "cat /proc/loadavg | awk '{print $1}'",
        "device_type": "linux",
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
        "label": hc.get("label", hc.get("name", "")),
        "icon": hc.get("icon", "📊"),
        "unit": hc.get("unit", ""),
        # Backend selection (ssh / mcp_tool / http_get / ping)
        "backend": hc.get("backend", BACKEND_SSH),
        "device_type": hc.get("device_type", "linux"),
        # SSH backend
        "cmd": hc.get("cmd", ""),
        # MCP tool backend
        "mcp_tool": hc.get("mcp_tool", ""),
        "mcp_action": hc.get("mcp_action", ""),
        "value_extractor": hc.get("value_extractor", ""),  # regex group or JSON path
        # HTTP backend
        "http_url": hc.get("http_url", ""),
        "http_json_path": hc.get("http_json_path", ""),
        "http_headers": hc.get("http_headers", {}),
        # Common
        "threshold": hc.get("threshold", 0),
        "compare": hc.get("compare", "gt"),
        "severity": hc.get("severity", "warning"),
        "interval_minutes": hc.get("interval_minutes", 30),
        "enabled": hc.get("enabled", True),
        "last_run": None,
        "maintenance_until": hc.get("maintenance_until", ""),  # ISO datetime — alerts suppressed until then
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
    threshold=None,
    interval_minutes: int = None,
    severity: str = None,
    enabled: bool = None,
    compare: str = None,
    maintenance_until: str = None,
    backend: str = None,
    cmd: str = None,
    device_type: str = None,
    mcp_tool: str = None,
    mcp_action: str = None,
    value_extractor: str = None,
    http_url: str = None,
    http_json_path: str = None,
    http_headers: dict = None,
) -> None:
    """Updates the configuration of a single metric (any provided field)."""
    state = load_state()
    if name not in state.checks_config:
        logger.warning(f"Unknown metric: {name}")
        return

    cfg = state.checks_config[name]
    updates = {
        "threshold": threshold, "interval_minutes": interval_minutes,
        "severity": severity, "enabled": enabled, "compare": compare,
        "maintenance_until": maintenance_until, "backend": backend,
        "cmd": cmd, "device_type": device_type,
        "mcp_tool": mcp_tool, "mcp_action": mcp_action,
        "value_extractor": value_extractor, "http_url": http_url,
        "http_json_path": http_json_path, "http_headers": http_headers,
    }
    for k, v in updates.items():
        if v is not None:
            if k == "interval_minutes":
                cfg[k] = max(1, int(v))
            else:
                cfg[k] = v

    save_state(state)
    logger.info(f"Metric '{name}' updated: {cfg}")

    # Update scheduler
    _reschedule_check(name, cfg)


def add_custom_check(
    name: str,
    label: str,
    threshold,
    cmd: str = "",
    unit: str = "",
    compare: str = "gt",
    severity: str = "warning",
    interval_minutes: int = 30,
    icon: str = "📊",
    backend: str = BACKEND_SSH,
    device_type: str = "linux",
    mcp_tool: str = "",
    mcp_action: str = "",
    value_extractor: str = "",
    http_url: str = "",
    http_json_path: str = "",
    http_headers: dict = None,
) -> None:
    """Adds a new custom metric. Supports SSH / MCP / HTTP / Ping backends."""
    state = load_state()
    if name in state.checks_config:
        raise ValueError(f"A metric named '{name}' already exists.")

    state.checks_config[name] = _check_to_config({
        "label": label, "icon": icon, "unit": unit,
        "backend": backend, "device_type": device_type,
        "cmd": cmd,
        "mcp_tool": mcp_tool, "mcp_action": mcp_action,
        "value_extractor": value_extractor,
        "http_url": http_url, "http_json_path": http_json_path,
        "http_headers": http_headers or {},
        "threshold": threshold, "compare": compare,
        "severity": severity, "interval_minutes": interval_minutes,
        "enabled": True,
    })
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

# ─── Value Extraction & Comparison ──────────────────────────────────

def _extract_value(raw: str, extractor: str = ""):
    """
    Extract a numeric/string value from raw output.
    - No extractor → try float(raw), fallback to raw
    - regex:PATTERN → use first match group
    - json:PATH → simple JSON path (e.g. 'data.cpu.usage')
    """
    if not raw:
        return None

    if not extractor:
        try:
            return float(raw.strip())
        except (ValueError, AttributeError):
            return raw.strip()

    if extractor.startswith("regex:"):
        import re
        pat = extractor[6:]
        m = re.search(pat, raw)
        if m:
            try:
                return float(m.group(1) if m.groups() else m.group(0))
            except (ValueError, IndexError):
                return m.group(0)
        return None

    if extractor.startswith("json:"):
        import json as _json
        try:
            data = _json.loads(raw)
            path = extractor[5:]
            for part in path.split("."):
                if isinstance(data, dict):
                    data = data.get(part)
                elif isinstance(data, list):
                    data = data[int(part)] if part.isdigit() else None
                else:
                    return None
                if data is None:
                    return None
            return data if not isinstance(data, (int, float)) else float(data)
        except Exception:
            return None

    return raw.strip()


def _compare(value, threshold, op: str) -> bool:
    """Universal comparison — returns True if alert should fire."""
    if value is None:
        return False
    try:
        if op == "gt":
            return float(value) > float(threshold)
        if op == "gte":
            return float(value) >= float(threshold)
        if op == "lt":
            return float(value) < float(threshold)
        if op == "lte":
            return float(value) <= float(threshold)
        if op == "eq":
            return str(value).strip() == str(threshold).strip()
        if op == "ne":
            return str(value).strip() != str(threshold).strip()
        if op == "contains":
            return str(threshold) in str(value)
        if op == "not_contains":
            return str(threshold) not in str(value)
        if op == "regex":
            import re
            return bool(re.search(str(threshold), str(value)))
    except (ValueError, TypeError):
        return False
    return False


# ─── History Storage ────────────────────────────────────────────────

_HISTORY_FILE = Path(__file__).resolve().parent.parent / "monitor_history.json"
_HISTORY_MAX = 200  # last N samples per (server, metric)


def append_history(server_id: str, check_name: str, value, status: str, checked_at: str):
    """Append a data point to the history (capped at _HISTORY_MAX per metric)."""
    from logging_config.atomic_io import atomic_update_json

    def _mutate(data):
        if not isinstance(data, dict):
            data = {}
        key = f"{server_id}/{check_name}"
        series = data.get(key, [])
        series.append({
            "t": checked_at,
            "v": value if isinstance(value, (int, float)) else str(value)[:100],
            "s": status,
        })
        if len(series) > _HISTORY_MAX:
            series = series[-_HISTORY_MAX:]
        data[key] = series
        return data

    try:
        atomic_update_json(_HISTORY_FILE, _mutate, default={})
    except Exception as e:
        logger.warning(f"History append failed: {e}")


def get_history(server_id: str, check_name: str) -> list[dict]:
    """Get history for a specific (server, metric) pair."""
    from logging_config.atomic_io import atomic_read_json
    data = atomic_read_json(_HISTORY_FILE, default={})
    return data.get(f"{server_id}/{check_name}", [])


# ─── Health Checker ──────────────────────────────────────────────────

class HealthChecker:
    """Multi-backend metric collector. Supports SSH, MCP tools, HTTP, and ping."""

    def _run_ssh(self, server: dict, cfg: dict):
        """SSH backend — execute command, parse output."""
        from tools.ssh_tool import execute_ssh_command
        raw = execute_ssh_command(
            server["ip"], server["user"], server["password"],
            cfg["cmd"],
        ).strip()
        return _extract_value(raw, cfg.get("value_extractor", ""))

    def _run_mcp(self, server: dict, cfg: dict):
        """MCP tool backend — invoke any MCP tool, extract value from output."""
        from core.agent_loop import _dispatch_tool

        tool_name = cfg.get("mcp_tool", "")
        action = cfg.get("mcp_action", "")
        if not tool_name or not action:
            raise ValueError("mcp_tool and mcp_action must be set")

        # Build minimal connections dict
        from devices.storage import DeviceStorage
        connections = {}
        if server:
            dtype = cfg.get("device_type", "linux")
            connections[dtype] = {
                "ip": server.get("ip", ""),
                "user": server.get("user", ""),
                "pwd": server.get("password", ""),
                "name": server.get("name", ""),
            }

        # Tool input — MCP tools accept either command or action
        tool_input = {"command": action, "action": action}
        # If the device type uses target_host
        if server:
            tool_input["target_host"] = server.get("ip", "")

        raw = _dispatch_tool(tool_name, tool_input, connections)
        return _extract_value(raw, cfg.get("value_extractor", ""))

    def _run_http(self, server: dict, cfg: dict):
        """HTTP GET backend — fetch URL, optionally extract JSON path."""
        import httpx
        url = cfg.get("http_url", "")
        if server:
            url = url.replace("{ip}", server.get("ip", ""))
            url = url.replace("{name}", server.get("name", ""))

        # Resolve vault refs (Bearer tokens etc.)
        try:
            from tools.registry import _resolve_vault_refs
            url = _resolve_vault_refs(url)
            headers = {k: _resolve_vault_refs(str(v))
                       for k, v in (cfg.get("http_headers") or {}).items()}
        except Exception:
            headers = cfg.get("http_headers") or {}

        resp = httpx.get(url, headers=headers, timeout=10.0)
        resp.raise_for_status()

        json_path = cfg.get("http_json_path", "")
        if json_path:
            return _extract_value(resp.text, f"json:{json_path}")

        # Try numeric body, else text
        return _extract_value(resp.text, cfg.get("value_extractor", ""))

    def _run_ping(self, server: dict, cfg: dict):
        """ICMP ping backend — returns latency in ms, or -1 on failure."""
        import platform, subprocess, re as _re
        ip = server.get("ip", "")
        if not ip:
            return -1
        flag = "-n" if platform.system().lower() == "windows" else "-c"
        try:
            out = subprocess.run(
                ["ping", flag, "1", ip],
                capture_output=True, timeout=5, text=True,
            ).stdout
            m = _re.search(r"time[=<](\d+\.?\d*)\s*ms", out, _re.IGNORECASE)
            return float(m.group(1)) if m else -1.0
        except Exception:
            return -1.0

    def run_check(self, server: dict, cfg: dict, check_name: str) -> MonitorResult:
        """Run a single check using the configured backend."""
        backend = cfg.get("backend", BACKEND_SSH)
        label = cfg.get("label", check_name)
        unit = cfg.get("unit", "")
        threshold = cfg.get("threshold", 0)
        severity = cfg.get("severity", "warning")
        op = cfg.get("compare", "gt")
        sid = server.get("id", "") if server else ""
        sname = server.get("name", "global") if server else "global"
        sip = server.get("ip", "") if server else ""

        # Maintenance window check
        mw = cfg.get("maintenance_until", "")
        if mw:
            try:
                until = datetime.fromisoformat(mw)
                if datetime.now() < until:
                    return MonitorResult(
                        server_id=sid, server_name=sname, server_ip=sip,
                        check_name=check_name, check_label=label, value=0.0,
                        threshold=threshold, unit=unit, status="maintenance",
                        severity=severity, checked_at=datetime.now().isoformat(),
                        error=f"Suppressed until {mw}",
                    )
            except Exception:
                pass

        try:
            if backend == BACKEND_SSH:
                value = self._run_ssh(server, cfg)
            elif backend == BACKEND_MCP:
                value = self._run_mcp(server, cfg)
            elif backend == BACKEND_HTTP:
                value = self._run_http(server, cfg)
            elif backend == BACKEND_PING:
                value = self._run_ping(server, cfg)
            else:
                raise ValueError(f"Unknown backend: {backend}")

            # Build result
            try:
                num_value = float(value) if value is not None else 0.0
            except (TypeError, ValueError):
                num_value = 0.0

            exceeded = _compare(value, threshold, op)
            status = severity if exceeded else "ok"

            result = MonitorResult(
                server_id=sid, server_name=sname, server_ip=sip,
                check_name=check_name, check_label=label,
                value=num_value, threshold=float(threshold) if isinstance(threshold, (int, float, str)) and str(threshold).replace(".","").replace("-","").isdigit() else 0.0,
                unit=unit, status=status, severity=severity,
                checked_at=datetime.now().isoformat(),
            )

            # Append to history (non-blocking, best-effort)
            try:
                append_history(sid, check_name, num_value, status, result.checked_at)
            except Exception:
                pass

            return result

        except Exception as e:
            err_result = MonitorResult(
                server_id=sid, server_name=sname, server_ip=sip,
                check_name=check_name, check_label=label,
                value=0.0, threshold=float(threshold) if isinstance(threshold, (int, float)) else 0.0,
                unit=unit, status="error", severity=severity,
                checked_at=datetime.now().isoformat(),
                error=f"{type(e).__name__}: {str(e)[:200]}",
            )
            try:
                append_history(sid, check_name, 0, "error", err_result.checked_at)
            except Exception:
                pass
            return err_result

    def check_single_metric(self, check_name: str):
        """Checks a single metric across applicable targets."""
        state = load_state()
        cfg = state.checks_config.get(check_name)
        if not cfg or not cfg.get("enabled", True):
            return

        logger.info(f"Monitor: '{check_name}' check starting (backend={cfg.get('backend', BACKEND_SSH)})…")

        backend = cfg.get("backend", BACKEND_SSH)
        targets = self._resolve_targets(cfg, backend)

        if not targets:
            logger.info(f"Monitor: no targets for '{check_name}'")
            return

        new_results = []
        for target in targets:
            result = self.run_check(target, cfg, check_name)
            logger.info(
                f"Monitor: {result.server_name} / {check_name} = "
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

        # Replace results for this metric
        state = load_state()
        other_results = [r for r in state.results if r.get("check_name") != check_name]
        state.results = other_results + new_results
        cfg["last_run"] = datetime.now().isoformat()
        state.checks_config[check_name] = cfg
        save_state(state)
        logger.info(f"Monitor: '{check_name}' done — {len(new_results)} result(s).")

    def _resolve_targets(self, cfg: dict, backend: str) -> list[dict]:
        """Resolve the list of targets to check (servers or [None] for global)."""
        # HTTP and ping can be global (no device) if no device_type set
        device_type = cfg.get("device_type", "")
        if not device_type and backend in (BACKEND_HTTP, BACKEND_PING):
            return [{"id": "global", "name": "Global", "ip": "", "user": "", "password": ""}]

        if not device_type:
            device_type = "linux"  # SSH default

        from devices.storage import DeviceStorage
        return DeviceStorage.get_by_type(device_type)

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
