"""
📋 Server Inventory Page
Lists detailed inventory information of existing devices and allows filtering.
"""

import streamlit as st
import pandas as pd
import os
from auth.authenticator import check_auth
from ui.sidebar import render_sidebar
from devices.storage import DeviceStorage, DEVICE_TYPES

st.set_page_config(page_title="Server Inventory", page_icon="📋", layout="wide")

# Inject Modern UI CSS
css_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui", "style.css")
if os.path.exists(css_path):
    with open(css_path, "r", encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# Auth check
if not check_auth():
    st.stop()


render_sidebar()
st.title("📋 Server Inventory")
st.markdown("Detailed breakdown of all servers and network devices in the infrastructure.")

# Fetch device data
devices = DeviceStorage.list_all()

if not devices:
    st.info("No registered devices found.")
    st.stop()

# --- Statistics ---
total_devices = len(devices)
active_devices = sum(1 for d in devices if d.get('status', 'active') == 'active')

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Devices", total_devices)
col2.metric("Active", active_devices)
col3.metric("Linux Servers", sum(1 for d in devices if d['type'] == 'linux'))
col4.metric("Windows Servers", sum(1 for d in devices if d['type'] == 'windows'))

st.divider()

# --- Filters ---
st.subheader("🔍 Filtering")
f_col1, f_col2, f_col3 = st.columns(3)

with f_col1:
    search_query = st.text_input("Search (Hostname, Name, IP, OS)", "")
with f_col2:
    selected_types = st.multiselect(
        "Device Type",
        options=list(DEVICE_TYPES.keys()),
        format_func=lambda x: DEVICE_TYPES[x]["label"],
        default=[]
    )
with f_col3:
    status_options = ["active", "maintenance", "decommissioned"]
    selected_status = st.multiselect("Status", status_options, default=["active", "maintenance"])

# Data filtering
filtered_devices = []
for d in devices:
    # 1. Filter by type
    if selected_types and d["type"] not in selected_types:
        continue

    # 2. Filter by status
    if selected_status and d.get("status", "active") not in selected_status:
        continue

    # 3. Text search
    if search_query:
        q = search_query.lower()
        search_fields = [
            str(d.get("name", "")).lower(),
            str(d.get("hostname", "")).lower(),
            str(d.get("ip", "")).lower(),
            str(d.get("os", "")).lower(),
            str(d.get("role", "")).lower()
        ]
        if not any(q in f for f in search_fields):
            continue

    filtered_devices.append(d)

# --- Table View ---
if filtered_devices:
    # Flatten for Pandas DataFrame
    flat_data = []
    for d in filtered_devices:
        flat_data.append({
            "Type": DEVICE_TYPES.get(d["type"], {}).get("icon", "❓"),
            "Name": d.get("name", ""),
            "Hostname": d.get("hostname", ""),
            "IP Address": d.get("ip", ""),
            "OS": d.get("os", ""),
            "Role": d.get("role", ""),
            "CPU": d.get("cpu", ""),
            "RAM": d.get("ram", ""),
            "Disk": d.get("disk", ""),
            "Location": d.get("location", ""),
            "Status": d.get("status", "active"),
            "Notes": d.get("notes", "")
        })

    df = pd.DataFrame(flat_data)

    # Show all data as DataFrame
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True
    )
else:
    st.warning("No devices match the selected filters.")
