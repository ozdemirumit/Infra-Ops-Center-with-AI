"""
📧 Email Settings
Configure SMTP for notifications (workflow notify steps, incident alerts).
The password lives in the encrypted vault under `api_keys/smtp_password`.
"""

import os
import re
import tempfile
from pathlib import Path

import streamlit as st

from auth.authenticator import check_auth, is_admin
from ui.sidebar import render_sidebar
from config.settings import settings
from core.notifier import is_email_configured, send_email, test_smtp_connection

st.set_page_config(page_title="Email Settings", page_icon="📧", layout="wide")

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


ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _save_env_keys(updates: dict[str, str]) -> None:
    """Update keys in .env preserving the rest. Atomic write."""
    from logging_config.atomic_io import _get_lock

    text = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    lines = text.splitlines(keepends=True)
    replaced = set()
    new_lines = []
    for line in lines:
        matched = False
        for k, v in updates.items():
            if re.match(rf"^\s*{re.escape(k)}\s*=", line):
                new_lines.append(f"{k}={v}\n")
                replaced.add(k)
                matched = True
                break
        if not matched:
            new_lines.append(line)
    for k, v in updates.items():
        if k not in replaced:
            new_lines.append(f"{k}={v}\n")

    lock = _get_lock(str(ENV_PATH))
    with lock:
        fd, tmp = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=ENV_PATH.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        os.replace(tmp, ENV_PATH)

    for k, v in updates.items():
        os.environ[k] = v


# ─── UI ─────────────────────────────────────────────────────────────

st.title("📧 Email Settings")
st.markdown(
    "Configure the SMTP server used for workflow notifications and incident "
    "alerts. The password is stored encrypted in the credential vault "
    "(`api_keys/smtp_password`) — never written to `.env`."
)

if is_email_configured():
    st.success("✅ SMTP is configured.")
else:
    st.info("ℹ️ SMTP not configured yet. Fill in host + from address below.")

tab_cfg, tab_test = st.tabs(["⚙️ Configuration", "🧪 Test"])

# ═══════════════════════════════════════════════════════════════
# TAB 1 — CONFIG
# ═══════════════════════════════════════════════════════════════

