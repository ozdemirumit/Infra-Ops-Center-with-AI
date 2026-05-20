"""
🔐 Credential Vault
Centralized encrypted secret storage for all MCP tools, devices, LDAP, and API keys.
"""

import os
import streamlit as st
from auth.authenticator import check_auth, is_admin
from ui.sidebar import render_sidebar

st.set_page_config(page_title="Credential Vault", page_icon="🔐", layout="wide")

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

from auth.vault import (
    list_secrets, list_categories, set_secret, delete_secret,
    has_secret, get_secret,
    migrate_from_devices, migrate_from_env, migrate_from_ldap,
)

st.title("🔐 Credential Vault")
st.markdown(
    "Centralized encrypted storage for **all** application credentials. "
    "Uses Fernet AES-128 with the same key as device storage."
)

# ─── Stats ────────────────────────────────────────────────────
categories = list_categories()
total_count = sum(c["count"] for c in categories)

stat_cols = st.columns(min(len(categories) + 1, 6))
stat_cols[0].metric("Total Secrets", total_count)
for i, cat in enumerate(categories[:5]):
    icon = {
        "devices": "🖥️", "ldap": "🏢", "api_keys": "🔑",
        "proxy": "🛡️", "custom_tools": "🔧",
    }.get(cat["category"], "📦")
    stat_cols[i + 1].metric(f"{icon} {cat['category']}", cat["count"])

st.divider()

tab_browse, tab_add, tab_migrate = st.tabs([
    "🗂️ Browse Secrets",
    "➕ Add Secret",
    "📦 Migrate Existing",
])

# ═══════════════════════════════════════════════════════════════
# TAB 1: BROWSE
# ═══════════════════════════════════════════════════════════════

with tab_browse:
    col_filter, col_search = st.columns([1, 2])
    with col_filter:
        cat_options = ["All"] + [c["category"] for c in categories]
        selected_cat = st.selectbox("Filter category", cat_options)
    with col_search:
        search = st.text_input("🔍 Search name or description", placeholder="").strip().lower()

    filter_cat = None if selected_cat == "All" else selected_cat
    secrets = list_secrets(category=filter_cat)

    if search:
        secrets = [
            s for s in secrets
            if search in s["name"].lower() or search in (s["description"] or "").lower()
        ]

    if not secrets:
        st.info("No secrets in this category. Add one or migrate existing credentials.")
    else:
        for s in secrets:
            with st.container(border=True):
                col_info, col_actions = st.columns([4, 1])

                with col_info:
                    cat_icon = {
                        "devices": "🖥️", "ldap": "🏢", "api_keys": "🔑",
                        "proxy": "🛡️", "custom_tools": "🔧",
                    }.get(s["category"], "📦")
                    st.markdown(
                        f"**{cat_icon} {s['name']}**  "
                        f"<span style='color:#6b7690;font-size:0.75rem;'>"
                        f"`{s['category']}`</span>",
                        unsafe_allow_html=True,
                    )
                    if s["description"]:
                        st.caption(s["description"])
                    st.code(s["masked_preview"] or "(empty)", language=None)
                    if s["tags"]:
                        st.caption(" ".join(f"`{t}`" for t in s["tags"]))
                    if s["updated_at"]:
                        st.caption(f"Updated: {s['updated_at'][:16]}")

                with col_actions:
                    # Reveal
                    reveal_key = f"reveal_{s['key']}"
                    if st.button("👁️", key=f"reveal_btn_{s['key']}",
                                 use_container_width=True, help="Show value"):
                        st.session_state[reveal_key] = not st.session_state.get(reveal_key, False)

                    # Delete with confirmation
                    pending_del = st.session_state.get("_vault_pending_del")
                    if pending_del == s["key"]:
                        if st.button("✅ Confirm", key=f"confdel_{s['key']}",
                                     type="primary", use_container_width=True):
                            delete_secret(s["category"], s["name"])
                            st.session_state.pop("_vault_pending_del", None)
                            st.rerun()
                    else:
                        if st.button("🗑️", key=f"del_{s['key']}",
                                     use_container_width=True):
                            st.session_state["_vault_pending_del"] = s["key"]
                            st.rerun()

                if st.session_state.get(reveal_key):
                    revealed = get_secret(s["category"], s["name"])
                    st.code(revealed or "(decrypt failed or empty)", language=None)


