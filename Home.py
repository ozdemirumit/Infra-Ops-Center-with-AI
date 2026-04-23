"""
🛡️ Infra Ops Center with AI — Main Entry Point

Modular architecture:
  config/    → Central settings
  auth/      → Login screen, session, roles
  proxy/     → AI Proxy (rate limit, log, retry, token tracking)
  tools/     → SSH, Switch, Deco tools
  core/      → Agentic loop + tool dispatcher
  sessions/  → Task session management
  ui/        → Sidebar + Chat components
"""

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*urllib3.*chardet.*charset_normalizer.*")

import streamlit as st
import os
from config.settings import settings
from auth.authenticator import check_auth
from ui.sidebar import render_sidebar
from ui.chat import render_chat_history
from core.agent_loop import run_agent_loop
from core.planner import generate_plan, is_risky_prompt, format_plan_markdown
from sessions.storage import (
    create_session, get_session, list_sessions,
    delete_session, status_badge,
    STATUS_ACTIVE, STATUS_COMPLETED, STATUS_FAILED
)

# ── Autonomous Monitoring Scheduler ──
try:
    import streamlit as _st

    @_st.cache_resource
    def _start_monitor_scheduler():
        from core.monitor import get_scheduler
        return get_scheduler(interval_minutes=30)

    _start_monitor_scheduler()
except Exception as _e:
    import logging
    logging.getLogger("monitor").warning(
        f"Monitor scheduler init failed — continuing without background monitoring: {_e}"
    )

# ── Log cleanup scheduler (daily) ──
try:
    @_st.cache_resource
    def _start_log_cleanup():
        from logging_config.log_cleanup import start_cleanup_scheduler
        return start_cleanup_scheduler(interval_hours=24)

    _start_log_cleanup()
except Exception as _e:
    import logging
    logging.getLogger("cleanup").warning(f"Log cleanup scheduler init failed: {_e}")

