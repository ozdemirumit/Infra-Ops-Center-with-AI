"""
Tests for the workflow engine — template, loader, engine, and persistence.

We mock the agent / tool / metric dispatch points so the tests stay hermetic.
"""

import sys
import os
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.fernet import Fernet
os.environ.setdefault("DEVICE_ENCRYPTION_KEY", Fernet.generate_key().decode())


# ─── template.render ────────────────────────────────────────────────

def test_render_plain_string():
    from core.workflow.template import render
    assert render("hello", {}) == "hello"


def test_render_substitutes_path():
    from core.workflow.template import render
    out = render("{{ inputs.host }} is up", {"inputs": {"host": "srv01"}})
    assert out == "srv01 is up"


def test_render_returns_typed_value_for_whole_placeholder():
    from core.workflow.template import render
    # Whole-string placeholder → returns the actual int, not "42"
    assert render("{{ inputs.n }}", {"inputs": {"n": 42}}) == 42


def test_render_walks_dict_and_list():
    from core.workflow.template import render
    out = render(
        {"cmd": "df {{ inputs.path }}", "tags": ["{{ inputs.env }}", "ops"]},
        {"inputs": {"path": "/var", "env": "prod"}},
    )
    assert out == {"cmd": "df /var", "tags": ["prod", "ops"]}


def test_render_missing_path_stays_empty():
    from core.workflow.template import render
    assert render("x={{ missing.path }}", {}) == "x="


# ─── template.evaluate_when ─────────────────────────────────────────

def test_when_truthy_path():
    from core.workflow.template import evaluate_when
    assert evaluate_when("step.failed", {"step": {"failed": True}}) is True


def test_when_equality():
    from core.workflow.template import evaluate_when
    assert evaluate_when('step.status == "ok"', {"step": {"status": "ok"}}) is True
    assert evaluate_when('step.status == "ok"', {"step": {"status": "fail"}}) is False


def test_when_not_negation():
    from core.workflow.template import evaluate_when
    assert evaluate_when("not step.failed", {"step": {"failed": False}}) is True


def test_when_contains():
    from core.workflow.template import evaluate_when
    assert evaluate_when('output contains "active"', {"output": "service is active"}) is True
    assert evaluate_when('output contains "active"', {"output": "stopped"}) is False


def test_when_in_list():
    from core.workflow.template import evaluate_when
    ctx = {"sev": "high"}
    assert evaluate_when('sev in ["low", "high"]', ctx) is True
    assert evaluate_when('sev in ["low", "medium"]', ctx) is False


# ─── loader ─────────────────────────────────────────────────────────

def test_validate_rejects_missing_steps():
    from core.workflow.loader import validate_workflow
    errs = validate_workflow({"name": "x"})
    assert any("steps" in e for e in errs)


def test_validate_catches_unknown_step_type():
    from core.workflow.loader import validate_workflow
    errs = validate_workflow({
        "name": "x",
        "steps": [{"id": "a", "type": "wat"}],
    })
    assert any("invalid type" in e for e in errs)


def test_validate_catches_duplicate_ids():
    from core.workflow.loader import validate_workflow
    errs = validate_workflow({
        "name": "x",
        "steps": [
            {"id": "a", "type": "set", "values": {"x": 1}},
            {"id": "a", "type": "set", "values": {"y": 2}},
        ],
    })
    assert any("duplicate" in e for e in errs)


def test_validate_tool_requires_tool_name():
    from core.workflow.loader import validate_workflow
    errs = validate_workflow({
        "name": "x",
        "steps": [{"id": "a", "type": "tool"}],
    })
    assert any("tool" in e for e in errs)


def test_validate_ok_for_example_workflows():
    from core.workflow.loader import load_workflow, validate_workflow
    for name in ("disk_full_remediation", "service_health_check", "daily_health_report"):
        wf = load_workflow(name)
        errs = validate_workflow(wf)
        assert errs == [], f"{name} had errors: {errs}"


# ─── engine — pure step execution (no MCP / no AI) ──────────────────

def _isolate_runs():
    from core.workflow import storage
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json").name
    storage.RUNS_FILE = Path(tmp)
    return Path(tmp)


def test_engine_set_and_notify():
    _isolate_runs()
    from core.workflow import WorkflowEngine, get_run
    wf = {
        "name": "test_set_notify",
        "steps": [
            {"id": "vars", "type": "set", "values": {"who": "world"}},
            {"id": "say", "type": "notify", "channel": "log",
             "message": "hello {{ who }}"},
        ],
    }
    eng = WorkflowEngine()
    run_id = eng.start(wf)
    run = get_run(run_id)
    assert run["status"] == "completed"
    statuses = [h["status"] for h in run["history"]]
    assert statuses == ["completed", "completed"]
    assert run["history"][1]["result"]["message"] == "hello world"


def test_engine_branch_then():
    _isolate_runs()
    from core.workflow import WorkflowEngine, get_run
    wf = {
        "name": "test_branch",
        "steps": [
            {"id": "init", "type": "set", "values": {"flag": True}},
            {"id": "br", "type": "branch", "when": "flag",
             "then": [{"id": "yes", "type": "notify", "channel": "log",
                       "message": "yes branch"}],
             "else": [{"id": "no", "type": "notify", "channel": "log",
                       "message": "no branch"}]},
        ],
    }
    eng = WorkflowEngine()
    run_id = eng.start(wf)
    hist_steps = [h["step"] for h in get_run(run_id)["history"]]
    assert "yes" in hist_steps and "no" not in hist_steps


