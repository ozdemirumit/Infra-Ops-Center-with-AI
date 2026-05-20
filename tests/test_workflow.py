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
            # allow_missing: opts out of capability-gap pause so the
            # dispatcher is called regardless of registry membership
            {"id": "do", "type": "tool", "tool": "any_future_mcp",
             "allow_missing": True,
             "input": {"command": "uname -a", "target_host": "10.0.0.1"}},
        ],
    }
    run_id = WorkflowEngine().start(wf, connections={"linux": {"ip": "10.0.0.1"}})
    run = get_run(run_id)
    assert run["status"] == "completed"
    assert calls and calls[0][0] == "any_future_mcp"
    assert calls[0][1]["command"] == "uname -a"


# ─── Dry-run mode ───────────────────────────────────────────────────

def test_dry_run_tool_does_not_call_dispatch(monkeypatch):
    """tool step must NOT invoke _dispatch_tool in dry-run mode."""
    _isolate_runs()
    from core.workflow import WorkflowEngine, get_run

    called = []
    def boom(name, inp, connections):
        called.append((name, inp))
        raise RuntimeError("dispatch should not run in dry-run!")

    import core.agent_loop as al
    monkeypatch.setattr(al, "_dispatch_tool", boom)

    wf = {
        "name": "test_dry_tool",
        "steps": [
            {"id": "do", "type": "tool", "tool": "some_mcp",
             "input": {"command": "rm -rf /", "target_host": "srv01"}},
        ],
    }
    run_id = WorkflowEngine().start(wf, dry_run=True)
    run = get_run(run_id)
    assert run["status"] == "completed"
    assert called == []  # dispatch must not have been invoked

    result = run["history"][0]["result"]
    assert result["dry_run"] is True
    assert "DRY-RUN" in result["output"]
    assert "some_mcp" in result["output"]


def test_dry_run_agent_skips_llm():
    """agent step must produce a mock summary without touching the proxy."""
    _isolate_runs()
    from core.workflow import WorkflowEngine, get_run

    wf = {
        "name": "test_dry_agent",
        "steps": [
            {"id": "investigate", "type": "agent",
             "prompt": "What is wrong with srv01?", "max_steps": 5},
        ],
    }
    # If the agent actually called AIProxy() with no API key, the test
    # would either fail or make a network call. Dry-run guarantees neither.
    run_id = WorkflowEngine().start(wf, dry_run=True)
    run = get_run(run_id)
    assert run["status"] == "completed"
    res = run["history"][0]["result"]
    assert res["dry_run"] is True
    assert res["turns"] == 0
    assert "What is wrong with srv01" in res["summary"]


def test_dry_run_wait_approval_auto_approves():
    """wait_approval must auto-pass in dry-run so subsequent steps still run."""
    _isolate_runs()
    from core.workflow import WorkflowEngine, get_run

    wf = {
        "name": "test_dry_approval",
        "steps": [
            {"id": "ask", "type": "wait_approval", "prompt": "Risky thing?",
             "risk": "high"},
            {"id": "after", "type": "notify", "channel": "log",
             "message": "approved"},
        ],
    }
    run_id = WorkflowEngine().start(wf, dry_run=True)
    run = get_run(run_id)
    assert run["status"] == "completed"
    statuses = {h["step"]: h["status"] for h in run["history"]}
    assert statuses["ask"] == "completed"
    assert statuses["after"] == "completed"
    assert run["history"][0]["result"]["dry_run"] is True


def test_dry_run_notify_does_not_send():
    """notify must not invoke any real channel in dry-run."""
    _isolate_runs()
    from core.workflow import WorkflowEngine, get_run

    wf = {
        "name": "test_dry_notify",
        "steps": [
            {"id": "say", "type": "notify", "channel": "webhook",
             "url": "http://invalid.example.com/this-would-fail",
             "message": "should never send"},
        ],
    }
    # If notify actually fired a webhook, httpx.post would raise / timeout.
    # In dry-run it must short-circuit cleanly.
    run_id = WorkflowEngine().start(wf, dry_run=True)
    run = get_run(run_id)
    assert run["status"] == "completed"
    res = run["history"][0]["result"]
    assert res["sent"] is False
    assert res["dry_run"] is True


