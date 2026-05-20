"""
🏢 LDAP / Active Directory Settings
Configure LDAP authentication, test connection, and map LDAP groups to roles.
"""

import os
import streamlit as st
from auth.authenticator import check_auth, is_admin
from ui.sidebar import render_sidebar

st.set_page_config(page_title="LDAP Settings", page_icon="🏢", layout="wide")

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

from auth.ldap_auth import (
    get_config, save_config, is_available,
    test_connection, test_user_search, DEFAULT_CONFIG,
)

st.title("🏢 LDAP / Active Directory")
st.markdown("Configure enterprise directory authentication.")

# ── Library check ────────────────────────────────────────────────
if not is_available():
    st.error(
        "⚠️ **ldap3 library is not installed.**\n\n"
        "Install with:  `pip install ldap3`\n\n"
        "Then restart the application."
    )
    st.stop()

config = get_config()

tab_basic, tab_search, tab_roles, tab_test = st.tabs([
    "⚙️ Basic Settings",
    "🔎 User Search",
    "👥 Role Mapping",
    "🧪 Test & Verify",
])

# ═══════════════════════════════════════════════════════════════════
# TAB 1: BASIC SETTINGS
# ═══════════════════════════════════════════════════════════════════

with tab_basic:
    st.markdown("##### Server connection")

    enabled = st.toggle(
        "Enable LDAP authentication",
        value=config.get("enabled", False),
        help="When enabled, users see an LDAP login option on the sign-in screen.",
    )

    col_server, col_port = st.columns([3, 1])
    with col_server:
        server = st.text_input(
            "Server URI",
            value=config.get("server", ""),
            placeholder="ldap://dc.example.com  or  ldaps://dc.example.com",
        )
    with col_port:
        port = st.number_input(
            "Port", value=config.get("port", 389),
            min_value=1, max_value=65535, step=1,
            help="389 for plain/StartTLS · 636 for LDAPS",
        )

    col_ssl, col_tls = st.columns(2)
    with col_ssl:
        use_ssl = st.toggle(
            "Use SSL (LDAPS)", value=config.get("use_ssl", False),
            help="Connect via LDAPS on port 636",
        )
    with col_tls:
        use_tls = st.toggle(
            "Use StartTLS", value=config.get("use_tls", False),
            help="Upgrade plain connection to TLS",
        )

    base_dn = st.text_input(
        "Base DN *",
        value=config.get("base_dn", ""),
        placeholder="DC=example,DC=com",
        help="Root of your directory tree",
    )

    connect_timeout = st.number_input(
        "Connection timeout (seconds)",
        value=config.get("connect_timeout", 5),
        min_value=1, max_value=60, step=1,
    )

    # Save
    if st.button("💾 Save Basic Settings", type="primary", use_container_width=True):
        new_config = dict(config)
        new_config.update({
            "enabled": enabled,
            "server": server.strip(),
            "port": int(port),
            "use_ssl": use_ssl,
            "use_tls": use_tls,
            "base_dn": base_dn.strip(),
            "connect_timeout": int(connect_timeout),
        })
        save_config(new_config)
        st.success("✅ Saved.")
        st.rerun()


# ═══════════════════════════════════════════════════════════════════
# TAB 2: USER SEARCH
# ═══════════════════════════════════════════════════════════════════

with tab_search:
    st.markdown("##### How users are located in the directory")
    st.caption(
        "Two strategies are supported. Use **DN Template** for simple flat directories, "
        "or **Service Bind + Search** for production AD environments."
    )

    strategy = st.radio(
        "Strategy",
        ["Service Bind + Search (recommended for AD)", "DN Template (simple)"],
        index=0 if not config.get("user_dn_template") else 1,
    )

    if strategy.startswith("DN Template"):
        st.markdown("**DN Template** — direct bind using a DN pattern")
        user_dn_template = st.text_input(
            "User DN Template",
            value=config.get("user_dn_template", ""),
            placeholder="CN={username},OU=Users,DC=example,DC=com",
            help="Use {username} as placeholder",
        )
        # Clear search fields when using template
        bind_dn = ""
        bind_password = ""
        user_search_filter = config.get("user_search_filter", "(sAMAccountName={username})")
        user_search_base = ""
    else:
        st.markdown("**Service Bind + Search** — bind with a service account, then search for the user")
        user_dn_template = ""

        col_bdn, col_bpwd = st.columns(2)
        with col_bdn:
            bind_dn = st.text_input(
                "Service Account DN",
                value=config.get("bind_dn", ""),
                placeholder="CN=svc-ldap,OU=Service,DC=example,DC=com",
            )
        with col_bpwd:
            bind_password = st.text_input(
                "Service Account Password",
                value=config.get("bind_password", ""),
                type="password",
            )

        user_search_base = st.text_input(
            "User Search Base (optional, defaults to Base DN)",
            value=config.get("user_search_base", ""),
            placeholder="OU=Users,DC=example,DC=com",
        )

        user_search_filter = st.text_input(
            "User Search Filter",
            value=config.get("user_search_filter", "(sAMAccountName={username})"),
            help="Use {username} placeholder. AD: (sAMAccountName={username}). OpenLDAP: (uid={username})",
        )

    st.markdown("##### Attribute mapping")
    col_a1, col_a2, col_a3 = st.columns(3)
    with col_a1:
        attr_username = st.text_input(
            "Username attribute",
            value=config.get("attribute_username", "sAMAccountName"),
            help="AD: sAMAccountName · OpenLDAP: uid",
        )
    with col_a2:
        attr_email = st.text_input("Email attribute", value=config.get("attribute_email", "mail"))
    with col_a3:
        attr_display = st.text_input(
            "Display name attribute",
            value=config.get("attribute_displayname", "displayName"),
        )

    if st.button("💾 Save User Search Settings", type="primary", use_container_width=True):
        new_config = dict(config)
        new_config.update({
            "user_dn_template": user_dn_template.strip(),
            "bind_dn": bind_dn.strip(),
            "bind_password": bind_password,
            "user_search_base": user_search_base.strip(),
            "user_search_filter": user_search_filter.strip(),
            "attribute_username": attr_username.strip(),
            "attribute_email": attr_email.strip(),
            "attribute_displayname": attr_display.strip(),
        })
        save_config(new_config)
        st.success("✅ Saved.")
        st.rerun()