# --- Page Settings ---
st.set_page_config(
    page_title="Infra Ops Center with AI",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- Modern UI Style ---
css_path = os.path.join(os.path.dirname(__file__), "ui", "style.css")
if os.path.exists(css_path):
    with open(css_path, "r", encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# --- Startup validation ---
_startup_warnings = settings.validate_startup()
if _startup_warnings:
    st.error("⚠️ **Configuration Issues Detected**")
    for w in _startup_warnings:
        st.warning(w)
    st.info("💡 Run `python setup_env.py` to generate a valid configuration.")
    # Critical (🔴) warnings block startup; 🟠 warnings allow continuing
    if any(w.startswith("🔴") for w in _startup_warnings):
        st.stop()

# --- Authentication ---
if not check_auth():
    st.stop()

# --- Sidebar ---
connections = render_sidebar()

# --- Main Memory ---
if "messages" not in st.session_state:
    st.session_state.messages = []

st.markdown(
    """
    <h1 style='background: linear-gradient(135deg, #5b8def 0%, #a78bfa 100%);
               -webkit-background-clip: text;
               -webkit-text-fill-color: transparent;
               background-clip: text;
               font-weight: 800;
               font-size: 2.25rem;
               letter-spacing: -0.03em;
               margin-bottom: 0.25rem;'>
        🛡️ Infra Ops Center
    </h1>
    """,
    unsafe_allow_html=True,
)

# ────────────────────────────────────────────────────────────────────
# SESSION MANAGEMENT
# ────────────────────────────────────────────────────────────────────

active_session_id = st.session_state.get("active_session_id")
active_session    = get_session(active_session_id) if active_session_id else None

# ── If there is an active session, show chat interface ──
if active_session and active_session.get("status") in (STATUS_ACTIVE, STATUS_COMPLETED, STATUS_FAILED):

    # Title + close button
    col_title, col_back = st.columns([5, 1])
    with col_title:
        badge = status_badge(active_session["status"])
        st.markdown(f"### 📋 {active_session['title']}")
        st.caption(f"{badge} · Started: {active_session['created_at'][:16]}")
    with col_back:
        if st.button("← Tasks", use_container_width=True):
            st.session_state.active_session_id = None
            st.session_state.messages = []
            st.rerun()

    st.divider()

    # Load session messages into main memory
    if not st.session_state.messages:
        st.session_state.messages = active_session.get("messages", [])

    render_chat_history()

    # Context message for completed tasks
    if active_session.get("status") == STATUS_COMPLETED:
        st.info("✅ This task is completed. You can continue where you left off by typing a new message.")
    elif active_session.get("status") == STATUS_FAILED:
        st.warning("❌ This task ended with an error. You can retry by typing a new message.")

    # ── Is there a plan awaiting approval? ──
    pending_plan = st.session_state.get("pending_plan")
    pending_prompt = st.session_state.get("pending_plan_prompt", "")

    if pending_plan:
        st.info("📋 AI generated a plan. Review and approve or cancel.")
        with st.expander("🗺️ Execution Plan", expanded=True):
            st.markdown(format_plan_markdown(pending_plan))

        col_ok, col_cancel = st.columns(2)
        with col_ok:
            if st.button("✅ Approve & Run", type="primary", use_container_width=True):
                sess_obj = get_session(active_session_id)
                if sess_obj:
                    sess_obj["plan"] = pending_plan.to_dict()
                    from sessions.storage import save_session
                    save_session(sess_obj)
                st.session_state.pending_plan = None
                st.session_state.pending_plan_prompt = ""
                run_agent_loop(pending_prompt, connections, session_id=active_session_id)
                st.rerun()
        with col_cancel:
            if st.button("❌ Cancel", use_container_width=True):
                st.session_state.pending_plan = None
                st.session_state.pending_plan_prompt = ""
                st.rerun()

    # ── Is there a change command awaiting approval? ──
    elif st.session_state.get("pending_command"):
        pc = st.session_state["pending_command"]
        cmd = pc.get("command_text", "")
        risk_icon = pc.get("risk_icon", "🟡")
        risk = pc.get("risk", "medium")
        impact = pc.get("impact", "")

        st.warning(
            f"⚠️ **Change command awaiting approval**\n\n"
            f"**Command:** `{cmd}`\n\n"
            f"**Risk:** {risk_icon} {risk.upper()} · {impact}"
        )

        col_a, col_r = st.columns(2)
        with col_a:
            if st.button("✅ Approve & Execute", type="primary", use_container_width=True, key="approve_cmd"):
                pc = st.session_state.pop("pending_command")
                from core.agent_loop import _dispatch_tool, TOOL_ICONS
                from proxy.ai_proxy import AIProxy

                tool_name = pc["tool_name"]
                tool_input = pc["tool_input"]
                conns = pc.get("connections", connections)
                sess_id = pc.get("session_id", active_session_id)
                icon = TOOL_ICONS.get(tool_name, "🛠️")

                with st.chat_message("assistant"):
                    st.success(f"▶️ Approved — executing `{cmd}`...")
                    with st.spinner(f"{icon} {tool_name} running..."):
                        raw_result = _dispatch_tool(tool_name, tool_input, conns)
                        proxy = AIProxy()
                        result = proxy.filter_ssh_output(raw_result)
                    st.markdown(f"**{icon} {tool_name} Output:**")
                    st.code(result, language="bash")

                # Save to history
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": [{"type": "text", "text": f"✅ `{cmd}` approved and executed."}],
                })
                st.session_state.messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": pc.get("tool_use_id", ""), "content": result}],
                })

                # Save session
                if sess_id:
                    from sessions.storage import save_session, get_session as _gs
                    _s = _gs(sess_id)
                    if _s:
                        _s["messages"] = st.session_state.messages
                        _s["status"] = "active"
                        save_session(_s)

                # Let AI interpret the result
                run_agent_loop(
                    f"Command executed, analyze the result and provide a summary:\n{result[:3000]}",
                    conns, session_id=sess_id,
                )
                st.rerun()

        with col_r:
            if st.button("❌ Reject", use_container_width=True, key="reject_cmd"):
                st.session_state.pop("pending_command", None)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": [{"type": "text", "text": f"🚫 `{cmd}` command rejected."}],
                })
                if active_session_id:
                    from sessions.storage import save_session, get_session as _gs
                    _s = _gs(active_session_id)
                    if _s:
                        _s["messages"] = st.session_state.messages
                        save_session(_s)
                st.rerun()

    else:
            # Normal user input — for sessions in any state
            planning_enabled = st.session_state.get("planning_enabled", True)
            if prompt := st.chat_input("Type your message..."):
                if planning_enabled and is_risky_prompt(prompt):
                    with st.spinner("🗣️ AI generating plan..."):
                        plan = generate_plan(prompt, connections)
                    if plan and plan.risk in ("medium", "high"):
                        st.session_state.pending_plan = plan
                        st.session_state.pending_plan_prompt = prompt
                        st.rerun()
                    else:
                        run_agent_loop(prompt, connections, session_id=active_session_id)
                        st.rerun()
                else:
                    run_agent_loop(prompt, connections, session_id=active_session_id)
                    st.rerun()

