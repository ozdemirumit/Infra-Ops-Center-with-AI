"""
📊 Monitoring Dashboard — Independent Metric-Based Monitoring

Each metric is shown on a separate card. Threshold, interval, and severity are configured individually.
Custom metrics can be added.
"""

import streamlit as st
import os
from datetime import datetime
from auth.authenticator import check_auth
from ui.sidebar import render_sidebar
from core.monitor import (
    load_state, save_state, run_check_now,
    get_checks_config, update_check_config,
    add_custom_check, remove_custom_check,
    get_results_for_check, DEFAULT_HEALTH_CHECKS,
)
from sessions.storage import list_sessions, status_badge

st.set_page_config(page_title="Monitoring", page_icon="📊", layout="wide")

css_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui", "style.css")
if os.path.exists(css_path):
    with open(css_path, "r", encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

if not check_auth():
    st.stop()

connections = render_sidebar()
is_admin = st.session_state.get("role") == "admin"

st.title("📊 Autonomous Monitoring")
st.markdown("Each metric is monitored at independent intervals. Incidents are automatically created when thresholds are exceeded.")

# ─── Overall Status ────────────────────────────────────────────────────────────
state = load_state()
checks_config = state.checks_config

active_checks = sum(1 for c in checks_config.values() if c.get("enabled"))
total_checks = len(checks_config)

col_status, col_metrics, col_action = st.columns([2, 1, 1])

with col_status:
    if state.scheduler_running:
        st.success(f"✅ Scheduler running — **{active_checks}/{total_checks}** metrics active")
    else:
        st.warning("⚠️ Scheduler stopped")

with col_metrics:
    # Show worst status
    statuses = [r.get("status", "ok") for r in state.results]
    if "critical" in statuses:
        st.error("🔴 Critical alert!")
    elif "warning" in statuses:
        st.warning("🟡 Warning")
    elif "error" in statuses:
        st.info("⚫ Errors in some checks")
    elif statuses:
        st.success("🟢 All normal")

with col_action:
    st.write("")
    if st.button("▶️ Run All Checks", use_container_width=True, type="primary"):
        with st.spinner("Running checks..."):
            run_check_now()
        st.success("All checks completed!")
        st.rerun()

st.divider()

# ═══════════════════════════════════════════════════════════════════════════
# METRIC CARDS
# ═══════════════════════════════════════════════════════════════════════════

st.subheader("📋 Metric Panels")

default_names = {hc["name"] for hc in DEFAULT_HEALTH_CHECKS}

for check_name, cfg in checks_config.items():
    icon = cfg.get("icon", "📊")
    label = cfg.get("label", check_name)
    enabled = cfg.get("enabled", True)
    threshold = cfg.get("threshold", 0)
    interval = cfg.get("interval_minutes", 30)
    severity = cfg.get("severity", "warning")
    unit = cfg.get("unit", "")
    last_run = cfg.get("last_run")
    is_default = check_name in default_names

    # Results for this metric
    results = [r for r in state.results if r.get("check_name") == check_name]

    # Worst status
    check_statuses = [r.get("status", "ok") for r in results]
    if "critical" in check_statuses:
        status_icon = "🔴"
        status_label = "Critical"
    elif "warning" in check_statuses:
        status_icon = "🟡"
        status_label = "Warning"
    elif "error" in check_statuses:
        status_icon = "⚫"
        status_label = "Error"
    elif results:
        status_icon = "🟢"
        status_label = "Normal"
    else:
        status_icon = "⚪"
        status_label = "No data"

    enabled_label = "" if enabled else " (disabled)"

    with st.expander(
        f"{icon} **{label}** — {status_icon} {status_label} · "
        f"Threshold: {threshold}{unit} · Every {interval}min{enabled_label}",
        expanded=False,
    ):
        # ── Results (per server) ──
        if results:
            cols = st.columns(min(len(results), 4))
            for i, r in enumerate(results):
                with cols[i % len(cols)]:
                    r_status = r.get("status", "ok")
                    r_icon = {"ok": "✅", "warning": "⚠️", "critical": "🚨", "error": "❌"}.get(r_status, "❓")
                    st.metric(
                        label=f"{r_icon} {r.get('server_name', '?')}",
                        value=f"{r.get('value', 0):.1f}{unit}",
                        delta=f"Threshold: {threshold}{unit}",
                        delta_color="off",
                    )
                    if r.get("error"):
                        st.caption(f"❌ {r['error'][:60]}")
                    if r.get("incident_session_id"):
                        st.caption(f"🚨 Incident: {r['incident_session_id'][:8]}...")
        else:
            st.info("No checks performed yet.")

        if last_run:
            try:
                dt = datetime.fromisoformat(last_run)
                st.caption(f"Last check: {dt.strftime('%d.%m.%Y %H:%M:%S')}")
            except Exception:
                st.caption(f"Last check: {last_run}")

        # ── Actions ──
        col_run, col_ai, col_spacer = st.columns([1, 1, 2])
        with col_run:
            if st.button("▶️ Run Check", key=f"run_{check_name}", use_container_width=True):
                with st.spinner(f"Checking '{label}'..."):
                    run_check_now(check_name)
                st.rerun()
        with col_ai:
            # AI investigation button if there's a problem
            has_problem = any(s in check_statuses for s in ("warning", "critical", "error"))
            if has_problem:
                if st.button("🤖 AI Investigate", key=f"ai_{check_name}", type="primary", use_container_width=True):
                    with st.spinner("🤖 Starting AI investigation..."):
                        from core.incident_manager import trigger_ai_investigation
                        sid = trigger_ai_investigation(check_name)
                    if sid:
                        st.success(f"🤖 AI incident created! Session: {sid[:8]}...")
                        if st.button("👁️ View", key=f"go_ai_{check_name}"):
                            st.session_state.active_session_id = sid
                            st.session_state.messages = []
                            st.switch_page("main.py")
                    else:
                        st.info("ℹ️ An incident already exists for this metric.")
                    st.rerun()
            else:
                st.button("🤖 AI Investigate", key=f"ai_{check_name}", disabled=True, use_container_width=True, help="AI cannot be triggered when there is no problem")

        # ── Settings (admin) ──
        if is_admin:
            st.markdown("---")
            st.markdown("**⚙️ Settings**")

            col_thresh, col_interval, col_sev, col_enabled = st.columns([1, 1, 1, 1])

            with col_thresh:
                new_threshold = st.number_input(
                    "Threshold", value=float(threshold), step=1.0,
                    key=f"thresh_{check_name}",
                )
            with col_interval:
                new_interval = st.number_input(
                    "Interval (min)", value=int(interval), min_value=1, max_value=1440, step=5,
                    key=f"interval_{check_name}",
                )
            with col_sev:
                sev_options = ["warning", "critical"]
                new_severity = st.selectbox(
                    "Severity", options=sev_options,
                    index=sev_options.index(severity) if severity in sev_options else 0,
                    key=f"sev_{check_name}",
                )
            with col_enabled:
                new_enabled = st.toggle(
                    "Active", value=enabled, key=f"enabled_{check_name}",
                )

            col_save, col_delete = st.columns([1, 1])
            with col_save:
                if st.button("💾 Save", key=f"save_{check_name}", use_container_width=True):
                    update_check_config(
                        check_name,
                        threshold=new_threshold,
                        interval_minutes=new_interval,
                        severity=new_severity,
                        enabled=new_enabled,
                    )
                    st.success(f"'{label}' settings updated!")
                    st.rerun()
            with col_delete:
                if not is_default:
                    if st.button("🗑️ Delete", key=f"del_{check_name}", use_container_width=True):
                        remove_custom_check(check_name)
                        st.success(f"'{label}' deleted!")
                        st.rerun()

st.divider()

# ═══════════════════════════════════════════════════════════════════════════
# ADD NEW METRIC
# ═══════════════════════════════════════════════════════════════════════════

if is_admin:
    st.subheader("➕ Add New Metric")
    st.caption("Define a custom metric using an SSH command. The command must return a numeric value.")

    with st.form("add_check", clear_on_submit=True):
        col_name, col_label, col_icon = st.columns([2, 2, 1])
        with col_name:
            new_name = st.text_input("Metric Name (snake_case)", placeholder="swap_usage")
        with col_label:
            new_label = st.text_input("Display Name", placeholder="Swap Usage")
        with col_icon:
            new_icon = st.text_input("Icon", value="📊", max_chars=2)

        new_cmd = st.text_input(
            "SSH Command (must return a numeric output)",
            placeholder="free | awk '/Swap:/{if($2>0) printf \"%.0f\",$3/$2*100; else print 0}'",
        )

        col_t, col_u, col_c, col_s, col_i = st.columns(5)
        with col_t:
            new_thresh = st.number_input("Threshold", value=80.0, step=1.0)
        with col_u:
            new_unit = st.text_input("Unit", value="%", max_chars=5)
        with col_c:
            new_compare = st.selectbox("Comparison", ["gt", "lt"], format_func=lambda x: "Greater than (>)" if x == "gt" else "Less than (<)")
        with col_s:
            new_sev = st.selectbox("Severity", ["warning", "critical"])
        with col_i:
            new_int = st.number_input("Interval (min)", value=30, min_value=1, max_value=1440, step=5)

        submitted = st.form_submit_button("🚀 Add Metric", type="primary", use_container_width=True)

    if submitted and new_name and new_label and new_cmd:
        try:
            add_custom_check(
                name=new_name, label=new_label, cmd=new_cmd,
                threshold=new_thresh, unit=new_unit, compare=new_compare,
                severity=new_sev, interval_minutes=new_int, icon=new_icon,
            )
            st.success(f"✅ '{new_label}' metric added!")
            st.rerun()
        except ValueError as e:
            st.error(f"❌ {e}")

st.divider()

# ═══════════════════════════════════════════════════════════════════════════
# INCIDENTS
# ═══════════════════════════════════════════════════════════════════════════

st.subheader("🚨 Automatically Created Incidents")

incident_sessions = [
    s for s in list_sessions()
    if s.get("title", "").startswith("🚨 Otomatik:")
]

if not incident_sessions:
    st.info("No active auto-created incidents.")
else:
    for sess in incident_sessions:
        badge = status_badge(sess["status"])
        created = sess.get("created_at", "")[:16]

        with st.container(border=True):
            col_info, col_action = st.columns([4, 1])
            with col_info:
                st.markdown(f"**{sess['title']}**")
                st.caption(f"{badge} · {created}")
            with col_action:
                if st.button("👁️ View", key=f"inc_{sess['id']}", use_container_width=True):
                    st.session_state.active_session_id = sess["id"]
                    st.session_state.messages = []
                    st.switch_page("main.py")