# ═══════════════════════════════════════════════════════════════
# TAB 2: ADD
# ═══════════════════════════════════════════════════════════════

with tab_add:
    st.markdown("##### Add a new credential")

    with st.form("vault_add", clear_on_submit=True):
        col_cat, col_name = st.columns(2)
        with col_cat:
            cat_input = st.text_input(
                "Category",
                placeholder="e.g. api_keys, devices, custom_tools, ldap",
                help="Use lowercase; common ones: devices, api_keys, ldap, proxy, custom_tools",
            )
        with col_name:
            name_input = st.text_input(
                "Name (unique within category)",
                placeholder="e.g. zabbix_token, prod-server-01",
            )

        value_input = st.text_area(
            "Secret value",
            placeholder="The actual credential — will be encrypted with Fernet AES-128",
            height=80,
        )

        desc_input = st.text_input(
            "Description (optional, NOT encrypted)",
            placeholder="e.g. Production Zabbix API token",
        )

        tags_input = st.text_input(
            "Tags (comma-separated, optional)",
            placeholder="production, ssh, api",
        )

        submitted = st.form_submit_button("🔐 Store Secret", type="primary", use_container_width=True)

        if submitted:
            if not cat_input or not name_input or not value_input:
                st.error("❌ Category, name, and value are required.")
            else:
                try:
                    tags = [t.strip() for t in tags_input.split(",") if t.strip()] if tags_input else []
                    key = set_secret(cat_input, name_input, value_input, desc_input, tags)
                    st.success(f"✅ Stored as `{key}`")
                except Exception as e:
                    st.error(f"❌ {type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════
# TAB 3: MIGRATE
# ═══════════════════════════════════════════════════════════════

with tab_migrate:
    st.markdown(
        "##### Import existing credentials into the vault\n\n"
        "These migrations **copy** secrets — they do NOT delete from the original location, "
        "so legacy code keeps working until you fully cut over."
    )

    col_dev, col_env, col_ldap = st.columns(3)

    with col_dev:
        st.markdown("**🖥️ Device passwords**")
        st.caption("Copies passwords from `devices/devices.json` into the vault under `devices/{id}`.")
        if st.button("Migrate Devices →", use_container_width=True, key="mig_dev"):
            with st.spinner("Migrating…"):
                n = migrate_from_devices()
            st.success(f"✅ {n} device(s) imported.")
            st.rerun()

    with col_env:
        st.markdown("**🔑 API keys from .env**")
        st.caption("Copies ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, PROXY_API_KEY, PROXY_ADMIN_KEY into `api_keys/`.")
        if st.button("Migrate API Keys →", use_container_width=True, key="mig_env"):
            with st.spinner("Migrating…"):
                result = migrate_from_env()
            for prov, status in result.items():
                st.text(f"  {prov}: {status}")
            st.rerun()

    with col_ldap:
        st.markdown("**🏢 LDAP bind password**")
        st.caption("Copies LDAP service account password from `ldap_config.json` into `ldap/bind_password`.")
        if st.button("Migrate LDAP →", use_container_width=True, key="mig_ldap"):
            with st.spinner("Migrating…"):
                ok = migrate_from_ldap()
            if ok:
                st.success("✅ LDAP password imported.")
            else:
                st.info("No LDAP password found.")
            st.rerun()

    st.divider()
    st.markdown(
        "##### 💡 How to use vault values in your code\n\n"
        "```python\n"
        "from auth.vault import get_secret, set_secret\n\n"
        "# Fetch\n"
        "key = get_secret('api_keys', 'zabbix_token')\n\n"
        "# Store\n"
        "set_secret('custom_tools', 'jenkins_token', 'abc123',\n"
        "           description='Jenkins API token', tags=['ci'])\n"
        "```"
    )

    st.markdown(
        "##### 🔧 Use in custom MCP tool backend config\n\n"
        "When defining a custom HTTP MCP tool, reference vault secrets in headers/templates "
        "with `${vault:category/name}` placeholder syntax (auto-resolved at execution time):\n\n"
        "```json\n"
        "{\n"
        '  "headers": {\n'
        '    "Authorization": "Bearer ${vault:api_keys/zabbix_token}"\n'
        "  }\n"
        "}\n"
        "```"
    )
