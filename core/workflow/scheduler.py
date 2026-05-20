"""
Schedule-triggered workflows.

Any workflow whose YAML declares:

    trigger:
      type: schedule
      cron: "0 8 * * *"   # 5-field crontab (m h dom mon dow)

is automatically registered with APScheduler. When the cron fires, the
WorkflowEngine starts a fresh run with `triggered_by=schedule:<name>`.

The scheduler is restartable: every call to start_workflow_scheduler()
re-scans the workflow directory, so saving / editing / deleting a YAML
file in the Editor tab can trigger a reload via reload_workflow_jobs().
"""

import threading
from datetime import datetime
from typing import Optional

from logging_config.logger import get_logger

logger = get_logger("workflow.scheduler")


_JOB_PREFIX = "wf_schedule:"
_scheduler = None
_lock = threading.Lock()


# ─── Lifecycle ──────────────────────────────────────────────────────

def start_workflow_scheduler():
    """
    Start (or reuse) the APScheduler instance and register every
    schedule-triggered workflow. Returns the scheduler or None if
    APScheduler is unavailable.
    """
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning("APScheduler not installed; scheduled workflows disabled.")
        return None

    with _lock:
        if _scheduler is None:
            _scheduler = BackgroundScheduler(
                daemon=True,
                job_defaults={"coalesce": True, "max_instances": 1},
            )
            _scheduler.start()
            logger.info("Workflow scheduler started.")
        _register_all_jobs(_scheduler)
    return _scheduler


def reload_workflow_jobs() -> int:
    """
    Re-scan the workflow directory and refresh scheduled jobs.
    Returns the number of currently-scheduled workflows.
    """
    with _lock:
        if _scheduler is None:
            return 0
        # Remove every previous wf job, then re-add
        for job in list(_scheduler.get_jobs()):
            if job.id.startswith(_JOB_PREFIX):
                try:
                    _scheduler.remove_job(job.id)
                except Exception:
                    pass
        return _register_all_jobs(_scheduler)


def stop_workflow_scheduler():
    """Shut the scheduler down (called from tests / explicit teardown)."""
    global _scheduler
    with _lock:
        if _scheduler is not None:
            try:
                _scheduler.shutdown(wait=False)
            except Exception:
                pass
            _scheduler = None


# ─── Inspection ─────────────────────────────────────────────────────

def get_scheduled_jobs_info() -> list[dict]:
    """
    Snapshot of scheduled workflows for UI display.

    Each entry: { workflow, cron, next_run, job_id, error }
    """
    if _scheduler is None:
        return []
    out = []
    for job in _scheduler.get_jobs():
        if not job.id.startswith(_JOB_PREFIX):
            continue
        next_run = job.next_run_time
        out.append({
            "workflow": job.id[len(_JOB_PREFIX):],
            "cron": _format_trigger(job.trigger),
            "next_run": next_run.isoformat() if next_run else None,
            "job_id": job.id,
            "error": None,
        })
    return sorted(out, key=lambda r: r.get("next_run") or "")


# ─── Internals ──────────────────────────────────────────────────────

def _register_all_jobs(scheduler) -> int:
    """Discover every YAML workflow with a schedule trigger and add a job."""
    from core.workflow.loader import list_workflows, load_workflow
    from apscheduler.triggers.cron import CronTrigger

    count = 0
    for meta in list_workflows():
        if meta.get("errors"):
            continue
        trig = meta.get("trigger") or {}
        if trig.get("type") != "schedule":
            continue
        cron = trig.get("cron")
        if not cron:
            logger.warning(
                f"Workflow '{meta['name']}' has trigger=schedule but no cron — skipping."
            )
            continue

        try:
            trigger = CronTrigger.from_crontab(cron)
        except Exception as e:
            logger.error(f"Invalid cron '{cron}' on workflow '{meta['name']}': {e}")
            continue

        job_id = f"{_JOB_PREFIX}{meta['name']}"
        try:
            scheduler.add_job(
                _fire_workflow,
                args=[meta["name"]],
                trigger=trigger,
                id=job_id,
                replace_existing=True,
                misfire_grace_time=300,
            )
            count += 1
            logger.info(
                f"Scheduled workflow '{meta['name']}' with cron '{cron}'"
            )
        except Exception as e:
            logger.error(f"Failed to schedule workflow '{meta['name']}': {e}")
    return count


def _fire_workflow(workflow_name: str):
    """Cron callback — launches a fresh workflow run."""
    logger.info(f"[wf-cron] firing workflow: {workflow_name}")
    try:
        from core.workflow import load_workflow, WorkflowEngine
        wf = load_workflow(workflow_name)
        engine = WorkflowEngine()
        run_id = engine.start(
            workflow=wf,
            inputs={},  # default inputs come from the YAML
            connections={},
            triggered_by=f"schedule:{workflow_name}",
        )
        logger.info(f"[wf-cron] {workflow_name} → run {run_id}")
    except Exception as e:
        logger.error(f"[wf-cron] {workflow_name} failed: {e}", exc_info=True)


def _format_trigger(trigger) -> str:
    """Best-effort human-friendly cron expression for the UI."""
    try:
        # APScheduler CronTrigger exposes .fields with names
        # in the order: year, month, day, week, day_of_week, hour, minute, second
        parts = {f.name: str(f) for f in trigger.fields}
        # Standard 5-field order: m h dom mon dow
        return " ".join([
            parts.get("minute", "*"),
            parts.get("hour", "*"),
            parts.get("day", "*"),
            parts.get("month", "*"),
            parts.get("day_of_week", "*"),
        ])
    except Exception:
        return str(trigger)
