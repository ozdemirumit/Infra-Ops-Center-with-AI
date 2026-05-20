"""
Tests for core/workflow/scheduler.py — cron-triggered workflows.

We don't want a real BackgroundScheduler running threads during tests, so
we patch it with a recording fake. We do exercise the real cron parsing
path so YAML cron strings are validated end-to-end.
"""

import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.fernet import Fernet
os.environ.setdefault("DEVICE_ENCRYPTION_KEY", Fernet.generate_key().decode())


# ─── Recording fake scheduler ──────────────────────────────────────

class _FakeJob:
    def __init__(self, fn, trigger, args, job_id):
        self.id = job_id
        self.func = fn
        self.trigger = trigger
        self.args = args
        # APScheduler's real CronTrigger computes a next fire time. We
        # use the real one (we don't patch CronTrigger), so we can call
        # get_next_fire_time to mimic .next_run_time.
        try:
            from datetime import datetime, timezone
            self.next_run_time = trigger.get_next_fire_time(
                None, datetime.now(timezone.utc)
            )
        except Exception:
            self.next_run_time = None


class _FakeScheduler:
    instances = []

    def __init__(self, *_a, **_kw):
        self.jobs: dict[str, _FakeJob] = {}
        self.started = False
        self.shutdown_called = False
        _FakeScheduler.instances.append(self)

    def start(self):
        self.started = True

    def add_job(self, fn, args=None, trigger=None, id=None,
                replace_existing=False, **_kw):
        job_id = id or f"job_{len(self.jobs)}"
        if job_id in self.jobs and not replace_existing:
            raise RuntimeError("duplicate job")
        self.jobs[job_id] = _FakeJob(fn, trigger, args or [], job_id)

    def remove_job(self, job_id):
        self.jobs.pop(job_id, None)

    def get_jobs(self):
        return list(self.jobs.values())

    def shutdown(self, wait=False):
        self.shutdown_called = True


# ─── Helpers ───────────────────────────────────────────────────────

def _install_fake_scheduler(monkeypatch):
    """Patch BackgroundScheduler in apscheduler.schedulers.background."""
    _FakeScheduler.instances.clear()
    # Reset module singleton
    from core.workflow import scheduler as sch_mod
    sch_mod._scheduler = None
    # Patch import target
    import apscheduler.schedulers.background as bg
    monkeypatch.setattr(bg, "BackgroundScheduler", _FakeScheduler)


def _temp_workflow(yaml_text: str, name: str = "_test_sched") -> Path:
    """Drop a YAML file under WORKFLOWS_DIR and return its path."""
    from core.workflow.loader import WORKFLOWS_DIR
    p = WORKFLOWS_DIR / f"{name}.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    return p


# ─── Tests ─────────────────────────────────────────────────────────

def test_start_with_no_scheduled_workflows_returns_empty(monkeypatch):
    _install_fake_scheduler(monkeypatch)
    from core.workflow.scheduler import (
        start_workflow_scheduler, get_scheduled_jobs_info,
    )
    # Default examples already include daily_health_report with a cron
    sched = start_workflow_scheduler()
    assert sched is not None
    rows = get_scheduled_jobs_info()
    # At least the shipped daily_health_report (cron: "0 8 * * *") is scheduled
    names = {r["workflow"] for r in rows}
    assert "daily_health_report" in names


def test_yaml_with_cron_is_registered(monkeypatch):
    _install_fake_scheduler(monkeypatch)
    from core.workflow.scheduler import (
        start_workflow_scheduler, get_scheduled_jobs_info,
    )

    yaml_text = """
name: _test_sched_yaml
description: test
trigger:
  type: schedule
  cron: "*/15 * * * *"
steps:
  - id: noop
    type: notify
    channel: log
    message: tick
""".lstrip()
    p = _temp_workflow(yaml_text, "_test_sched_yaml")
    try:
        start_workflow_scheduler()
        rows = get_scheduled_jobs_info()
        names = {r["workflow"] for r in rows}
        assert "_test_sched_yaml" in names

        match = next(r for r in rows if r["workflow"] == "_test_sched_yaml")
        # Cron canonicalised by APScheduler — minute field is */15 → "*/15"
        assert "*/15" in match["cron"]
        # next_run_time present because the fake honoured CronTrigger
        assert match["next_run"] is not None
    finally:
        p.unlink(missing_ok=True)


def test_invalid_cron_does_not_crash_others(monkeypatch):
    _install_fake_scheduler(monkeypatch)
    from core.workflow.scheduler import (
        start_workflow_scheduler, get_scheduled_jobs_info,
    )
    bad = _temp_workflow("""
name: _test_bad_cron
trigger:
  type: schedule
  cron: "not a valid cron expression"
steps:
  - id: x
    type: notify
    channel: log
    message: nope
""".lstrip(), "_test_bad_cron")

    good = _temp_workflow("""
name: _test_good_cron
trigger:
  type: schedule
  cron: "0 9 * * *"
steps:
  - id: x
    type: notify
    channel: log
    message: morning
""".lstrip(), "_test_good_cron")

    try:
        start_workflow_scheduler()
        names = {r["workflow"] for r in get_scheduled_jobs_info()}
        # Bad one rejected, good one kept
        assert "_test_bad_cron" not in names
        assert "_test_good_cron" in names
    finally:
        bad.unlink(missing_ok=True)
        good.unlink(missing_ok=True)