# ── No active session: Task selector ──
else:
    # Hero section
    st.markdown(
        """
        <div style='text-align: left; margin-bottom: 1.5rem;'>
            <p style='color: #a8b3c5; font-size: 1.05rem; line-height: 1.6; max-width: 720px; margin-top: 0.5rem;'>
                Give commands in natural language — your AI operator will connect to servers via
                SSH, HTTP or REST and complete the task autonomously.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Quick status
    try:
        from tools.registry import get_active_tools
        from devices.storage import DeviceStorage
        total_devices = sum(len(DeviceStorage.get_by_type(dt)) for dt in dict(DEVICE_TYPES))
        total_tools = len(get_active_tools())
        total_tasks = len(list_sessions(limit=100))
        active_tasks = sum(1 for s in list_sessions(limit=100) if s.get("status") == STATUS_ACTIVE)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("🖥️ Devices", total_devices)
        c2.metric("🔧 Active Tools", total_tools)
        c3.metric("📋 Total Tasks", total_tasks)
        c4.metric("⚡ Active", active_tasks)
    except Exception:
        pass

    st.write("")

    # New task — clean card
    with st.container(border=True):
        st.markdown("##### 🚀 Start a New Task")
        with st.form("new_session_form", clear_on_submit=True):
            task_title = st.text_input(
                "task_input",
                placeholder="E.g. Update all Linux servers · Check disk usage · List running VMs...",
                label_visibility="collapsed",
            )
            submitted = st.form_submit_button(
                "▶️ Launch Task",
                use_container_width=True,
                type="primary"
            )

    if submitted and task_title.strip():
        new_session = create_session(task_title.strip(), connections)
        st.session_state.active_session_id = new_session["id"]
        st.session_state.messages = []
        st.rerun()

    # Previous tasks
    sessions = list_sessions(limit=30)
    if sessions:
        st.write("")
        st.markdown("##### 📋 Recent Tasks")

        for sess in sessions:
            status = sess.get("status", "unknown")
            badge = status_badge(status)
            created = sess["created_at"][:16] if "created_at" in sess else ""
            title = sess.get("title", "Untitled")

            # Status colors for left accent
            status_colors = {
                STATUS_ACTIVE: "#5b8def",
                STATUS_COMPLETED: "#10b981",
                STATUS_FAILED: "#ef4444",
            }
            accent = status_colors.get(status, "#6b7690")

            with st.container(border=True):
                col_info, col_continue, col_delete = st.columns([7, 1.4, 0.7])

                with col_info:
                    st.markdown(
                        f"<div style='border-left: 3px solid {accent}; padding-left: 12px;'>"
                        f"<div style='font-weight: 600; font-size: 0.95rem; color: #f1f5f9; margin-bottom: 4px;'>{title}</div>"
                        f"<div style='font-size: 0.75rem; color: #6b7690;'>{badge} · {created}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                with col_continue:
                    btn_label = "▶️ Continue" if status == STATUS_ACTIVE else "👁️ View"
                    if st.button(btn_label, key=f"open_{sess['id']}", use_container_width=True):
                        st.session_state.active_session_id = sess["id"]
                        st.session_state.messages = []
                        st.rerun()

                with col_delete:
                    pending_del = st.session_state.get("_pending_delete_session")
                    if pending_del == sess["id"]:
                        if st.button("✅", key=f"confirm_{sess['id']}", use_container_width=True,
                                     type="primary", help="Confirm delete"):
                            delete_session(sess["id"])
                            st.session_state.pop("_pending_delete_session", None)
                            if st.session_state.get("active_session_id") == sess["id"]:
                                st.session_state.active_session_id = None
                            st.rerun()
                    else:
                        if st.button("🗑️", key=f"del_{sess['id']}", use_container_width=True,
                                     help="Delete task (click again to confirm)"):
                            st.session_state["_pending_delete_session"] = sess["id"]
                            st.rerun()
    else:
        st.info("💡 No tasks yet. Type your first task above to get started.")