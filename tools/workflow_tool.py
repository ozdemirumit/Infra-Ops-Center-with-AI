"""
workflow_ops — built-in MCP tool that lets Claude (and any other MCP-aware
caller) drive the workflow engine from chat.

Actions:
    list                — show available workflow definitions
    list_runs           — show recent runs
    run                 — start a workflow (supports dry_run)
    status              — get a run's current state + history
    approve             — resume a paused run after approval
    reject              — cancel a paused run
    cancel              — cancel a running workflow

All execution happens through the same engine the UI uses, so behaviour
is identical regardless of trigger source.
"""

import json
from logging_config.logger import get_logger

logger = get_logger("workflow_tool")


WORKFLOW_OPS_TOOL = {
    "name": "workflow_ops",
    "description": (
        "Control multi-step workflows: list available workflows, launch a run "
        "(optionally dry-run), check status, approve or reject paused steps. "
        "Every workflow tool-step calls other MCPs internally — so use this "
        "when an investigation or remediation needs more than one tool call "
        "with branching / approval logic. Pass actions as `action` and "
        "additional parameters as top-level fields."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": (
                    "One of: list, list_runs, run, status, approve, reject, cancel."
                ),
                "enum": ["list", "list_runs", "run", "status",
                         "approve", "reject", "cancel"],
            },
            "workflow": {
                "type": "string",
                "description": "Workflow name (without .yaml). Required for `run`.",
            },
            "inputs": {
                "type": "object",
                "description": "Input overrides for the workflow (free-form mapping).",
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "If true, the workflow runs in simulation mode — no real MCP "
                    "calls, no LLM, no notifications. Strongly recommended for the "
                    "first launch of an unfamiliar workflow."
                ),
            },
            "run_id": {
                "type": "string",
                "description": "Run identifier. Required for status/approve/reject/cancel.",
            },
            "note": {
                "type": "string",
                "description": "Optional note recorded with an approve/reject decision.",
            },
            "limit": {
                "type": "integer",
                "description": "list_runs only — cap on number of runs returned (default 20).",
            },
        },
        "required": ["action"],
    },
}


# ─── Dispatcher ────────────────────────────────────────────────────

def execute_workflow_action(tool_input: dict, connections: dict | None = None) -> str:
    """Entry point used by core.agent_loop._dispatch_tool."""
    action = (tool_input.get("action") or "").strip().lower()
    if not action:
        return "❌ workflow_ops: `action` is required."

    try:
        if action == "list":
            return _action_list()
        if action == "list_runs":
            return _action_list_runs(int(tool_input.get("limit", 20)))
        if action == "run":
            return _action_run(
                workflow=tool_input.get("workflow", ""),
                inputs=tool_input.get("inputs") or {},
                dry_run=bool(tool_input.get("dry_run", False)),
                connections=connections or {},
            )
        if action == "status":
            return _action_status(tool_input.get("run_id", ""))
        if action == "approve":
            return _action_decide(tool_input.get("run_id", ""),
                                  approve=True,
                                  note=tool_input.get("note", ""))
        if action == "reject":
            return _action_decide(tool_input.get("run_id", ""),
                                  approve=False,
                                  note=tool_input.get("note", ""))
        if action == "cancel":
            return _action_cancel(tool_input.get("run_id", ""))
        return f"❌ workflow_ops: unknown action '{action}'."
    except Exception as e:
        logger.exception("workflow_ops failed")
        return f"❌ workflow_ops error: {type(e).__name__}: {e}"


# ─── Action implementations ───────────────────────────────────────

def _action_list() -> str:
    from core.workflow import list_workflows
    rows = list_workflows()
    if not rows:
        return "No workflows registered. Drop YAML files into /workflows/."

    out = ["Available workflows:"]
    for r in rows:
        line = f"- {r['name']} ({r['step_count']} steps)"
        if r["description"]:
            desc = r["description"].strip().splitlines()[0][:120]
            line += f" — {desc}"
        trig = r.get("trigger") or {}
        if trig.get("type"):
            line += f"  [trigger: {trig['type']}]"
        if r["errors"]:
            line += f"  ⚠️ ERRORS: {'; '.join(r['errors'])}"
        out.append(line)
    return "\n".join(out)


def _action_list_runs(limit: int) -> str:
    from core.workflow import list_runs
    runs = list_runs()[:max(1, min(limit, 100))]
    if not runs:
        return "No runs yet."
    out = [f"Showing {len(runs)} most recent run(s):"]
    for r in runs:
        dry = " [DRY]" if r.get("dry_run") else ""
        out.append(
            f"- {r['id']}  {r['status']}{dry}  workflow={r['workflow_name']}  "
            f"started={r['created_at'][:16]}  by={r.get('triggered_by', '?')}"
        )
    return "\n".join(out)


