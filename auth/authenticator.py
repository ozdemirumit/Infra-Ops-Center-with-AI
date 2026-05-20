"""
Authentication module.
Login screen, session management, and role control.
Passwords are hashed with bcrypt.
"""

import streamlit as st
import bcrypt
from config.settings import settings
from logging_config.logger import get_logger, audit_log, AuditEvent

logger = get_logger("auth")


def _verify_password(plain_password: str, hashed_password: str) -> bool:
    """Compares a plain text password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8")
        )
    except Exception:
        return False


def _try_local_login(username: str, password: str) -> bool:
    """Try local bcrypt-based authentication. Returns True on success."""
    if username == settings.APP_USERNAME and _verify_password(password, settings.APP_PASSWORD_HASH):
        st.session_state["authenticated"] = True
        st.session_state["username"] = username
        st.session_state["display_name"] = username
        st.session_state["email"] = ""
        st.session_state["role"] = "admin" if username in settings.ADMIN_USERS else "viewer"
        st.session_state["auth_source"] = "local"
        logger.info(f"Local login: {username}")
        audit_log(AuditEvent.LOGIN_SUCCESS, user=username, detail="Local login")
        return True
    return False


def _try_ldap_login(username: str, password: str) -> bool:
    """Try LDAP authentication. Returns True on success."""
    try:
        from auth.ldap_auth import authenticate as ldap_authenticate, is_ldap_enabled
    except Exception:
        return False

    if not is_ldap_enabled():
        return False

    user_info = ldap_authenticate(username, password)
    if not user_info:
        return False

    st.session_state["authenticated"] = True
    st.session_state["username"] = user_info["username"]
    st.session_state["display_name"] = user_info.get("display_name", username)
    st.session_state["email"] = user_info.get("email", "")
    st.session_state["role"] = user_info.get("role", "viewer")
    st.session_state["auth_source"] = "ldap"
    logger.info(f"LDAP login: {username} (role={user_info.get('role')})")
    audit_log(AuditEvent.LOGIN_SUCCESS, user=username, detail=f"LDAP login (role={user_info.get('role')})")
    return True


def _show_login_page():
    """Displays the login form. Updates session state on successful login."""
    st.markdown(
        """
        <style>
        .login-title {
            text-align: center;
            font-size: 1.8rem;
            font-weight: 700;
            margin-bottom: 8px;
            color: #e0e0e0;
        }
        .login-subtitle {
            text-align: center;
            font-size: 0.95rem;
            color: #888;
            margin-bottom: 30px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Check if LDAP is enabled
    ldap_enabled = False
    try:
        from auth.ldap_auth import is_ldap_enabled
        ldap_enabled = is_ldap_enabled()
    except Exception:
        pass

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown('<div class="login-title">🛡️ Infra Ops Center with AI</div>', unsafe_allow_html=True)
        st.markdown('<div class="login-subtitle">Sign in to continue</div>', unsafe_allow_html=True)

        # Auth source selector (only shown when LDAP is enabled)
        if ldap_enabled:
            auth_source = st.radio(
                "Sign in with:",
                ["🏢 LDAP / Active Directory", "🔑 Local Account"],
                horizontal=True,
                key="login_source",
            )
            use_ldap = auth_source.startswith("🏢")
        else:
            use_ldap = False

        with st.form("login_form"):
            username = st.text_input("👤 Username", placeholder="username")
            password = st.text_input("🔒 Password", type="password", placeholder="••••••••")
            submit = st.form_submit_button("Sign In", use_container_width=True, type="primary")

            if submit:
                if not username or not password:
                    st.error("Username and password are required.")
                    return

                success = False
                if use_ldap:
                    success = _try_ldap_login(username, password)
                    if not success:
                        logger.warning(f"LDAP login failed: {username}")
                        audit_log(AuditEvent.LOGIN_FAILURE, user=username, detail="LDAP auth failed", success=False)
                        st.error("❌ LDAP authentication failed. Check username, password, or contact your administrator.")
                        return
                else:
                    success = _try_local_login(username, password)
                    if not success:
                        # Fallback: try LDAP automatically if enabled, helpful when admin disabled the toggle
                        # (Off by default for clarity)
                        logger.warning(f"Local login failed: {username}")
                        audit_log(AuditEvent.LOGIN_FAILURE, user=username, detail="Invalid password", success=False)
                        st.error("❌ Invalid username or password.")
                        return

                if success:
                    st.rerun()


def check_auth() -> bool:
    """
    Authentication check.
    Shows login page and returns False if not logged in.
    Returns True if logged in.
    """
    if not settings.APP_PASSWORD_HASH:
        # Skip auth if hash is not defined (development mode)
        st.session_state.setdefault("authenticated", True)
        st.session_state.setdefault("username", "admin")
        st.session_state.setdefault("role", "admin")
        return True

    if not st.session_state.get("authenticated", False):
        _show_login_page()
        return False

    return True


def logout():
    """Ends the session."""
    username = st.session_state.get("username", "unknown")
    logger.info(f"Logout: {username}")
    audit_log(AuditEvent.LOGOUT, user=username, detail="User logged out")
    for key in ["authenticated", "username", "role"]:
        st.session_state.pop(key, None)
    st.rerun()


def get_current_user() -> str:
    """Returns the active username."""
    return st.session_state.get("username", "anonymous")


def is_admin() -> bool:
    """Checks if the user is an admin."""
    return st.session_state.get("role", "viewer") == "admin"