def test_dry_run_sleep_does_not_sleep():
    """sleep must not actually pause the process in dry-run."""
    _isolate_runs()
    import time as _t
    from core.workflow import WorkflowEngine, get_run

    wf = {
        "name": "test_dry_sleep",
        "steps": [
            {"id": "pause", "type": "sleep", "seconds": 30},
        ],
    }
    t0 = _t.time()
    run_id = WorkflowEngine().start(wf, dry_run=True)
    elapsed = _t.time() - t0
    assert elapsed < 5.0, f"dry-run sleep blocked for {elapsed}s"
    res = get_run(run_id)["history"][0]["result"]
    assert res["slept"] == 0
    assert res["would_sleep"] == 30


def test_real_run_default_is_not_dry():
    """Default mode is real — dry_run flag must be False unless asked."""
    _isolate_runs()
    from core.workflow import WorkflowEngine, get_run
    wf = {"name": "x", "steps": [{"id": "v", "type": "set", "values": {"a": 1}}]}
    run_id = WorkflowEngine().start(wf)
    assert get_run(run_id).get("dry_run") is False


# ─── manual_instruction step ────────────────────────────────────────

def test_manual_instruction_pauses_for_confirmation():
    _isolate_runs()
    from core.workflow import (
        WorkflowEngine, get_run, STATUS_WAITING_APPROVAL, STATUS_COMPLETED,
    )
    wf = {
        "name": "test_manual",
        "steps": [
            {"id": "human_step", "type": "manual_instruction",
             "title": "Replace failed disk",
             "body": "1. Slot 4\n2. Hot-swap\n3. Wait for rebuild",
             "why": "No MCP can hot-swap hardware.",
             "risk": "high"},
            {"id": "after", "type": "notify", "channel": "log",
             "message": "rebuild started"},
        ],
    }
    eng = WorkflowEngine()
    run_id = eng.start(wf)
    run = get_run(run_id)
    assert run["status"] == STATUS_WAITING_APPROVAL
    last = run["history"][-1]["result"]
    assert last["kind"] == "manual_instruction"
    assert last["title"] == "Replace failed disk"
    assert "Hot-swap" in last["body"]

    eng.resume(run_id, approval=True, approval_note="done")
    run = get_run(run_id)
    assert run["status"] == STATUS_COMPLETED


def test_manual_instruction_dry_run_auto_confirms():
    _isolate_runs()
    from core.workflow import WorkflowEngine, get_run
    wf = {
        "name": "test_manual_dry",
        "steps": [
            {"id": "step", "type": "manual_instruction",
             "body": "do the thing"},
            {"id": "next", "type": "notify", "channel": "log",
             "message": "ok"},
        ],
    }
    run_id = WorkflowEngine().start(wf, dry_run=True)
    run = get_run(run_id)
    assert run["status"] == "completed"
    statuses = [h["status"] for h in run["history"]]
    assert statuses == ["completed", "completed"]


def test_manual_instruction_validator_requires_body():
    from core.workflow.loader import validate_workflow
    errs = validate_workflow({
        "name": "x",
        "steps": [{"id": "a", "type": "manual_instruction", "title": "no body"}],
    })
    assert any("manual_instruction" in e for e in errs)


# ─── Capability-gap detection ──────────────────────────────────────