def _action_run(workflow: str, inputs: dict, dry_run: bool,
                connections: dict) -> str:
    if not workflow:
        return "❌ `workflow` is required for action=run."

    from core.workflow import load_workflow, validate_workflow, WorkflowEngine
    try:
        wf = load_workflow(workflow)
    except FileNotFoundError:
        return (f"❌ Workflow '{workflow}' not found. "
                f"Use action=list to see what's available.")

    errs = validate_workflow(wf)
    if errs:
        return ("❌ Workflow has validation errors — cannot run:\n  - "
                + "\n  - ".join(errs))

    engine = WorkflowEngine()
    run_id = engine.start(
        workflow=wf, inputs=inputs, connections=connections,
        triggered_by="mcp:workflow_ops" + ("/dry-run" if dry_run else ""),
        dry_run=dry_run,
    )

    # Re-fetch to report current status
    from core.workflow import get_run
    run = get_run(run_id) or {}
    msg = [
        f"{'🧪 Dry-run' if dry_run else 'Workflow'} '{workflow}' started.",
        f"  run_id: {run_id}",
        f"  status: {run.get('status', '?')}",
    ]
    if run.get("status") == "waiting_approval":
        idx = run.get("current_index", 0)
        steps = run.get("workflow_steps", [])
        if idx < len(steps):
            from core.workflow.template import render
            step = render(steps[idx], run.get("context", {}))
            msg.append(f"  awaiting approval at step '{steps[idx].get('id')}'")
            msg.append(f"  prompt: {step.get('prompt', '')[:300]}")
            msg.append(
                f"  → call workflow_ops again with action=approve "
                f"(or reject) and run_id={run_id} to continue."
            )
    elif run.get("status") == "completed":
        msg.append("  ✅ completed in a single sweep.")
    return "\n".join(msg)


def _action_status(run_id: str) -> str:
    if not run_id:
        return "❌ `run_id` is required for action=status."
    from core.workflow import get_run
    run = get_run(run_id)
    if not run:
        return f"❌ run not found: {run_id}"

    out = [
        f"Run {run_id}",
        f"  workflow: {run['workflow_name']}",
        f"  status:   {run['status']}" + (" [DRY-RUN]" if run.get('dry_run') else ""),
        f"  triggered_by: {run.get('triggered_by', '?')}",
        f"  created: {run['created_at'][:19]}   updated: {run['updated_at'][:19]}",
    ]
    if run.get("error"):
        out.append(f"  error: {run['error']}")

    inputs = run.get("context", {}).get("inputs", {})
    if inputs:
        out.append("  inputs: " + json.dumps(inputs)[:300])

    history = run.get("history", [])
    if history:
        out.append("\nStep history:")
        for h in history:
            short = {"completed": "✓", "skipped": "·", "waiting": "⏸",
                     "approved": "👍", "rejected": "👎", "error": "✗",
                     "failed": "✗"}.get(h.get("status"), "?")
            out.append(f"  {short} {h['step']} ({h['type']}) "
                       f"— {h['status']}  {h.get('duration_ms', 0)}ms")

    if run["status"] == "waiting_approval":
        idx = run.get("current_index", 0)
        steps = run.get("workflow_steps", [])
        if idx < len(steps):
            from core.workflow.template import render
            step = render(steps[idx], run.get("context", {}))
            out.append("\n⏸  Awaiting approval:")
            out.append(f"   prompt: {step.get('prompt', '')[:400]}")
            out.append(f"   risk:   {step.get('risk', 'medium')}")
            out.append(
                f"   → workflow_ops action=approve|reject run_id={run_id}"
            )
    return "\n".join(out)


def _action_decide(run_id: str, approve: bool, note: str) -> str:
    if not run_id:
        return "❌ `run_id` is required."
    from core.workflow import WorkflowEngine, get_run
    before = get_run(run_id)
    if not before:
        return f"❌ run not found: {run_id}"
    if before["status"] != "waiting_approval":
        return (f"⚠️ run is in status '{before['status']}' — only "
                f"'waiting_approval' runs can be approved/rejected.")

    eng = WorkflowEngine()
    eng.resume(run_id, approval=approve, approval_note=note)
    after = get_run(run_id)
    verb = "approved" if approve else "rejected"
    return (
        f"Run {run_id} {verb}.\n"
        f"  status: {after['status']}\n"
        f"  steps run: {len(after.get('history', []))}"
    )


def _action_cancel(run_id: str) -> str:
    if not run_id:
        return "❌ `run_id` is required."
    from core.workflow import WorkflowEngine, get_run
    run = get_run(run_id)
    if not run:
        return f"❌ run not found: {run_id}"
    if run["status"] in ("completed", "failed", "cancelled"):
        return f"⚠️ run already in terminal state: {run['status']}"
    WorkflowEngine().cancel(run_id)
    return f"Run {run_id} cancelled."
