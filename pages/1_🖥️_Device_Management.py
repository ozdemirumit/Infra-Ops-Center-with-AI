"""
🖥️ Device Management Page
Add, edit, and delete devices.
Passwords are encrypted and stored using Fernet.
"""

import streamlit as st
import os
from auth.authenticator import check_auth, is_admin
from devices.storage import DeviceStorage, DEVICE_TYPES

st.set_page_config(page_title="Device Management", page_icon="🖥️", layout="wide")

# Inject Modern UI CSS
css_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui", "style.css")
if os.path.exists(css_path):
    with open(css_path, "r", encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# Auth check
if not check_auth():
    st.stop()

if not is_admin():
    st.error("⛔ You do not have permission to access this page. Please log in with an admin account.")
    st.stop()

st.title("🖥️ Device Management")
st.markdown("Add, edit, or delete your servers and network devices. Passwords are stored encrypted.")

# --- REGISTERED DEVICES ---
st.header("📋 Registered Devices")

devices = DeviceStorage.list_all()

if not devices:
    st.info("No registered devices yet. Use the form below to add a new device.")
else:
    for device in devices:
        dtype_info = DEVICE_TYPES.get(device["type"], {"label": device["type"], "icon": "❓"})

        with st.expander(f"{dtype_info['icon']} **{device['name']}** — `{device['ip']}` ({dtype_info['label']})", expanded=False):
            col1, col2, col3 = st.columns([3, 3, 2])

            with col1:
                st.text(f"Type: {dtype_info['label']}")
                st.text(f"IP: {device['ip']}")
            with col2:
                st.text(f"Username: {device['user']}")
                st.text(f"Password: {'●●●●●●' if device['has_password'] else '⚠️ Not set'}")
                if device.get("hostname"):
                    st.text(f"Hostname: {device['hostname']}")
                if device.get("os"):
                    st.text(f"OS: {device['os']}")
            with col3:
                # Edit button
                if st.button(f"✏️ Edit", key=f"edit_{device['id']}", use_container_width=True):
                    st.session_state["editing_device"] = device["id"]
                    st.rerun()

                # Delete button
                if st.button(f"🗑️ Delete", key=f"del_{device['id']}", use_container_width=True, type="secondary"):
                    DeviceStorage.delete(device["id"])
                    st.success(f"✅ '{device['name']}' deleted.")
                    st.rerun()

st.divider()

# --- DEVICE EDIT FORM ---
editing_id = st.session_state.get("editing_device")
if editing_id:
    st.header("✏️ Edit Device")

    device_data = DeviceStorage.get_by_id(editing_id)
    if device_data:
        with st.form("edit_form"):
            type_options = list(DEVICE_TYPES.keys())
            type_labels = [DEVICE_TYPES[t]["label"] for t in type_options]
            current_idx = type_options.index(device_data["type"]) if device_data["type"] in type_options else 0

            edit_name = st.text_input("Device Name", value=device_data["name"])
            edit_type = st.selectbox("Device Type", type_options, index=current_idx, format_func=lambda x: DEVICE_TYPES[x]["label"])

            c1, c2, c3 = st.columns(3)
            with c1:
                edit_ip = st.text_input("IP Address", value=device_data["ip"])
                edit_user = st.text_input("Username", value=device_data["user"])
                edit_pwd = st.text_input("New Password (leave blank to keep current)", type="password")
            with c2:
                edit_hostname = st.text_input("Hostname", value=device_data.get("hostname", ""))
                edit_os = st.text_input("OS", value=device_data.get("os", ""))
                edit_cpu = st.text_input("CPU", value=device_data.get("cpu", ""))
            with c3:
                edit_ram = st.text_input("RAM", value=device_data.get("ram", ""))
                edit_disk = st.text_input("Disk", value=device_data.get("disk", ""))
                edit_role = st.text_input("Role", value=device_data.get("role", ""))

            edit_location = st.text_input("Location", value=device_data.get("location", ""))
            edit_notes = st.text_area("Notes", value=device_data.get("notes", ""))

            col1, col2 = st.columns(2)
            with col1:
                if st.form_submit_button("💾 Save", use_container_width=True, type="primary"):
                    DeviceStorage.update(
                        editing_id, edit_name, edit_type, edit_ip, edit_user,
                        password=edit_pwd if edit_pwd else None,
                        hostname=edit_hostname, os=edit_os, cpu=edit_cpu,
                        ram=edit_ram, disk=edit_disk, role=edit_role,
                        location=edit_location, notes=edit_notes
                    )
                    st.session_state.pop("editing_device", None)
                    st.success("✅ Device updated!")
                    st.rerun()
            with col2:
                if st.form_submit_button("❌ Cancel", use_container_width=True):
                    st.session_state.pop("editing_device", None)
                    st.rerun()
    else:
        st.error("Device not found.")
        st.session_state.pop("editing_device", None)

    st.divider()

# --- ADD NEW DEVICE ---
st.header("➕ Add New Device")

with st.form("add_device_form", clear_on_submit=True):
    type_options = list(DEVICE_TYPES.keys())

    col1, col2 = st.columns(2)
    with col1:
        new_name = st.text_input("Device Name *", placeholder="e.g. Production Server 1")
        new_type = st.selectbox("Device Type *", type_options, format_func=lambda x: DEVICE_TYPES[x]["label"])
        new_ip = st.text_input("IP Address *", placeholder="192.168.1.x")
    with col2:
        new_user = st.text_input("Username *", placeholder="root")
        new_pwd = st.text_input("Password *", type="password")

    submitted = st.form_submit_button("➕ Add Device", use_container_width=True, type="primary")

    if submitted:
        if not all([new_name, new_type, new_ip, new_user, new_pwd]):
            st.error("❌ Please fill in all required fields (*).")
        else:
            device_id = DeviceStorage.add(new_name, new_type, new_ip, new_user, new_pwd)
            st.success(f"✅ '{new_name}' added successfully! Edit the device to enter detailed inventory information. (ID: {device_id})")
            st.rerun()