def test_unknown_tool_pauses_as_capability_gap(monkeypatch):
    """A `tool:` step naming an MCP not in the registry must pause as
    a manual-instruction, NOT call _dispatch_tool, NOT silently fail."""
    _isolate_runs()
    from core.workflow import WorkflowEngine, get_run, STATUS_WAITING_APPROVAL

    # Force the live registry to NOT include the made-up tool name
    import tools.registry as reg
    monkeypatch.setattr(reg, "get_active_tools",
                        lambda: [{"name": "linux_ops", "description": "..."}])

    called = []
    import core.agent_loop as al
    monkeypatch.setattr(al, "_dispatch_tool",
                        lambda *a, **kw: called.append(a) or "should never run")

    wf = {
        "name": "test_gap",
        "steps": [
            {"id": "futuristic", "type": "tool",
             "tool": "quantum_router_ops",
             "input": {"command": "entangle", "target_host": "10.0.0.1"}},
            {"id": "after", "type": "notify", "channel": "log",
             "message": "should only run after manual confirm"},
        ],
    }
    eng = WorkflowEngine()
    run_id = eng.start(wf)
    run = get_run(run_id)

    # Paused at the unknown tool
    assert run["status"] == STATUS_WAITING_APPROVAL
    assert called == []  # dispatcher NOT invoked
    last = run["history"][-1]["result"]
    assert last["kind"] == "capability_gap"
    assert last["missing_tool"] == "quantum_router_ops"
    assert "linux_ops" in last["available_mcps"]
    assert "quantum_router_ops" in last["instructions"]
    assert "10.0.0.1" in last["instructions"]

    # Resume — workflow proceeds
    eng.resume(run_id, approval=True, approval_note="did it manually")
    run = get_run(run_id)
    assert run["status"] == "completed"
    steps_done = [h["step"] for h in run["history"]]
    assert "after" in steps_done


def test_known_tool_does_not_trigger_gap(monkeypatch):
    """A tool in the registry must execute normally — no gap pause."""
    _isolate_runs()
    from core.workflow import WorkflowEngine, get_run

    import tools.registry as reg
    monkeypatch.setattr(reg, "get_active_tools",
                        lambda: [{"name": "linux_ops", "description": "..."}])

    import core.agent_loop as al
    monkeypatch.setattr(al, "_dispatch_tool",
                        lambda name, inp, conns: f"OK ran {name}")

    wf = {
        "name": "test_known",
        "steps": [
            {"id": "do", "type": "tool", "tool": "linux_ops",
             "input": {"command": "uptime"}},
        ],
    }
    run_id = WorkflowEngine().start(wf)
    run = get_run(run_id)
    assert run["status"] == "completed"
    res = run["history"][0]["result"]
    assert res.get("kind") != "capability_gap"
    assert res["output"] == "OK ran linux_ops"


def test_allow_missing_flag_bypasses_gap(monkeypatch):
    """`allow_missing: true` on a tool step skips gap detection and
    dispatches anyway (escape hatch for custom dispatchers)."""
    _isolate_runs()
    from core.workflow import WorkflowEngine, get_run

    import tools.registry as reg
    monkeypatch.setattr(reg, "get_active_tools", lambda: [])

    import core.agent_loop as al
    monkeypatch.setattr(al, "_dispatch_tool",
                        lambda n, i, c: f"dispatched {n} anyway")

    wf = {
        "name": "test_bypass",
        "steps": [
            {"id": "exotic", "type": "tool", "tool": "weird_mcp",
             "allow_missing": True, "input": {"command": "ping"}},
        ],
    }
    run_id = WorkflowEngine().start(wf)
    run = get_run(run_id)
    assert run["status"] == "completed"
    assert "dispatched weird_mcp" in run["history"][0]["result"]["output"]


# ─── workflow_ops MCP tool ──────────────────────────────────────────

def test_workflow_ops_list_action():
    from tools.workflow_tool import execute_workflow_action
    out = execute_workflow_action({"action": "list"})
    # Three shipped example workflows must appear
    assert "disk_full_remediation" in out
    assert "service_health_check" in out
    assert "daily_health_report" in out


def test_workflow_ops_missing_action():
    from tools.workflow_tool import execute_workflow_action
    out = execute_workflow_action({})
    assert out.startswith("❌")
    assert "action" in out.lower()


def test_workflow_ops_unknown_action():
    from tools.workflow_tool import execute_workflow_action
    out = execute_workflow_action({"action": "destroy_everything"})
    assert "unknown action" in out.lower()


