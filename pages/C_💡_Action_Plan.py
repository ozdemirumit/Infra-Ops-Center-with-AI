"""
💡 Action Plan
Describe a problem in plain language → get a numbered remediation plan
with ready-to-run commands, MCP routing, and rollback guidance.

This page does NOT execute anything. The output is text — you decide
what to copy, paste into chat, or save as a runbook.
"""

import os
from datetime import datetime

import streamlit as st

from auth.authenticator import check_auth
from ui.sidebar import render_sidebar
from core.action_plan import generate_action_plan, save_plan_as_runbook

st.set_page_config(page_title="Action Plan", page_icon="💡", layout="wide")

css_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui", "style.css")
if os.path.exists(css_path):
    with open(css_path, "r", encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

if not check_auth():
    st.stop()

render_sidebar()

st.title("💡 Action Plan")
st.markdown(
    "Describe a problem in plain language. The agent will produce a "
    "**numbered remediation plan** with the exact commands to run via "
    "each MCP — or step-by-step manual instructions when no MCP can do "
    "the job. This page **never executes** anything; you stay in control."
)

# ─── MCP awareness banner ─────────────────────────────────────────
try:
    from tools.registry import get_active_tools
    _active_tools = get_active_tools()
    if _active_tools:
        with st.expander(
            f"🛰️ {len(_active_tools)} MCP(s) available to the planner",
            expanded=False,
        ):
            for t in _active_tools:
                st.markdown(
                    f"- **`{t.get('name', '?')}`** — "
                    f"{(t.get('description') or '').strip().splitlines()[0][:140]}"
                )
            st.caption(
                "The plan will route commands through these MCPs. Anything that "
                "isn't covered will appear as a manual instruction."
            )
    else:
        st.warning(
            "No MCPs registered. The plan will be entirely manual instructions."
        )
except Exception as e:
    st.caption(f"Could not enumerate MCPs: {e}")

st.divider()

# ─── Input form ───────────────────────────────────────────────────
with st.form("action_plan_form", clear_on_submit=False):
    problem = st.text_area(
        "What's the problem?",
        height=120,
        placeholder=(
            "e.g. Disk on srv-db01 is 92% full and growing 1 GB / hour. "
            "Need to identify the cause and free space without a service "
            "restart if possible."
        ),
    )

    col_target_a, col_target_b, col_target_c = st.columns([2, 2, 1])
    with col_target_a:
        target_name = st.text_input("Target name (optional)",
                                    placeholder="srv-db01")
    with col_target_b:
        target_ip = st.text_input("Target IP (optional)",
                                  placeholder="10.0.5.21")
    with col_target_c:
        target_type = st.selectbox(
            "Type",
            options=["(auto)", "linux", "windows", "esxi", "router",
                     "switch", "commvault", "other"],
        )

    extra = st.text_area(
        "Extra context (optional)",
        height=80,
        placeholder=(
            "Constraints, things you've already tried, change-window "
            "limits — anything the planner should know."
        ),
    )

    submitted = st.form_submit_button(
        "💡 Generate plan", type="primary", use_container_width=True
    )

# ─── Result ───────────────────────────────────────────────────────
if submitted:
    if not problem.strip():
        st.error("Please describe the problem first.")
    else:
        target = {}
        if target_name: target["name"] = target_name.strip()
        if target_ip:   target["ip"] = target_ip.strip()
        if target_type and target_type != "(auto)":
            target["device_type"] = target_type

        with st.spinner("🧠 Thinking… (this calls the LLM once, no MCPs are touched)"):
            plan = generate_action_plan(
                problem=problem.strip(),
                target=target or None,
                extra_context=extra.strip(),
            )
        st.session_state["_last_plan"] = plan

# Re-render last plan (survives reruns from button clicks)
plan = st.session_state.get("_last_plan")
if plan:
    risk_color = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(plan.risk, "⚪")
    st.markdown(
        f"#### {risk_color} Plan — risk: **{plan.risk}**  "
        f"<span style='color:#6b7690;font-size:0.8rem'>"
        f"generated {plan.created_at[:19]}"
        + (f" · model `{plan.model}`" if plan.model else "")
        + "</span>",
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        st.markdown(plan.plan_markdown or "_(empty plan — try again)_")

    # ── Actions ──
    col_save, col_copy, col_chat, col_clear = st.columns(4)

    with col_save:
        if st.button("💾 Save as runbook", use_container_width=True,
                     help="Writes to knowledge_base/runbooks/ and indexes "
                          "the plan in RAG so future agent turns can find it."):
            path = save_plan_as_runbook(plan)
            if path:
                st.success(f"✅ Saved: `{os.path.basename(path)}`")
            else:
                st.error("Save failed — see logs.")

    with col_copy:
        if st.download_button(
            "⬇️ Download .md",
            data=plan.plan_markdown.encode("utf-8"),
            file_name=f"plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
            mime="text/markdown",
            use_container_width=True,
        ):
            pass

    with col_chat:
        if st.button("💬 Send to chat", use_container_width=True,
                     help="Drop the plan into the main agent's chat as a "
                          "user message — the agent can then execute it "
                          "step-by-step using MCPs."):
            st.session_state["_action_plan_chat_seed"] = (
                "I have a plan I want you to execute step by step. "
                "Wait for my confirmation between destructive steps.\n\n"
                + plan.plan_markdown
            )
            st.success("✅ Plan queued — open Home to run it through the agent.")

    with col_clear:
        if st.button("🗑️ Clear", use_container_width=True):
            st.session_state.pop("_last_plan", None)
            st.rerun()

    # Raw markdown view for debugging / copy-paste
    with st.expander("View raw markdown", expanded=False):
        st.code(plan.plan_markdown, language="markdown")

st.divider()

# ─── Helper hint ──────────────────────────────────────────────────
st.markdown(
    "##### 📖 How is this different from Workflows?\n"
    "- **Workflows (`🔀`)** are YAML files the engine executes "
    "automatically with approvals and branching. Use them when the "
    "remediation should be **repeatable and auditable**.\n"
    "- **Action plans (this page)** are text guides the agent can follow "
    "or you can read manually. Use them for **one-off investigations**, "
    "brainstorming, or to draft the steps you'll later codify as a "
    "workflow."
)
