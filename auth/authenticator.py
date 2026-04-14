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


def _show_login_page():
    """Displays the login form. Updates session state on successful login."""
    st.markdown(
        """
        <style>
        .login-container {
            max-width: 420px;
            margin: 80px auto;
            padding: 40px;
            border-radius: 16px;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }
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

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown('<div class="login-title">🛡️ Infra Ops Center with AI</div>', unsafe_allow_html=True)
        st.markdown('<div class="login-subtitle">Sign in to continue</div>', unsafe_allow_html=True)

        with st.form("login_form"):
            username = st.text_input("👤 Username", placeholder="admin")
            password = st.text_input("🔒 Password", type="password", placeholder="••••••••")
            submit = st.form_submit_button("Sign In", use_container_width=True, type="primary")

            if submit:
                if not username or not password:
                    st.error("Username and password are required.")
                    return

                # Password verification
                if username == settings.APP_USERNAME and _verify_password(password, settings.APP_PASSWORD_HASH):
                    st.session_state["authenticated"] = True
                    st.session_state["username"] = username
                    st.session_state["role"] = "admin" if username in settings.ADMIN_USERS else "viewer"
                    logger.info(f"Successful login: {username}")
                    audit_log(AuditEvent.LOGIN_SUCCESS, user=username, detail="User logged in")
                    st.rerun()
                else:
                    logger.warning(f"Failed login attempt: {username}")
                    audit_log(AuditEvent.LOGIN_FAILURE, user=username, detail="Invalid password", success=False)
                    st.error("❌ Invalid username or password.")


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