def test_workflow_ops_run_status_approve_full_cycle(monkeypatch):
    """Full lifecycle through the MCP surface only — no UI involvement."""
    _isolate_runs()
    from tools.workflow_tool import execute_workflow_action
    from core.workflow import storage

    # Save a tiny test workflow to the loader's directory
    import yaml as _y
    from core.workflow.loader import WORKFLOWS_DIR
    yaml_text = _y.dump({
        "name": "_test_via_mcp",
        "steps": [
            {"id": "ask", "type": "wait_approval", "prompt": "go?",
             "risk": "low"},
            {"id": "done", "type": "notify", "channel": "log",
             "message": "approved via MCP"},
        ],
    })
    wf_path = WORKFLOWS_DIR / "_test_via_mcp.yaml"
    wf_path.write_text(yaml_text, encoding="utf-8")
    try:
        # 1. RUN
        run_out = execute_workflow_action({
            "action": "run", "workflow": "_test_via_mcp",
        })
        assert "started" in run_out
        # Extract run_id
        import re
        m = re.search(r"run_id:\s*(\w+)", run_out)
        assert m, f"no run_id in: {run_out}"
        run_id = m.group(1)
        assert "awaiting approval" in run_out

        # 2. STATUS
        status_out = execute_workflow_action({"action": "status", "run_id": run_id})
        assert "waiting_approval" in status_out
        assert "Awaiting approval" in status_out

        # 3. APPROVE
        appr_out = execute_workflow_action({
            "action": "approve", "run_id": run_id, "note": "ok via mcp",
        })
        assert "approved" in appr_out
        assert "completed" in appr_out

        # 4. Final status reflects completion
        final = execute_workflow_action({"action": "status", "run_id": run_id})
        assert "completed" in final
    finally:
        wf_path.unlink(missing_ok=True)


def test_workflow_ops_reject_cancels():
    _isolate_runs()
    from tools.workflow_tool import execute_workflow_action
    import yaml as _y
    from core.workflow.loader import WORKFLOWS_DIR

    yaml_text = _y.dump({
        "name": "_test_reject",
        "steps": [
            {"id": "ask", "type": "wait_approval", "prompt": "go?"},
            {"id": "after", "type": "notify", "channel": "log", "message": "no"},
        ],
    })
    wf_path = WORKFLOWS_DIR / "_test_reject.yaml"
    wf_path.write_text(yaml_text, encoding="utf-8")
    try:
        run_out = execute_workflow_action({"action": "run", "workflow": "_test_reject"})
        import re
        m = re.search(r"run_id:\s*(\w+)", run_out)
        run_id = m.group(1)

        out = execute_workflow_action({"action": "reject", "run_id": run_id})
        assert "rejected" in out
        assert "cancelled" in out
    finally:
        wf_path.unlink(missing_ok=True)


def test_workflow_ops_run_unknown_workflow():
    from tools.workflow_tool import execute_workflow_action
    out = execute_workflow_action({"action": "run", "workflow": "_nope_"})
    assert out.startswith("❌")
    assert "not found" in out


def test_workflow_ops_status_unknown_run():
    from tools.workflow_tool import execute_workflow_action
    out = execute_workflow_action({"action": "status", "run_id": "deadbeef"})
    assert "not found" in out


def test_workflow_ops_run_supports_dry_run():
    _isolate_runs()
    from tools.workflow_tool import execute_workflow_action
    out = execute_workflow_action({
        "action": "run", "workflow": "daily_health_report",
        "dry_run": True,
    })
    assert "Dry-run" in out or "started" in out


def test_workflow_ops_registered_in_registry():
    """Verify workflow_ops is exposed as a built-in MCP — Claude can find it."""
    from tools.registry import get_active_tools
    names = {t["name"] for t in get_active_tools()}
    assert "workflow_ops" in names


def test_workflow_ops_dispatched_through_agent_loop():
    """The agent loop dispatcher must route 'workflow_ops' correctly."""
    from core.agent_loop import _dispatch_tool
    out = _dispatch_tool("workflow_ops", {"action": "list"}, {})
    assert "Available workflows" in out or "No workflows" in out


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