with tab_cfg:
    with st.form("smtp_form"):
        col_host, col_port = st.columns([3, 1])
        with col_host:
            host = st.text_input("SMTP host", value=settings.SMTP_HOST,
                                 placeholder="smtp.gmail.com")
        with col_port:
            port = st.number_input("Port", min_value=1, max_value=65535,
                                   value=int(settings.SMTP_PORT))

        col_tls, col_ssl = st.columns(2)
        with col_tls:
            use_tls = st.checkbox("Use STARTTLS", value=settings.SMTP_USE_TLS,
                                  help="Standard for port 587.")
        with col_ssl:
            use_ssl = st.checkbox("Use SSL (implicit TLS)",
                                  value=settings.SMTP_USE_SSL,
                                  help="Standard for port 465.")

        user = st.text_input("SMTP username", value=settings.SMTP_USER,
                             placeholder="ops@example.com",
                             help="Leave blank for unauthenticated relays.")

        # Password — only ever written to vault, never to env / form state.
        current_pwd_set = False
        try:
            from auth.vault import has_secret
            current_pwd_set = has_secret("api_keys", "smtp_password")
        except Exception:
            pass

        pwd_label = "SMTP password (already set — leave blank to keep)" if current_pwd_set \
            else "SMTP password (will be encrypted)"
        password = st.text_input(pwd_label, type="password",
                                 placeholder="••••••••" if current_pwd_set else "")

        sender = st.text_input("From address", value=settings.SMTP_FROM,
                               placeholder="ai-ops@example.com")

        default_to = st.text_input(
            "Default recipient(s)",
            value=settings.SMTP_DEFAULT_TO,
            placeholder="oncall@example.com, ops-team@example.com",
            help="Comma-separated. Workflow steps can override per-step with `to:`.",
        )

        timeout = st.number_input("Connection timeout (seconds)",
                                  min_value=1, max_value=120,
                                  value=int(settings.SMTP_TIMEOUT))

        col_save, col_clear = st.columns([1, 1])
        with col_save:
            submitted = st.form_submit_button("💾 Save settings",
                                              type="primary",
                                              use_container_width=True)
        with col_clear:
            clear_pwd = st.form_submit_button("🗑️ Clear stored password",
                                              use_container_width=True)

    if submitted:
        try:
            _save_env_keys({
                "SMTP_HOST": host.strip(),
                "SMTP_PORT": str(int(port)),
                "SMTP_USER": user.strip(),
                "SMTP_FROM": sender.strip(),
                "SMTP_USE_TLS": "true" if use_tls else "false",
                "SMTP_USE_SSL": "true" if use_ssl else "false",
                "SMTP_TIMEOUT": str(int(timeout)),
                "SMTP_DEFAULT_TO": default_to.strip(),
            })
            # Refresh settings instance attrs
            settings.SMTP_HOST = host.strip()
            settings.SMTP_PORT = int(port)
            settings.SMTP_USER = user.strip()
            settings.SMTP_FROM = sender.strip()
            settings.SMTP_USE_TLS = use_tls
            settings.SMTP_USE_SSL = use_ssl
            settings.SMTP_TIMEOUT = int(timeout)
            settings.SMTP_DEFAULT_TO = default_to.strip()

            if password:
                from auth.vault import set_secret
                set_secret("api_keys", "smtp_password", password,
                           description="SMTP password for outbound email",
                           tags=["smtp", "email"])
                st.success("✅ Settings + password saved (password encrypted in vault).")
            else:
                st.success("✅ Settings saved. Password unchanged.")
        except Exception as e:
            st.error(f"❌ {type(e).__name__}: {e}")

    if clear_pwd:
        try:
            from auth.vault import delete_secret
            if delete_secret("api_keys", "smtp_password"):
                st.success("✅ Stored password removed from vault.")
            else:
                st.info("No stored password to remove.")
        except Exception as e:
            st.error(f"❌ {e}")


# ═══════════════════════════════════════════════════════════════
# TAB 2 — TEST
# ═══════════════════════════════════════════════════════════════

with tab_test:
    st.markdown("##### Test the connection")
    if st.button("🔌 Probe SMTP server", type="primary"):
        with st.spinner("Connecting…"):
            result = test_smtp_connection()
        if result["ok"]:
            st.success(f"✅ Connected to {settings.SMTP_HOST}:{settings.SMTP_PORT}")
        else:
            st.error(f"❌ {result['error']}")

    st.divider()
    st.markdown("##### Send a test email")
    test_to = st.text_input("Recipient", value=settings.SMTP_DEFAULT_TO,
                            placeholder="you@example.com")
    test_subject = st.text_input("Subject",
                                 value="AI Ops Center — test email")
    test_body = st.text_area(
        "Body",
        value=(
            "This is a test email from the AI Ops Center.\n"
            "If you received this, SMTP is configured correctly."
        ),
        height=120,
    )

    if st.button("📤 Send test", type="primary",
                 disabled=not is_email_configured()):
        with st.spinner("Sending…"):
            res = send_email(subject=test_subject, body=test_body, to=test_to)
        if res["sent"]:
            st.success(f"✅ Sent to {', '.join(res['recipients'])}")
        else:
            st.error(f"❌ {res['error']}")


# ═══════════════════════════════════════════════════════════════
# Snippet helper
# ═══════════════════════════════════════════════════════════════

st.divider()
st.markdown("##### 📋 Use email in a workflow")
st.code(
    """- id: notify_team
  type: notify
  channel: email
  to: [oncall@example.com, ops@example.com]   # optional — falls back to SMTP_DEFAULT_TO
  subject: "Disk full on {{ inputs.server_name }}"
  message: |
    Disk usage on {{ inputs.server_name }} reached {{ inputs.value }}{{ inputs.unit }}.
    Investigation: {{ investigate.summary }}
  # html: true       # optional — pass an HTML body""",
    language="yaml",
)
