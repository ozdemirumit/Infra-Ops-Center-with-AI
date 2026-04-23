"""
💾 Backup & Restore Page
Export devices, tools config, monitoring config, and knowledge base as a backup archive.
Import a backup to restore state.
"""

import streamlit as st
import os
import json
import zipfile
import tempfile
from pathlib import Path
from datetime import datetime
from auth.authenticator import check_auth, is_admin
from ui.sidebar import render_sidebar

st.set_page_config(page_title="Backup & Restore", page_icon="💾", layout="wide")

# CSS
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

st.title("💾 Backup & Restore")
st.markdown("Export or import system configuration: devices, tools, monitoring, and knowledge base.")

ROOT = Path(__file__).resolve().parent.parent

# Files to include in backup
BACKUP_ITEMS = {
    "Devices": ROOT / "devices" / "devices.json",
    "Sessions": ROOT / "sessions" / "sessions.json",
    "Tools Config": ROOT / "tools" / "tools_state.json",
    "Monitor State": ROOT / "monitor_state.json",
    "Knowledge Base": ROOT / "knowledge_base",
}

tab_export, tab_import, tab_devices = st.tabs(["📥 Export Backup", "📤 Import Backup", "📋 Devices Only (CSV)"])

# ═══════════════════════════════════════════════════════════════════
# EXPORT
# ═══════════════════════════════════════════════════════════════════
with tab_export:
    st.markdown("##### Select what to include in the backup")

    include = {}
    cols = st.columns(len(BACKUP_ITEMS))
    for i, (label, path) in enumerate(BACKUP_ITEMS.items()):
        exists = path.exists()
        with cols[i]:
            include[label] = st.checkbox(
                label, value=exists, disabled=not exists,
                help=f"{'✓ exists' if exists else '× missing'}: {path.relative_to(ROOT)}",
            )

    st.write("")
    if st.button("📦 Create Backup Archive", type="primary", use_container_width=True):
        # Build ZIP in memory
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tmp.close()

        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
            # Metadata
            meta = {
                "created_at": datetime.now().isoformat(),
                "app": "Infra Ops Center with AI",
                "version": "1.0",
                "items": [label for label, sel in include.items() if sel],
            }
            zf.writestr("backup_meta.json", json.dumps(meta, indent=2))

            for label, sel in include.items():
                if not sel:
                    continue
                path = BACKUP_ITEMS[label]
                if not path.exists():
                    continue
                if path.is_file():
                    zf.write(path, path.relative_to(ROOT))
                else:
                    for file in path.rglob("*"):
                        if file.is_file() and "__pycache__" not in str(file):
                            zf.write(file, file.relative_to(ROOT))

        with open(tmp.name, "rb") as f:
            data = f.read()
        os.unlink(tmp.name)

        st.success(f"✅ Backup ready — {len(data) / 1024:.1f} KB")
        st.download_button(
            "💾 Download Backup",
            data,
            file_name=f"infra_ops_backup_{ts}.zip",
            mime="application/zip",
            use_container_width=True,
        )

# ═══════════════════════════════════════════════════════════════════
# IMPORT
# ═══════════════════════════════════════════════════════════════════
with tab_import:
    st.markdown("##### Restore from a backup archive")
    st.warning("⚠️ Imports will **overwrite** existing data. Consider exporting a backup first.")

    uploaded = st.file_uploader("Upload backup ZIP", type=["zip"])

    if uploaded:
        # Read archive
        with zipfile.ZipFile(uploaded) as zf:
            # Read metadata
            meta = {}
            try:
                meta = json.loads(zf.read("backup_meta.json").decode("utf-8"))
            except Exception:
                st.error("❌ Invalid backup archive (missing backup_meta.json).")
                st.stop()

            st.info(
                f"📄 Archive created: {meta.get('created_at', '?')}\n\n"
                f"📦 Items: {', '.join(meta.get('items', []))}"
            )

            confirm = st.checkbox("I understand this will overwrite existing data.")

            if st.button("🚀 Restore Now", type="primary", disabled=not confirm):
                restored = 0
                errors = []

                for name in zf.namelist():
                    if name == "backup_meta.json":
                        continue
                    try:
                        target = ROOT / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(name) as src, open(target, "wb") as dst:
                            dst.write(src.read())
                        restored += 1
                    except Exception as e:
                        errors.append(f"{name}: {e}")

                st.success(f"✅ Restored {restored} files.")
                if errors:
                    st.error("Some files failed:\n" + "\n".join(errors[:10]))
                st.info("♻️ Reload the app for changes to take effect.")

# ═══════════════════════════════════════════════════════════════════
# DEVICES CSV
# ═══════════════════════════════════════════════════════════════════
with tab_devices:
    st.markdown("##### Export/Import devices as CSV")
    st.caption("CSV excludes passwords (security). For full backup including encrypted passwords, use the ZIP export above.")

    from devices.storage import DeviceStorage

    col_exp, col_imp = st.columns(2)

    with col_exp:
        st.markdown("**📥 Export Devices**")
        devices = DeviceStorage.list_all()
        if devices:
            # Build CSV without passwords
            import io
            import csv
            buf = io.StringIO()
            fieldnames = ["name", "type", "ip", "user", "hostname", "os", "cpu", "ram",
                          "disk", "location", "role", "notes"]
            writer = csv.DictWriter(buf, fieldnames=fieldnames)
            writer.writeheader()
            for d in devices:
                row = {k: d.get(k, "") for k in fieldnames}
                writer.writerow(row)

            st.download_button(
                "💾 Download CSV",
                buf.getvalue(),
                file_name=f"devices_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
                use_container_width=True,
            )
            st.caption(f"{len(devices)} devices")
        else:
            st.info("No devices to export.")

    with col_imp:
        st.markdown("**📤 Import Devices from CSV**")
        csv_file = st.file_uploader("CSV file", type=["csv"], key="csv_devices_import")

        if csv_file:
            import csv
            import io
            text = csv_file.read().decode("utf-8")
            reader = csv.DictReader(io.StringIO(text))
            rows = list(reader)

            st.dataframe(rows[:10], use_container_width=True)
            st.caption(f"Previewing first 10 of {len(rows)} rows.")

            default_pwd = st.text_input(
                "Default password for imported devices",
                type="password",
                help="CSV doesn't carry passwords. Provide a default (you can edit each later).",
            )

            if st.button("➕ Import", type="primary", disabled=not default_pwd):
                added = 0
                skipped = 0
                for row in rows:
                    try:
                        if not row.get("name") or not row.get("ip"):
                            skipped += 1
                            continue
                        DeviceStorage.add(
                            row["name"], row.get("type", "linux"),
                            row["ip"], row.get("user", "root"),
                            default_pwd,
                        )
                        added += 1
                    except Exception:
                        skipped += 1
                st.success(f"✅ Imported {added} devices ({skipped} skipped).")
                st.rerun()