# ═══════════════════════════════════════════════════════════════════
# TAB 3: ROLE MAPPING
# ═══════════════════════════════════════════════════════════════════

with tab_roles:
    st.markdown("##### Map LDAP groups to application roles")
    st.caption(
        "When a user logs in, their `memberOf` groups are checked against these lists. "
        "If a user belongs to an admin group → admin role; viewer group → viewer; else → default."
    )

    admin_groups_text = st.text_area(
        "Admin Groups (one DN per line)",
        value="\n".join(config.get("admin_groups", [])),
        height=100,
        placeholder="CN=IT-Admins,OU=Groups,DC=example,DC=com\nCN=Domain Admins,CN=Users,DC=example,DC=com",
    )

    viewer_groups_text = st.text_area(
        "Viewer Groups (one DN per line)",
        value="\n".join(config.get("viewer_groups", [])),
        height=100,
        placeholder="CN=Helpdesk,OU=Groups,DC=example,DC=com",
    )

    default_role = st.selectbox(
        "Default role (when no group matches)",
        ["viewer", "admin", "none"],
        index=["viewer", "admin", "none"].index(config.get("default_role", "viewer"))
        if config.get("default_role") in ["viewer", "admin", "none"] else 0,
        help="'none' will deny access entirely if no group matches",
    )

    if st.button("💾 Save Role Mapping", type="primary", use_container_width=True):
        admin_groups = [g.strip() for g in admin_groups_text.splitlines() if g.strip()]
        viewer_groups = [g.strip() for g in viewer_groups_text.splitlines() if g.strip()]
        new_config = dict(config)
        new_config.update({
            "admin_groups": admin_groups,
            "viewer_groups": viewer_groups,
            "default_role": default_role,
        })
        save_config(new_config)
        st.success(f"✅ Saved {len(admin_groups)} admin group(s), {len(viewer_groups)} viewer group(s).")
        st.rerun()


# ═══════════════════════════════════════════════════════════════════
# TAB 4: TEST & VERIFY
# ═══════════════════════════════════════════════════════════════════

with tab_test:
    st.markdown("##### Verify your LDAP configuration")

    col_conn_btn, col_user_btn = st.columns(2)

    with col_conn_btn:
        st.markdown("**Connection Test**")
        st.caption("Verifies server is reachable and service account can bind.")
        if st.button("🔌 Test Connection", use_container_width=True, type="primary"):
            with st.spinner("Connecting to LDAP server…"):
                ok, msg = test_connection(config)
            if ok:
                st.success(msg)
            else:
                st.error(f"❌ {msg}")

    with col_user_btn:
        st.markdown("**User Search Test**")
        st.caption("Searches for a specific user without authenticating.")
        test_user = st.text_input("Test username", placeholder="jdoe", key="test_user_search")
        if st.button("🔎 Search User", use_container_width=True):
            if not test_user:
                st.warning("Enter a username first.")
            else:
                with st.spinner(f"Searching for {test_user}…"):
                    ok, msg, info = test_user_search(test_user, config)
                if ok:
                    st.success(msg)
                    st.json(info)
                else:
                    st.error(f"❌ {msg}")

    st.divider()

    st.markdown("**🔐 Full Authentication Test**")
    st.caption("Tries a real login — useful to verify groups and role mapping end-to-end.")

    col_u, col_p = st.columns(2)
    with col_u:
        auth_user = st.text_input("Username", key="auth_test_user")
    with col_p:
        auth_pwd = st.text_input("Password", type="password", key="auth_test_pwd")

    if st.button("🔐 Test Login", type="primary", use_container_width=True):
        if not auth_user or not auth_pwd:
            st.warning("Username and password required.")
        else:
            from auth.ldap_auth import authenticate
            # Force enable temporarily for test
            test_config = dict(config)
            test_config["enabled"] = True
            save_config(test_config)
            try:
                with st.spinner("Authenticating…"):
                    result = authenticate(auth_user, auth_pwd)
            finally:
                # Restore original enabled state
                save_config(config)

            if result:
                st.success(f"✅ Authenticated as **{result['display_name']}**")
                col1, col2 = st.columns(2)
                with col1:
                    st.text(f"Username: {result['username']}")
                    st.text(f"Email: {result.get('email', '—')}")
                    st.text(f"Role: {result['role']}")
                with col2:
                    st.text(f"DN: {result['dn']}")
                with st.expander(f"Groups ({len(result.get('groups', []))})"):
                    for g in result.get("groups", []):
                        st.text(g)
            else:
                st.error("❌ Authentication failed. Check logs for details.")

    st.divider()

    # ── Current config preview ─────────────────────────────────
    with st.expander("📋 Current Configuration (read-only)", expanded=False):
        # Mask sensitive fields
        preview = dict(config)
        if preview.get("bind_password"):
            preview["bind_password"] = "***MASKED***"
        st.json(preview)
