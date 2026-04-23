"""
📝 Audit Log Viewer
Displays audit trail (Syslog RFC 5424 format) with filtering.
"""

import streamlit as st
import os
import re
from pathlib import Path
from datetime import datetime, timedelta
from auth.authenticator import check_auth, is_admin
from ui.sidebar import render_sidebar

st.set_page_config(page_title="Audit Log", page_icon="📝", layout="wide")

# Inject CSS
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

st.title("📝 Audit Log")
st.markdown("All system events in Syslog RFC 5424 format.")

# ─── Log Files ────────────────────────────────────────────────────────
LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"

LOG_FILES = {
    "🔐 Audit Trail": "audit.log",
    "📦 Application": "app.log",
    "🤖 AI Proxy": "proxy.log",
    "🛠️ Tools": "tools.log",
}

# ─── Log Selector ─────────────────────────────────────────────────────
col_select, col_lines, col_refresh = st.columns([3, 1, 1])

with col_select:
    selected_log = st.selectbox("Log Source", list(LOG_FILES.keys()))

with col_lines:
    max_lines = st.number_input("Lines", value=200, min_value=50, max_value=5000, step=50)

with col_refresh:
    st.write("")
    if st.button("🔄 Refresh", use_container_width=True):
        st.rerun()

log_file = LOGS_DIR / LOG_FILES[selected_log]

if not log_file.exists():
    st.info(f"Log file not yet created: `{log_file.name}`")
    st.stop()

# ─── Filters ──────────────────────────────────────────────────────────
st.divider()

filter_cols = st.columns(4)

with filter_cols[0]:
    search_term = st.text_input("🔍 Search", placeholder="keyword, IP, command...")

with filter_cols[1]:
    level_filter = st.multiselect(
        "Level",
        ["DEBUG", "INFO", "WARN", "WARNING", "ERR", "ERROR", "CRIT"],
        default=[],
    )

with filter_cols[2]:
    event_filter = st.multiselect(
        "Event Type",
        ["LOGIN_SUCCESS", "LOGIN_FAILURE", "LOGOUT",
         "COMMAND_EXECUTE", "COMMAND_RESULT",
         "AI_REQUEST", "AI_RESPONSE",
         "DEVICE_ADD", "DEVICE_UPDATE", "DEVICE_DELETE",
         "DATA_FILTERED"],
        default=[],
    )

with filter_cols[3]:
    time_range = st.selectbox(
        "Time Range",
        ["All", "Last 1 hour", "Last 24 hours", "Last 7 days"],
    )

# ─── Load & Filter ────────────────────────────────────────────────────
try:
    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
except Exception as e:
    st.error(f"Could not read log file: {e}")
    st.stop()

# Take last N lines first
lines = all_lines[-max_lines:] if len(all_lines) > max_lines else all_lines

# Apply filters
def _line_matches(line: str) -> bool:
    if search_term and search_term.lower() not in line.lower():
        return False
    if level_filter and not any(lvl in line for lvl in level_filter):
        return False
    if event_filter and not any(ev in line for ev in event_filter):
        return False

    if time_range != "All":
        # Extract timestamp (RFC 5424: 2026-04-13T15:06:01.464...)
        ts_match = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", line)
        if ts_match:
            try:
                ts = datetime.fromisoformat(ts_match.group(1))
                now = datetime.now()
                if time_range == "Last 1 hour" and (now - ts) > timedelta(hours=1):
                    return False
                elif time_range == "Last 24 hours" and (now - ts) > timedelta(hours=24):
                    return False
                elif time_range == "Last 7 days" and (now - ts) > timedelta(days=7):
                    return False
            except Exception:
                pass

    return True

filtered = [line for line in lines if _line_matches(line)]

# ─── Stats ────────────────────────────────────────────────────────────
st.divider()

stat_cols = st.columns(4)
stat_cols[0].metric("Total Lines", len(all_lines))
stat_cols[1].metric("Shown", len(lines))
stat_cols[2].metric("Filtered", len(filtered))

# Count event types in filtered
event_counts = {}
for line in filtered:
    for event in ["LOGIN", "COMMAND_EXECUTE", "AI_RESPONSE", "DATA_FILTERED", "ERROR", "DEVICE_"]:
        if event in line:
            event_counts[event] = event_counts.get(event, 0) + 1
            break

top_event = max(event_counts.items(), key=lambda x: x[1]) if event_counts else ("None", 0)
stat_cols[3].metric("Top Event", f"{top_event[0]}", f"{top_event[1]} times" if top_event[1] else None)

# ─── Log Display ──────────────────────────────────────────────────────
st.divider()

if not filtered:
    st.info("No log entries match the current filters.")
else:
    # Color code lines
    def _colorize(line: str) -> str:
        line_html = line.replace("<", "&lt;").replace(">", "&gt;")
        if "ERR" in line or "ERROR" in line or "CRIT" in line:
            color = "#ef4444"
        elif "WARN" in line or "WARNING" in line:
            color = "#f59e0b"
        elif "LOGIN_SUCCESS" in line or "COMMAND_RESULT" in line:
            color = "#10b981"
        elif "INFO" in line:
            color = "#a8b3c5"
        else:
            color = "#6b7690"
        return f'<div style="color:{color};font-family:JetBrains Mono,monospace;font-size:0.78rem;padding:2px 0;">{line_html}</div>'

    log_html = "".join(_colorize(line) for line in reversed(filtered))
    st.markdown(
        f'<div style="background:rgba(0,0,0,0.35);border:1px solid rgba(255,255,255,0.08);'
        f'border-radius:10px;padding:12px;max-height:600px;overflow-y:auto;">'
        f'{log_html}</div>',
        unsafe_allow_html=True,
    )

    # Download filtered log
    st.download_button(
        "📥 Download Filtered Log",
        "".join(filtered),
        file_name=f"{log_file.stem}_filtered_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
        mime="text/plain",
    )