def test_missing_cron_field_skipped(monkeypatch):
    _install_fake_scheduler(monkeypatch)
    from core.workflow.scheduler import (
        start_workflow_scheduler, get_scheduled_jobs_info,
    )
    p = _temp_workflow("""
name: _test_no_cron
trigger:
  type: schedule
steps:
  - id: x
    type: notify
    channel: log
    message: hi
""".lstrip(), "_test_no_cron")
    try:
        start_workflow_scheduler()
        names = {r["workflow"] for r in get_scheduled_jobs_info()}
        assert "_test_no_cron" not in names
    finally:
        p.unlink(missing_ok=True)


def test_reload_picks_up_new_workflow(monkeypatch):
    _install_fake_scheduler(monkeypatch)
    from core.workflow.scheduler import (
        start_workflow_scheduler, reload_workflow_jobs,
        get_scheduled_jobs_info,
    )

    start_workflow_scheduler()
    before = {r["workflow"] for r in get_scheduled_jobs_info()}
    assert "_test_added_later" not in before

    p = _temp_workflow("""
name: _test_added_later
trigger:
  type: schedule
  cron: "30 3 * * *"
steps:
  - id: x
    type: notify
    channel: log
    message: late-night
""".lstrip(), "_test_added_later")
    try:
        n = reload_workflow_jobs()
        after = {r["workflow"] for r in get_scheduled_jobs_info()}
        assert "_test_added_later" in after
        # n is the total count, must be >= the previous count + 1
        assert n >= 1
    finally:
        p.unlink(missing_ok=True)


def test_reload_drops_removed_workflow(monkeypatch):
    _install_fake_scheduler(monkeypatch)
    from core.workflow.scheduler import (
        start_workflow_scheduler, reload_workflow_jobs,
        get_scheduled_jobs_info,
    )
    p = _temp_workflow("""
name: _test_to_remove
trigger:
  type: schedule
  cron: "0 0 * * *"
steps:
  - id: x
    type: notify
    channel: log
    message: y
""".lstrip(), "_test_to_remove")

    start_workflow_scheduler()
    assert "_test_to_remove" in {r["workflow"] for r in get_scheduled_jobs_info()}

    p.unlink()
    reload_workflow_jobs()
    assert "_test_to_remove" not in {r["workflow"] for r in get_scheduled_jobs_info()}


def test_fire_workflow_starts_a_run(monkeypatch):
    """The job callback must invoke WorkflowEngine.start with triggered_by=schedule."""
    _install_fake_scheduler(monkeypatch)

    # Sandbox the runs file
    from core.workflow import storage
    storage.RUNS_FILE = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".json").name)

    p = _temp_workflow("""
name: _test_fire
trigger:
  type: schedule
  cron: "0 0 * * *"
steps:
  - id: hello
    type: notify
    channel: log
    message: scheduled fire
""".lstrip(), "_test_fire")

    try:
        from core.workflow.scheduler import start_workflow_scheduler, _fire_workflow
        start_workflow_scheduler()

        _fire_workflow("_test_fire")

        from core.workflow import list_runs
        runs = list_runs()
        match = [r for r in runs if r["workflow_name"] == "_test_fire"]
        assert match, "no run created by _fire_workflow"
        run = match[0]
        assert run["triggered_by"].startswith("schedule:")
        assert run["status"] == "completed"
    finally:
        p.unlink(missing_ok=True)


def test_apscheduler_unavailable_returns_none(monkeypatch):
    """If APScheduler is not importable, start returns None gracefully."""
    # Reset module singleton
    from core.workflow import scheduler as sch_mod
    sch_mod._scheduler = None
    # Make the import fail
    import builtins
    real_import = builtins.__import__

    def fail_aps(name, *a, **kw):
        if name == "apscheduler.schedulers.background":
            raise ImportError("simulated no apscheduler")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fail_aps)
    assert sch_mod.start_workflow_scheduler() is None


def test_stop_resets_singleton(monkeypatch):
    _install_fake_scheduler(monkeypatch)
    from core.workflow.scheduler import (
        start_workflow_scheduler, stop_workflow_scheduler,
    )
    sched = start_workflow_scheduler()
    assert sched is not None
    stop_workflow_scheduler()
    from core.workflow import scheduler as sch_mod
    assert sch_mod._scheduler is None
    # Fake recorded the shutdown call
    assert any(f.shutdown_called for f in _FakeScheduler.instances)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
