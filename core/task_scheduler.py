"""
Task Scheduler — cron-like scheduled tasks.
Tasks are stored in scheduled_tasks.json and executed by APScheduler.
Each task runs via the headless agent loop.
"""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from logging_config.logger import get_logger
from logging_config.atomic_io import atomic_read_json, atomic_write_json

logger = get_logger("task_scheduler")

_TASKS_FILE = Path(__file__).resolve().parent.parent / "scheduled_tasks.json"


def list_tasks() -> list[dict]:
    return atomic_read_json(_TASKS_FILE, default=[])


def get_task(task_id: str) -> Optional[dict]:
    for t in list_tasks():
        if t.get("id") == task_id:
            return t
    return None


def add_task(
    name: str,
    prompt: str,
    schedule_type: str,  # "interval" | "cron"
    interval_minutes: int = 60,
    cron_expr: str = "",
    enabled: bool = True,
) -> dict:
    """Add a new scheduled task."""
    tasks = list_tasks()
    task = {
        "id": str(uuid.uuid4())[:8],
        "name": name,
        "prompt": prompt,
        "schedule_type": schedule_type,
        "interval_minutes": interval_minutes,
        "cron_expr": cron_expr,
        "enabled": enabled,
        "created_at": datetime.now().isoformat(),
        "last_run": None,
        "last_status": None,
    }
    tasks.append(task)
    atomic_write_json(_TASKS_FILE, tasks)
    logger.info(f"Scheduled task created: {task['id']} — {name}")
    _reschedule(task)
    return task


def update_task(task_id: str, **kwargs) -> bool:
    tasks = list_tasks()
    for t in tasks:
        if t["id"] == task_id:
            t.update(kwargs)
            atomic_write_json(_TASKS_FILE, tasks)
            _reschedule(t)
            return True
    return False


def delete_task(task_id: str) -> bool:
    tasks = list_tasks()
    new_tasks = [t for t in tasks if t["id"] != task_id]
    if len(new_tasks) == len(tasks):
        return False
    atomic_write_json(_TASKS_FILE, new_tasks)
    _remove_from_scheduler(task_id)
    return True


def set_enabled(task_id: str, enabled: bool):
    update_task(task_id, enabled=enabled)


# ─── Scheduler Integration ───

_scheduler = None


def get_scheduler():
    global _scheduler
    if _scheduler is None:
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            _scheduler = BackgroundScheduler(
                daemon=True,
                job_defaults={"coalesce": True, "max_instances": 1},
            )
            _scheduler.start()
            # Register all existing tasks
            for t in list_tasks():
                if t.get("enabled"):
                    _reschedule(t)
        except ImportError:
            logger.warning("APScheduler not available")
            return None
    return _scheduler


def _reschedule(task: dict):
    """Add or update a task in the scheduler."""
    sched = get_scheduler()
    if sched is None:
        return

    try:
        from apscheduler.triggers.interval import IntervalTrigger
        from apscheduler.triggers.cron import CronTrigger

        # Remove existing job if any
        _remove_from_scheduler(task["id"])

        if not task.get("enabled"):
            return

        # Build trigger
        if task["schedule_type"] == "cron" and task.get("cron_expr"):
            trigger = CronTrigger.from_crontab(task["cron_expr"])
        else:
            trigger = IntervalTrigger(minutes=task.get("interval_minutes", 60))

        sched.add_job(
            func=_run_task,
            args=[task["id"]],
            trigger=trigger,
            id=f"task_{task['id']}",
            replace_existing=True,
        )
        logger.info(f"Task {task['id']} scheduled ({task['schedule_type']})")
    except Exception as e:
        logger.error(f"Failed to schedule task {task['id']}: {e}")


def _remove_from_scheduler(task_id: str):
    sched = get_scheduler()
    if sched is None:
        return
    try:
        sched.remove_job(f"task_{task_id}")
    except Exception:
        pass


def _run_task(task_id: str):
    """Execute a scheduled task via headless agent loop."""
    task = get_task(task_id)
    if not task or not task.get("enabled"):
        return

    logger.info(f"Running scheduled task: {task['name']}")
    try:
        from devices.storage import DeviceStorage, DEVICE_TYPES
        from sessions.storage import create_session
        from core.headless_loop import run_headless_loop

        # Get all connections
        selected = {}
        for dtype in dict(DEVICE_TYPES):
            devices = DeviceStorage.get_by_type(dtype)
            selected[dtype] = devices[0]["id"] if devices else None
        connections = DeviceStorage.get_connections_for_selected(selected)

        # Create session
        session = create_session(f"⏱️ Scheduled: {task['name']}", connections)
        session_id = session["id"]

        # Run
        run_headless_loop(task["prompt"], connections, session_id)

        # Update last_run
        update_task(task_id, last_run=datetime.now().isoformat(), last_status="completed")
    except Exception as e:
        logger.error(f"Scheduled task {task_id} failed: {e}")
        update_task(task_id, last_run=datetime.now().isoformat(), last_status=f"failed: {e}")