def test_engine_branch_else():
    _isolate_runs()
    from core.workflow import WorkflowEngine, get_run
    wf = {
        "name": "test_branch_else",
        "steps": [
            {"id": "init", "type": "set", "values": {"flag": False}},
            {"id": "br", "type": "branch", "when": "flag",
             "then": [{"id": "yes", "type": "notify", "channel": "log",
                       "message": "yes"}],
             "else": [{"id": "no", "type": "notify", "channel": "log",
                       "message": "no"}]},
        ],
    }
    eng = WorkflowEngine()
    run_id = eng.start(wf)
    hist_steps = [h["step"] for h in get_run(run_id)["history"]]
    assert "no" in hist_steps and "yes" not in hist_steps


def test_engine_skip_when_false():
    _isolate_runs()
    from core.workflow import WorkflowEngine, get_run
    wf = {
        "name": "test_skip",
        "steps": [
            {"id": "init", "type": "set", "values": {"go": False}},
            {"id": "maybe", "type": "notify", "channel": "log",
             "message": "should be skipped", "when": "go"},
            {"id": "always", "type": "notify", "channel": "log",
             "message": "always runs"},
        ],
    }
    eng = WorkflowEngine()
    run_id = eng.start(wf)
    hist = {h["step"]: h["status"] for h in get_run(run_id)["history"]}
    assert hist["maybe"] == "skipped"
    assert hist["always"] == "completed"


def test_engine_wait_approval_pauses_and_resumes():
    _isolate_runs()
    from core.workflow import (
        WorkflowEngine, get_run, STATUS_WAITING_APPROVAL, STATUS_COMPLETED,
    )
    wf = {
        "name": "test_approval",
        "steps": [
            {"id": "ask", "type": "wait_approval", "prompt": "Proceed?"},
            {"id": "after", "type": "notify", "channel": "log",
             "message": "approved!"},
        ],
    }
    eng = WorkflowEngine()
    run_id = eng.start(wf)
    run = get_run(run_id)
    assert run["status"] == STATUS_WAITING_APPROVAL

    eng.resume(run_id, approval=True)
    run = get_run(run_id)
    assert run["status"] == STATUS_COMPLETED
    statuses_by_step = {h["step"]: h["status"] for h in run["history"]}
    assert statuses_by_step["ask"] == "approved"
    assert statuses_by_step["after"] == "completed"


def test_engine_wait_approval_reject_cancels():
    _isolate_runs()
    from core.workflow import WorkflowEngine, get_run, STATUS_CANCELLED
    wf = {
        "name": "test_reject",
        "steps": [
            {"id": "ask", "type": "wait_approval", "prompt": "Proceed?"},
            {"id": "after", "type": "notify", "channel": "log",
             "message": "should not run"},
        ],
    }
    eng = WorkflowEngine()
    run_id = eng.start(wf)
    eng.resume(run_id, approval=False)
    run = get_run(run_id)
    assert run["status"] == STATUS_CANCELLED
    assert all(h["step"] != "after" for h in run["history"])


def test_engine_on_error_continue():
    """A failing step with on_error: continue should not abort the run."""
    _isolate_runs()
    from core.workflow import WorkflowEngine, get_run, STATUS_COMPLETED
    wf = {
        "name": "test_on_error",
        "steps": [
            # An unknown step type would raise — use a metric_check we patch
            {"id": "bad", "type": "set", "values": "not_a_dict",
             "on_error": "continue"},
            {"id": "ok", "type": "notify", "channel": "log",
             "message": "still ran"},
        ],
    }
    # The first step will fail validation inside the handler? Actually set
    # accepts dicts; passing a string raises AttributeError inside handler.
    eng = WorkflowEngine()
    run_id = eng.start(wf)
    run = get_run(run_id)
    assert run["status"] == STATUS_COMPLETED
    statuses = {h["step"]: h["status"] for h in run["history"]}
    assert statuses["bad"] == "error"
    assert statuses["ok"] == "completed"


def test_engine_tool_step_calls_dispatch(monkeypatch):
    """tool step uses _dispatch_tool — verify it's invoked correctly."""
    _isolate_runs()
    from core.workflow import WorkflowEngine, get_run

    calls = []
    def fake_dispatch(name, inp, connections):
        calls.append((name, inp))
        return "OK output"

    # Patch the dispatcher used inside the engine module
    import core.agent_loop as al
    monkeypatch.setattr(al, "_dispatch_tool", fake_dispatch)

    wf = {
        "name": "test_tool",
        "steps": [
            {"id": "do", "type": "tool", "tool": "any_future_mcp",
             "input": {"command": "uname -a", "target_host": "10.0.0.1"}},
        ],
    }
    run_id = WorkflowEngine().start(wf, connections={"linux": {"ip": "10.0.0.1"}})
    run = get_run(run_id)
    assert run["status"] == "completed"
    assert calls and calls[0][0] == "any_future_mcp"
    assert calls[0][1]["command"] == "uname -a"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
