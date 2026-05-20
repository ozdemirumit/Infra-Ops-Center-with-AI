"""
🔀 Workflows — multi-step automation with conditional logic and approvals.

Browse workflow library, launch runs, review history, approve paused steps,
and edit/create YAML workflow definitions. Works with any MCP tool in the
registry — tool names are resolved at execution time.
"""

import os
import json
from datetime import datetime
from pathlib import Path

import streamlit as st

from auth.authenticator import check_auth
from ui.sidebar import render_sidebar
from core.workflow import (
    WorkflowEngine, load_workflow, list_workflows,
    list_runs, get_run, delete_run,
    WORKFLOWS_DIR, STATUS_WAITING_APPROVAL, STATUS_RUNNING,
    STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED,
    reload_workflow_jobs, get_scheduled_jobs_info,
)
from core.workflow.loader import save_workflow, delete_workflow, validate_workflow

st.set_page_config(page_title="Workflows", page_icon="🔀", layout="wide")

css_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui", "style.css")
if os.path.exists(css_path):
    with open(css_path, "r", encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

if not check_auth():
    st.stop()

connections = render_sidebar()
is_admin = st.session_state.get("role") == "admin"

st.title("🔀 Workflows")
st.markdown(
    "Multi-step automation with conditional branches, human approvals, and "
    "auto-generated runbooks. Tool steps work with **any MCP** in the registry — "
    "no engine changes needed when you add new ones."
)

STATUS_ICON = {
    "pending": "⏳", "running": "🔄", "waiting_approval": "⏸️",
    "completed": "✅", "failed": "❌", "cancelled": "🚫",
}


def _status_chip(status: str) -> str:
    return f"{STATUS_ICON.get(status, '❔')} {status}"


tab_lib, tab_runs, tab_edit = st.tabs([
    "📚 Library", "🏃 Runs", "✏️ Editor",
])

# ═══════════════════════════════════════════════════════════════
# TAB 1 — LIBRARY
# ═══════════════════════════════════════════════════════════════

with tab_lib:
    # ── Scheduled-workflow summary ─────────────────────────────
    schedule_rows = get_scheduled_jobs_info()
    schedule_map = {r["workflow"]: r for r in schedule_rows}

    with st.container(border=True):
        col_sched_info, col_sched_action = st.columns([4, 1])
        with col_sched_info:
            if schedule_rows:
                st.markdown(f"⏰ **{len(schedule_rows)} scheduled workflow(s)**")
                for row in schedule_rows:
                    nxt = row.get("next_run") or "?"
                    st.caption(
                        f"• `{row['workflow']}` — cron `{row['cron']}` · "
                        f"next: {nxt[:19] if nxt != '?' else '?'}"
                    )
            else:
                st.caption(
                    "⏰ No scheduled workflows yet. Add `trigger: { type: schedule, "
                    "cron: \"<expr>\" }` to a YAML to register one."
                )
        with col_sched_action:
            if st.button("🔄 Reload schedules", use_container_width=True,
                         help="Re-scan workflow YAMLs and refresh cron jobs"):
                n = reload_workflow_jobs()
                st.success(f"✅ {n} scheduled workflow(s) registered")
                st.rerun()

    workflows = list_workflows()
    if not workflows:
        st.info(f"No workflows yet. Drop YAML files into `{WORKFLOWS_DIR}` "
                "or use the Editor tab.")
    else:
        for wf_meta in workflows:
            errs = wf_meta["errors"]
            with st.container(border=True):
                col_info, col_actions = st.columns([4, 1])
                with col_info:
                    bad = "🔴 " if errs else ""
                    st.markdown(f"**{bad}{wf_meta['name']}**  "
                                f"<span style='color:#6b7690;font-size:0.75rem;'>"
                                f"`{wf_meta['file']}`</span>",
                                unsafe_allow_html=True)
                    if wf_meta["description"]:
                        st.caption(wf_meta["description"])
                    trig = wf_meta["trigger"] or {}
                    if trig:
                        st.caption(f"Trigger: `{trig.get('type', '?')}` "
                                   f"{json.dumps({k: v for k, v in trig.items() if k != 'type'})}")
                    sched = schedule_map.get(wf_meta["name"])
                    if sched:
                        nxt = (sched.get("next_run") or "?")[:19]
                        st.caption(
                            f"⏰ Scheduled — next run: {nxt}"
                        )
                    st.caption(f"Steps: **{wf_meta['step_count']}**")
                    if errs:
                        for e in errs:
                            st.error(f"⚠️ {e}")

                with col_actions:
                    if st.button("▶️ Run", key=f"run_{wf_meta['file']}",
                                 type="primary", use_container_width=True,
                                 disabled=bool(errs)):
                        st.session_state["_wf_to_launch"] = wf_meta["name"]
                        st.rerun()
                    if is_admin and st.button("🗑️", key=f"del_{wf_meta['file']}",
                                              use_container_width=True):
                        if st.session_state.get("_wf_confirm_del") == wf_meta["file"]:
                            delete_workflow(wf_meta["name"])
                            try:
                                reload_workflow_jobs()
                            except Exception:
                                pass
                            st.session_state.pop("_wf_confirm_del", None)
                            st.rerun()
                        else:
                            st.session_state["_wf_confirm_del"] = wf_meta["file"]
                            st.warning("Click 🗑️ again to confirm delete")

    # ── Launch form (shown when "Run" was clicked) ──
    launch = st.session_state.get("_wf_to_launch")
    if launch:
        st.divider()
        try:
            wf = load_workflow(launch)
        except Exception as e:
            st.error(f"Failed to load: {e}")
            st.session_state.pop("_wf_to_launch", None)
            st.stop()

        st.subheader(f"🚀 Launch: {wf['name']}")
        st.caption(wf.get("description", ""))

        defaults = wf.get("inputs", {}) or {}
        st.markdown("**Inputs** (override defaults if needed)")
        inputs_override: dict = {}
        with st.form("wf_launch_form"):
            for k, v in defaults.items():
                inputs_override[k] = st.text_input(k, value=str(v),
                                                   key=f"in_{launch}_{k}")
            # Allow extra
            extra_raw = st.text_area(
                "Extra inputs (JSON)", value="{}", height=80,
                help="Add inputs not pre-declared in the YAML.",
            )
            dry_run = st.checkbox(
                "🧪 Dry-run (simulate — no real tool calls, no LLM, no notifications)",
                value=False,
                help=(
                    "Walks the workflow end-to-end with every side-effecting step "
                    "mocked. Use it to verify control flow and step results before "
                    "letting the workflow loose for real."
                ),
            )
            col_go, col_cancel = st.columns([1, 1])
            with col_go:
                go = st.form_submit_button("▶️ Start run", type="primary",
                                           use_container_width=True)
            with col_cancel:
                cancel = st.form_submit_button("Cancel", use_container_width=True)

        if cancel:
            st.session_state.pop("_wf_to_launch", None)
            st.rerun()

        if go:
            try:
                extra = json.loads(extra_raw) if extra_raw.strip() else {}
            except Exception:
                extra = {}
                st.warning("Could not parse extra inputs; ignored.")
            final_inputs = {**defaults, **inputs_override, **extra}
            engine = WorkflowEngine()
            triggered = st.session_state.get("username", "manual")
            if dry_run:
                triggered = f"{triggered}/dry-run"
            run_id = engine.start(
                wf, inputs=final_inputs, connections=connections,
                triggered_by=triggered, dry_run=dry_run,
            )
            st.success(
                f"{'🧪 Dry-run' if dry_run else 'Run'} started: `{run_id}`"
            )
            st.session_state.pop("_wf_to_launch", None)
            st.session_state["_wf_view_run"] = run_id
            st.rerun()


# ═══════════════════════════════════════════════════════════════
# TAB 2 — RUNS
# ═══════════════════════════════════════════════════════════════

with tab_runs:
    runs = list_runs()
    if not runs:
        st.info("No runs yet. Launch one from the Library tab.")
    else:
        # Stats
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total", len(runs))
        c2.metric("Running", sum(1 for r in runs if r["status"] == STATUS_RUNNING))
        c3.metric("Waiting approval", sum(1 for r in runs
                                          if r["status"] == STATUS_WAITING_APPROVAL))
        c4.metric("Completed", sum(1 for r in runs if r["status"] == STATUS_COMPLETED))

        st.divider()

        view_id = st.session_state.get("_wf_view_run")

        # Filter
        show_dry = st.checkbox("Show dry-runs", value=True, key="show_dry_filter")
        if not show_dry:
            runs = [r for r in runs if not r.get("dry_run")]

        for r in runs:
            opened = r["id"] == view_id
            status = r["status"]
            dry_tag = "  🧪 DRY-RUN" if r.get("dry_run") else ""
            with st.expander(
                f"{_status_chip(status)}{dry_tag}  **{r['workflow_name']}**  "
                f"·  `{r['id']}`  ·  {r['created_at'][:16]}",
                expanded=opened,
            ):
                if r.get("dry_run"):
                    st.info(
                        "🧪 **Dry-run** — every side-effecting step was mocked. "
                        "No MCP calls, no LLM, no notifications were sent."
                    )
                col_meta, col_btn = st.columns([3, 1])
                with col_meta:
                    st.caption(f"Triggered by: {r.get('triggered_by', '?')}  ·  "
                               f"Updated: {r['updated_at'][:19]}")
                    if r.get("error"):
                        st.error(r["error"])
                    inputs = r.get("context", {}).get("inputs", {})
                    if inputs:
                        st.markdown("**Inputs:**")
                        st.code(json.dumps(inputs, indent=2), language="json")

                with col_btn:
                    if status in (STATUS_RUNNING, STATUS_WAITING_APPROVAL):
                        if st.button("🚫 Cancel", key=f"can_{r['id']}",
                                     use_container_width=True):
                            WorkflowEngine().cancel(r["id"])
                            st.rerun()
                    if is_admin and status in (STATUS_COMPLETED, STATUS_FAILED,
                                               STATUS_CANCELLED):
                        if st.button("🗑️ Delete", key=f"rd_{r['id']}",
                                     use_container_width=True):
                            delete_run(r["id"])
                            st.rerun()

                # Approval inbox — handles wait_approval, manual_instruction,
                # and capability-gap pauses uniformly.
                if status == STATUS_WAITING_APPROVAL:
                    idx = r["current_index"]
                    steps = r["workflow_steps"]
                    if idx < len(steps):
                        step = steps[idx]
                        from core.workflow.template import render as _r
                        rendered = _r(step, r["context"])
                        # Pull most recent history entry to detect capability-gap
                        last_result = (r.get("history") or [{}])[-1].get("result", {})
                        kind = last_result.get("kind", "")
                        risk = rendered.get("risk", last_result.get("risk", "medium"))
                        risk_color = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(risk, "🟡")

                        if kind == "capability_gap":
                            st.error(
                                f"🔌 **Missing MCP capability** — risk: "
                                f"{risk_color} {risk}"
                            )
                            st.markdown(last_result.get("instructions", ""))
                            ok_label = "✅ I did it manually — continue"
                            no_label = "❌ Abort workflow"
                        elif kind == "manual_instruction":
                            st.info(
                                f"📝 **Manual step** — risk: {risk_color} {risk}"
                            )
                            st.markdown(f"### {last_result.get('title', 'Action required')}")
                            st.markdown(last_result.get("body", ""))
                            if last_result.get("why"):
                                st.caption(f"_Why this is manual:_ {last_result['why']}")
                            ok_label = "✅ Confirm done — continue"
                            no_label = "❌ Skip (abort workflow)"
                        else:
                            st.warning(
                                f"⏸️ **Awaiting approval** — risk: {risk_color} {risk}"
                            )
                            st.info(rendered.get("prompt", ""))
                            ok_label = "✅ Approve & resume"
                            no_label = "❌ Reject (cancel run)"

                        note = st.text_input("Note (optional)",
                                             key=f"note_{r['id']}")
                        col_ok, col_no = st.columns(2)
                        with col_ok:
                            if st.button(ok_label, key=f"ok_{r['id']}",
                                         type="primary",
                                         use_container_width=True):
                                WorkflowEngine().resume(r["id"], approval=True,
                                                        approval_note=note)
                                st.rerun()
                        with col_no:
                            if st.button(no_label, key=f"no_{r['id']}",
                                         use_container_width=True):
                                WorkflowEngine().resume(r["id"], approval=False,
                                                        approval_note=note)
                                st.rerun()

                # History
                st.markdown("**Step history**")
                hist = r.get("history", [])
                if not hist:
                    st.caption("No steps executed yet.")
                for h in hist:
                    icon = {
                        "completed": "✅", "skipped": "⏭️", "waiting": "⏸️",
                        "approved": "👍", "rejected": "👎", "error": "❌",
                        "failed": "💥",
                    }.get(h.get("status"), "•")
                    st.markdown(
                        f"{icon} **{h['step']}** _({h['type']})_  "
                        f"<span style='color:#6b7690;font-size:0.75rem;'>"
                        f"{h.get('duration_ms', 0)}ms · {h.get('at', '')[:19]}"
                        f"</span>",
                        unsafe_allow_html=True,
                    )
                    res = h.get("result", {})
                    if isinstance(res, dict) and res:
                        with st.container():
                            for k, v in res.items():
                                if isinstance(v, (dict, list)):
                                    st.caption(f"**{k}:**")
                                    st.code(json.dumps(v, indent=2,
                                                       default=str)[:1500],
                                            language="json")
                                else:
                                    sv = str(v)
                                    if len(sv) > 400:
                                        sv = sv[:400] + "..."
                                    st.caption(f"**{k}:** {sv}")


# ═══════════════════════════════════════════════════════════════
# TAB 3 — EDITOR
# ═══════════════════════════════════════════════════════════════

with tab_edit:
    if not is_admin:
        st.warning("⛔ Admin access required to edit workflows.")
        st.stop()

    files = sorted(WORKFLOWS_DIR.glob("*.y*ml"))
    file_names = [p.name for p in files]

    col_pick, col_new = st.columns([2, 1])
    with col_pick:
        choice = st.selectbox("Open existing", options=["<new>"] + file_names)
    with col_new:
        new_name = st.text_input("New name (snake_case)",
                                 placeholder="my_workflow",
                                 disabled=(choice != "<new>"))

    if choice == "<new>":
        initial = (
            "name: " + (new_name or "my_workflow") + "\n"
            "description: |\n"
            "  Describe what this workflow does.\n\n"
            "inputs:\n"
            "  target_host: \"\"\n\n"
            "steps:\n"
            "  - id: probe\n"
            "    type: tool\n"
            "    tool: linux_ops    # any tool name from the MCP registry\n"
            "    input:\n"
            "      command: \"uname -a\"\n"
            "      target_host: \"{{ inputs.target_host }}\"\n\n"
            "  - id: summarize\n"
            "    type: agent\n"
            "    prompt: \"Summarize: {{ probe.output }}\"\n"
            "    max_steps: 2\n"
        )
        target_name = new_name
    else:
        target_name = Path(choice).stem
        initial = (WORKFLOWS_DIR / choice).read_text(encoding="utf-8")

    yaml_text = st.text_area("YAML", value=initial, height=400,
                             key=f"editor_{choice}")

    col_save, col_validate, col_del = st.columns(3)
    with col_validate:
        if st.button("🔍 Validate", use_container_width=True):
            try:
                import yaml as _y
                parsed = _y.safe_load(yaml_text)
                errs = validate_workflow(parsed) if isinstance(parsed, dict) else \
                    ["YAML root must be a mapping"]
                if errs:
                    for e in errs:
                        st.error(e)
                else:
                    st.success("✅ Valid")
            except Exception as e:
                st.error(f"YAML parse error: {e}")

    with col_save:
        if st.button("💾 Save", type="primary", use_container_width=True,
                     disabled=(not target_name)):
            try:
                p = save_workflow(target_name, yaml_text)
                # Refresh scheduled jobs in case this workflow has trigger:schedule
                try:
                    n = reload_workflow_jobs()
                    st.success(f"Saved: {p.name}  ·  {n} scheduled workflow(s) active")
                except Exception:
                    st.success(f"Saved: {p.name}")
                st.rerun()
            except Exception as e:
                st.error(str(e))

    with col_del:
        if choice != "<new>" and st.button("🗑️ Delete", use_container_width=True):
            if st.session_state.get("_ed_confirm_del") == choice:
                delete_workflow(target_name)
                try:
                    reload_workflow_jobs()
                except Exception:
                    pass
                st.session_state.pop("_ed_confirm_del", None)
                st.success(f"Deleted: {choice}")
                st.rerun()
            else:
                st.session_state["_ed_confirm_del"] = choice
                st.warning("Click again to confirm delete")

    # ── Registry helper: show MCP tool names available now ──
    st.divider()
    st.markdown("##### 🛰️ Available MCP tools (for `tool:` references)")
    try:
        from tools.registry import get_active_tools
        tools = get_active_tools()
        if tools:
            for t in tools:
                st.code(f"- {t.get('name', '?')}  — {t.get('description', '')[:80]}")
        else:
            st.caption("No tools active in the registry yet.")
    except Exception as e:
        st.caption(f"Could not read registry: {e}")

    st.markdown(
        "##### 📖 Step types\n\n"
        "- `agent` — free-form agent turn; picks MCP tools dynamically.\n"
        "- `tool` — direct call to a named MCP tool (any tool in registry).\n"
        "- `metric_check` — re-run a monitoring metric and check expectation.\n"
        "- `wait_approval` — pause for human approval (appears in Runs tab).\n"
        "- `branch` — `when:` expression chooses `then:` or `else:` step list.\n"
        "- `notify` — `channel: log|syslog|webhook|email` + message.\n"
        "  - `email`: optional `to: [...]` / `subject:` / `html: true`. "
        "Falls back to `SMTP_DEFAULT_TO`. SMTP config lives in Settings.\n"
        "- `sleep` — pause N seconds (≤300).\n"
        "- `set` — inject context variables.\n"
        "- `close_incident` — close the linked incident session.\n"
        "- `manual_instruction` — pause and show step-by-step human "
        "instructions for an action no MCP supports. Operator clicks "
        "Confirm to continue, or Skip to abort.\n"
        "\n"
        "**Capability gap auto-fallback:** if a `tool:` step names an MCP "
        "that is not in the live registry, the engine automatically pauses "
        "the run with a manual-instruction explaining what was needed, what "
        "input it would have received, and which MCPs *are* available.\n"
        "\n"
        "Reference values from earlier steps with `{{ step_id.field }}` or "
        "inputs with `{{ inputs.key }}`."
    )
