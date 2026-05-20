"""
📊 Monitoring Dashboard — Multi-Backend Independent Metric Monitoring

Each metric is shown on a separate card. Backend can be SSH, MCP tool, HTTP GET, or ICMP ping.
Thresholds, intervals, severity, maintenance windows, and comparison operators are all configurable.
"""

import streamlit as st
import os
import json
from datetime import datetime, timedelta
from auth.authenticator import check_auth
from ui.sidebar import render_sidebar
from core.monitor import (
    load_state, save_state, run_check_now,
    get_checks_config, update_check_config,
    add_custom_check, remove_custom_check,
    get_results_for_check, get_history,
    DEFAULT_HEALTH_CHECKS,
    BACKEND_SSH, BACKEND_MCP, BACKEND_HTTP, BACKEND_PING, COMPARE_OPS,
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
st.markdown(
    "Monitor anything — servers via **SSH**, services via **MCP tools**, "
    "endpoints via **HTTP**, or hosts via **ICMP ping**. "
    "Each metric runs on its own interval and creates incidents automatically."
)

# ─── Backend helpers ───────────────────────────────────────────────────────────

BACKEND_LABELS = {
    BACKEND_SSH: "🖥️ SSH command",
    BACKEND_MCP: "🛰️ MCP tool",
    BACKEND_HTTP: "🌐 HTTP GET",
    BACKEND_PING: "📶 ICMP ping",
}
BACKEND_OPTIONS = list(BACKEND_LABELS.keys())

COMPARE_LABELS = {
    "gt": "Greater than (>)", "gte": "Greater or equal (>=)",
    "lt": "Less than (<)", "lte": "Less or equal (<=)",
    "eq": "Equal (==)", "ne": "Not equal (!=)",
    "contains": "Contains", "not_contains": "Does not contain",
    "regex": "Regex match",
}


def _get_mcp_tool_names() -> list[str]:
    try:
        from tools.registry import get_active_tools
        tools = get_active_tools()
        return sorted({t.get("name", "") for t in tools if t.get("name")})
    except Exception:
        return ["linux_ops", "switch_ops"]


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
    backend = cfg.get("backend", BACKEND_SSH)
    compare = cfg.get("compare", "gt")
    is_default = check_name in default_names

    results = [r for r in state.results if r.get("check_name") == check_name]
    check_statuses = [r.get("status", "ok") for r in results]
    if "critical" in check_statuses:
        status_icon, status_label = "🔴", "Critical"
    elif "warning" in check_statuses:
        status_icon, status_label = "🟡", "Warning"
    elif "error" in check_statuses:
        status_icon, status_label = "⚫", "Error"
    elif "maintenance" in check_statuses:
        status_icon, status_label = "🛠️", "Maintenance"
    elif results:
        status_icon, status_label = "🟢", "Normal"
    else:
        status_icon, status_label = "⚪", "No data"

    enabled_label = "" if enabled else " (disabled)"
    backend_label = BACKEND_LABELS.get(backend, backend)

    with st.expander(
        f"{icon} **{label}** — {status_icon} {status_label} · "
        f"{backend_label} · {COMPARE_LABELS.get(compare, compare)} {threshold}{unit} · "
        f"Every {interval}min{enabled_label}",
        expanded=False,
    ):
        # ── Current results ──
        if results:
            cols = st.columns(min(len(results), 4))
            for i, r in enumerate(results):
                with cols[i % len(cols)]:
                    r_status = r.get("status", "ok")
                    r_icon = {
                        "ok": "✅", "warning": "⚠️", "critical": "🚨",
                        "error": "❌", "maintenance": "🛠️",
                    }.get(r_status, "❓")
                    try:
                        val_display = f"{float(r.get('value', 0)):.2f}{unit}"
                    except Exception:
                        val_display = f"{r.get('value', '?')}{unit}"
                    st.metric(
                        label=f"{r_icon} {r.get('server_name', '?')}",
                        value=val_display,
                        delta=f"Threshold: {threshold}{unit}",
                        delta_color="off",
                    )
                    if r.get("error"):
                        st.caption(f"❌ {r['error'][:80]}")
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

        # ── History chart ──
        if results:
            try:
                # Use first server's history
                first_sid = results[0].get("server_id", "")
                history = get_history(first_sid, check_name)
                if history and len(history) > 1:
                    import pandas as pd
                    df = pd.DataFrame(history)
                    df["t"] = pd.to_datetime(df["t"], errors="coerce")
                    df = df.dropna(subset=["t"]).set_index("t")
                    numeric_v = pd.to_numeric(df["v"], errors="coerce")
                    if numeric_v.notna().sum() > 1:
                        st.markdown("**📈 Trend** (last samples)")
                        st.line_chart(numeric_v.rename("value"), height=160)
            except Exception as e:
                st.caption(f"History unavailable: {e}")

        # ── Actions ──
        col_run, col_ai, col_spacer = st.columns([1, 1, 2])
        with col_run:
            if st.button("▶️ Run Check", key=f"run_{check_name}", use_container_width=True):
                with st.spinner(f"Checking '{label}'..."):
                    run_check_now(check_name)
                st.rerun()
        with col_ai:
            has_problem = any(s in check_statuses for s in ("warning", "critical", "error"))
            if has_problem:
                if st.button("🤖 AI Investigate", key=f"ai_{check_name}", type="primary", use_container_width=True):
                    with st.spinner("🤖 Starting AI investigation..."):
                        from core.incident_manager import trigger_ai_investigation
                        sid = trigger_ai_investigation(check_name)
                    if sid:
                        st.success(f"🤖 AI incident created! Session: {sid[:8]}...")
                        st.session_state.active_session_id = sid
                        st.session_state.messages = []
                    else:
                        st.info("ℹ️ An incident already exists for this metric.")
                    st.rerun()
            else:
                st.button("🤖 AI Investigate", key=f"ai_{check_name}",
                          disabled=True, use_container_width=True,
                          help="AI cannot be triggered when there is no problem")

        # ── Settings (admin) ──
        if is_admin:
            st.markdown("---")
            st.markdown("**⚙️ Settings**")

            col_thresh, col_interval, col_compare, col_sev, col_enabled = st.columns(5)

            with col_thresh:
                # threshold may be non-numeric for contains/regex
                try:
                    th_value = float(threshold)
                    new_threshold = st.number_input(
                        "Threshold", value=th_value, step=1.0,
                        key=f"thresh_{check_name}",
                    )
                except (TypeError, ValueError):
                    new_threshold = st.text_input(
                        "Threshold", value=str(threshold), key=f"thresh_{check_name}",
                    )
            with col_interval:
                new_interval = st.number_input(
                    "Interval (min)", value=int(interval), min_value=1, max_value=1440, step=5,
                    key=f"interval_{check_name}",
                )
            with col_compare:
                idx = COMPARE_OPS.index(compare) if compare in COMPARE_OPS else 0
                new_compare = st.selectbox(
                    "Comparison", options=COMPARE_OPS,
                    index=idx, format_func=lambda x: COMPARE_LABELS.get(x, x),
                    key=f"cmp_{check_name}",
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

            # ── Maintenance window ──
            mw = cfg.get("maintenance_until", "")
            col_mw1, col_mw2, col_mw3 = st.columns([2, 1, 1])
            with col_mw1:
                st.caption(
                    f"🛠️ Maintenance until: **{mw[:16]}**" if mw else "🛠️ No maintenance window"
                )
            with col_mw2:
                mw_hours = st.number_input(
                    "Hours", min_value=1, max_value=168, value=2,
                    key=f"mwh_{check_name}",
                )
            with col_mw3:
                if st.button("Silence", key=f"mw_set_{check_name}", use_container_width=True):
                    until = (datetime.now() + timedelta(hours=int(mw_hours))).isoformat()
                    update_check_config(check_name, maintenance_until=until)
                    st.rerun()
                if mw and st.button("Clear", key=f"mw_clr_{check_name}", use_container_width=True):
                    update_check_config(check_name, maintenance_until="")
                    st.rerun()

            # ── Backend-specific settings ──
            with st.expander("🔧 Backend configuration", expanded=False):
                backend_idx = BACKEND_OPTIONS.index(backend) if backend in BACKEND_OPTIONS else 0
                new_backend = st.selectbox(
                    "Backend", options=BACKEND_OPTIONS, index=backend_idx,
                    format_func=lambda x: BACKEND_LABELS.get(x, x),
                    key=f"bk_{check_name}",
                )

                new_device_type = st.text_input(
                    "Device type (filter targets — blank = global for HTTP/ping)",
                    value=cfg.get("device_type", ""),
                    key=f"dt_{check_name}",
                )

                # Backend-specific fields
                new_cmd = cfg.get("cmd", "")
                new_mcp_tool = cfg.get("mcp_tool", "")
                new_mcp_action = cfg.get("mcp_action", "")
                new_http_url = cfg.get("http_url", "")
                new_http_json_path = cfg.get("http_json_path", "")
                new_http_headers_raw = json.dumps(cfg.get("http_headers", {}) or {})

                if new_backend == BACKEND_SSH:
                    new_cmd = st.text_input(
                        "SSH command (numeric output)",
                        value=cfg.get("cmd", ""), key=f"cmd_{check_name}",
                    )
                elif new_backend == BACKEND_MCP:
                    tool_names = _get_mcp_tool_names()
                    tool_idx = tool_names.index(new_mcp_tool) if new_mcp_tool in tool_names else 0
                    new_mcp_tool = st.selectbox(
                        "MCP tool", options=tool_names or [""],
                        index=tool_idx if tool_names else 0,
                        key=f"mcp_t_{check_name}",
                    )
                    new_mcp_action = st.text_input(
                        "Action / command to send to the tool",
                        value=new_mcp_action,
                        placeholder="e.g. df -h, get_alerts, show interface status",
                        key=f"mcp_a_{check_name}",
                    )
                elif new_backend == BACKEND_HTTP:
                    new_http_url = st.text_input(
                        "URL (supports {ip}, {name}, ${vault:cat/name})",
                        value=new_http_url, key=f"http_u_{check_name}",
                        placeholder="https://api.example.com/health",
                    )
                    new_http_json_path = st.text_input(
                        "JSON path (optional, e.g. data.cpu.percent)",
                        value=new_http_json_path, key=f"http_j_{check_name}",
                    )
                    new_http_headers_raw = st.text_area(
                        "Headers JSON (vault refs supported)",
                        value=new_http_headers_raw,
                        height=80, key=f"http_h_{check_name}",
                        placeholder='{"Authorization": "Bearer ${vault:api_keys/zabbix}"}',
                    )

                new_extractor = st.text_input(
                    "Value extractor (optional: regex:PATTERN  or  json:path.to.value)",
                    value=cfg.get("value_extractor", ""), key=f"ext_{check_name}",
                )

            col_save, col_delete = st.columns([1, 1])
            with col_save:
                if st.button("💾 Save", key=f"save_{check_name}", use_container_width=True, type="primary"):
                    try:
                        headers_parsed = json.loads(new_http_headers_raw) if new_http_headers_raw.strip() else {}
                    except Exception:
                        headers_parsed = {}
                        st.warning("Invalid headers JSON — saved as empty.")

                    update_check_config(
                        check_name,
                        threshold=new_threshold,
                        interval_minutes=new_interval,
                        severity=new_severity,
                        enabled=new_enabled,
                        compare=new_compare,
                        backend=new_backend,
                        device_type=new_device_type,
                        cmd=new_cmd,
                        mcp_tool=new_mcp_tool,
                        mcp_action=new_mcp_action,
                        value_extractor=new_extractor,
                        http_url=new_http_url,
                        http_json_path=new_http_json_path,
                        http_headers=headers_parsed,
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
    st.caption(
        "Pick a backend — SSH command, MCP tool action, HTTP endpoint, or ICMP ping. "
        "Use the value extractor to pull a number out of a structured response."
    )

    add_backend = st.selectbox(
        "Backend",
        options=BACKEND_OPTIONS,
        format_func=lambda x: BACKEND_LABELS.get(x, x),
        key="add_backend",
    )

    with st.form("add_check", clear_on_submit=False):
        col_name, col_label, col_icon = st.columns([2, 2, 1])
        with col_name:
            new_name = st.text_input("Metric name (snake_case)", placeholder="swap_usage")
        with col_label:
            new_label = st.text_input("Display name", placeholder="Swap Usage")
        with col_icon:
            new_icon = st.text_input("Icon", value="📊", max_chars=2)

        new_device_type = st.text_input(
            "Device type (blank = global for HTTP/ping)",
            value="linux" if add_backend == BACKEND_SSH else "",
            placeholder="linux, windows, switch, firewall, ...",
        )

        # Backend-specific inputs
        add_cmd = ""
        add_mcp_tool = ""
        add_mcp_action = ""
        add_http_url = ""
        add_http_json_path = ""
        add_http_headers_raw = "{}"

        if add_backend == BACKEND_SSH:
            add_cmd = st.text_input(
                "SSH command",
                placeholder="free | awk '/Swap:/{if($2>0) printf \"%.0f\",$3/$2*100; else print 0}'",
            )
        elif add_backend == BACKEND_MCP:
            tool_names = _get_mcp_tool_names()
            add_mcp_tool = st.selectbox("MCP tool", options=tool_names or [""])
            add_mcp_action = st.text_input(
                "Action / command",
                placeholder="e.g. df -h, get_cluster_health, show interfaces",
            )
        elif add_backend == BACKEND_HTTP:
            add_http_url = st.text_input(
                "URL (supports {ip}, {name}, ${vault:cat/name})",
                placeholder="https://api.example.com/metrics",
            )
            add_http_json_path = st.text_input(
                "JSON path (optional)", placeholder="data.cpu.percent",
            )
            add_http_headers_raw = st.text_area(
                "Headers JSON (optional, vault refs supported)",
                value="{}", height=80,
                placeholder='{"Authorization": "Bearer ${vault:api_keys/token}"}',
            )

        add_extractor = st.text_input(
            "Value extractor (optional)",
            placeholder="regex:(\\d+\\.\\d+)   or   json:data.usage",
            help="Used when the raw output is not directly numeric.",
        )

        col_t, col_u, col_c, col_s, col_i = st.columns(5)
        with col_t:
            new_thresh_raw = st.text_input("Threshold", value="80")
        with col_u:
            new_unit = st.text_input("Unit", value="%", max_chars=5)
        with col_c:
            new_compare = st.selectbox(
                "Comparison", options=COMPARE_OPS,
                format_func=lambda x: COMPARE_LABELS.get(x, x),
            )
        with col_s:
            new_sev = st.selectbox("Severity", ["warning", "critical"])
        with col_i:
            new_int = st.number_input("Interval (min)", value=30, min_value=1, max_value=1440, step=5)

        submitted = st.form_submit_button("🚀 Add Metric", type="primary", use_container_width=True)

    if submitted and new_name and new_label:
        try:
            # Threshold parse
            try:
                new_thresh = float(new_thresh_raw)
            except ValueError:
                new_thresh = new_thresh_raw  # contains/regex/eq strings

            try:
                add_headers = json.loads(add_http_headers_raw) if add_http_headers_raw.strip() else {}
            except Exception:
                add_headers = {}

            add_custom_check(
                name=new_name, label=new_label,
                threshold=new_thresh, unit=new_unit, compare=new_compare,
                severity=new_sev, interval_minutes=new_int, icon=new_icon,
                backend=add_backend, device_type=new_device_type,
                cmd=add_cmd,
                mcp_tool=add_mcp_tool, mcp_action=add_mcp_action,
                value_extractor=add_extractor,
                http_url=add_http_url, http_json_path=add_http_json_path,
                http_headers=add_headers,
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
    if s.get("title", "").startswith("🚨 Otomatik:") or s.get("title", "").startswith("🚨 Auto:")
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
                    st.switch_page("Home.py")
