"""
⏱️ Scheduled Tasks
Define cron-like recurring tasks that AI executes automatically.
"""

import streamlit as st
import os
from datetime import datetime
from auth.authenticator import check_auth, is_admin
from ui.sidebar import render_sidebar
from core.task_scheduler import (
    list_tasks, add_task, update_task, delete_task, set_enabled, get_scheduler
)

st.set_page_config(page_title="Scheduled Tasks", page_icon="⏱️", layout="wide")

css_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui", "style.css")
if os.path.exists(css_path):
    with open(css_path, "r", encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

if not check_auth():
    st.stop()

render_sidebar()

if not is_admin():
    st.error("⛔ Admin access required.")
    st.stop()

# Ensure scheduler is running
get_scheduler()

st.title("⏱️ Scheduled Tasks")
st.markdown("Define recurring AI tasks (health checks, backups, reports). Tasks run in the background.")

# ═══════════════════════════════════════════════════════════════════
# ADD NEW TASK
# ═══════════════════════════════════════════════════════════════════
with st.expander("➕ Add New Scheduled Task", expanded=False):
    with st.form("new_task", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            new_name = st.text_input("Task Name *", placeholder="Daily disk check")
        with col2:
            new_enabled = st.toggle("Active", value=True)

        new_prompt = st.text_area(
            "AI Prompt *",
            placeholder="E.g.: Check disk usage on all Linux servers and summarize.",
            height=100,
        )

        col_type, col_cron, col_interval = st.columns([1, 2, 1])
        with col_type:
            new_type = st.selectbox("Schedule Type", ["interval", "cron"])
        with col_cron:
            new_cron = st.text_input(
                "Cron Expression (if cron)",
                placeholder="0 9 * * *  (daily at 9 AM)",
                help="Standard cron: minute hour day month weekday",
            )
        with col_interval:
            new_interval = st.number_input("Interval (min)", value=60, min_value=1)

        submitted = st.form_submit_button("➕ Create Task", type="primary", use_container_width=True)

        if submitted:
            if not new_name or not new_prompt:
                st.error("❌ Name and prompt are required.")
            elif new_type == "cron" and not new_cron:
                st.error("❌ Cron expression is required when type is 'cron'.")
            else:
                task = add_task(
                    new_name, new_prompt, new_type,
                    interval_minutes=new_interval,
                    cron_expr=new_cron,
                    enabled=new_enabled,
                )
                st.success(f"✅ Task '{new_name}' created!")
                st.rerun()

st.divider()

# ═══════════════════════════════════════════════════════════════════
# EXISTING TASKS
# ═══════════════════════════════════════════════════════════════════
tasks = list_tasks()

if not tasks:
    st.info("No scheduled tasks yet. Create one above.")
else:
    st.markdown(f"##### {len(tasks)} Scheduled Task(s)")

    for task in tasks:
        status_icon = "🟢" if task.get("enabled") else "⚫"
        schedule = (
            f"Every {task['interval_minutes']} min"
            if task["schedule_type"] == "interval"
            else f"Cron: `{task['cron_expr']}`"
        )
        last_run = task.get("last_run", "never")[:16] if task.get("last_run") else "never"
        last_status = task.get("last_status", "—")

        with st.container(border=True):
            col_info, col_actions = st.columns([5, 2])

            with col_info:
                st.markdown(f"**{status_icon} {task['name']}**")
                st.caption(f"📅 {schedule} · Last: {last_run} ({last_status})")
                with st.expander("Prompt"):
                    st.code(task["prompt"], language="text")

            with col_actions:
                # Toggle
                new_state = st.toggle(
                    "Active", value=task.get("enabled", True),
                    key=f"enabled_{task['id']}",
                )
                if new_state != task.get("enabled", True):
                    set_enabled(task["id"], new_state)
                    st.rerun()

                # Delete with confirmation
                pending = st.session_state.get("_pending_del_task")
                if pending == task["id"]:
                    cc1, cc2 = st.columns(2)
                    with cc1:
                        if st.button("✅ Confirm", key=f"confirm_del_{task['id']}",
                                     type="primary", use_container_width=True):
                            delete_task(task["id"])
                            st.session_state.pop("_pending_del_task", None)
                            st.rerun()
                    with cc2:
                        if st.button("❌", key=f"cancel_del_{task['id']}",
                                     use_container_width=True):
                            st.session_state.pop("_pending_del_task", None)
                            st.rerun()
                else:
                    if st.button("🗑️ Delete", key=f"del_{task['id']}",
                                 type="secondary", use_container_width=True):
                        st.session_state["_pending_del_task"] = task["id"]
                        st.rerun()

st.divider()

# Help
with st.expander("ℹ️ Cron expression examples"):
    st.markdown(
        """
        | Expression | Meaning |
        |---|---|
        | `* * * * *` | Every minute |
        | `0 * * * *` | Every hour |
        | `0 9 * * *` | Daily at 9:00 AM |
        | `0 9 * * 1-5` | Weekdays at 9:00 AM |
        | `0 0 1 * *` | 1st of every month at midnight |
        | `*/15 * * * *` | Every 15 minutes |
        """
    )
