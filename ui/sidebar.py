"""
Sidebar — Enterprise control panel.
"""

import streamlit as st
from auth.authenticator import logout, get_current_user, is_admin
from devices.storage import DeviceStorage, DEVICE_TYPES
from config.settings import settings


def render_sidebar() -> dict:
    with st.sidebar:
        # Brand header
        st.markdown(
            "<div style='padding: 0.25rem 0 0.5rem 0.25rem; font-weight: 700; "
            "font-size: 0.95rem; color: #e4e7ec; letter-spacing: -0.01em;'>"
            "🛡️ Infra Ops Center</div>",
            unsafe_allow_html=True,
        )

        user = get_current_user()
        role = "⚡ Admin" if is_admin() else "👁️ Viewer"
        st.caption(f"{role} · `{user}`")

        if st.button("🚪 Logout", use_container_width=True):
            logout()

        st.divider()

        # AI Model
        st.markdown("##### 🤖 AI Model")

        PROVIDER_MODELS = {
            "anthropic": ["claude-opus-4-5", "claude-sonnet-4-5", "claude-3-5-haiku-latest"],
            "openai": ["gpt-4o", "gpt-4o-mini"],
            "gemini": ["gemini-2.0-flash", "gemini-2.5-pro-preview-03-25"],
            "ollama": ["qwen2.5:32b", "qwen3.5:35b", "command-r"],
        }

        if "ai_provider" not in st.session_state:
            st.session_state.ai_provider = settings.DEFAULT_PROVIDER
        if "ai_model" not in st.session_state:
            st.session_state.ai_model = PROVIDER_MODELS.get(st.session_state.ai_provider, [""])[0]

        provider = st.selectbox(
            "Provider", list(PROVIDER_MODELS.keys()),
            index=list(PROVIDER_MODELS.keys()).index(st.session_state.ai_provider)
            if st.session_state.ai_provider in PROVIDER_MODELS else 0,
            key="provider_select", label_visibility="collapsed",
        )
        if provider != st.session_state.ai_provider:
            st.session_state.ai_provider = provider
            st.session_state.ai_model = PROVIDER_MODELS[provider][0]

        model = st.selectbox(
            "Model", PROVIDER_MODELS[provider],
            index=PROVIDER_MODELS[provider].index(st.session_state.ai_model)
            if st.session_state.ai_model in PROVIDER_MODELS[provider] else 0,
            key="model_select", label_visibility="collapsed",
        )
        st.session_state.ai_model = model

        st.divider()

        # Controls
        st.markdown("##### ⚙️ Controls")

        if "planning_enabled" not in st.session_state:
            st.session_state.planning_enabled = True
        st.session_state.planning_enabled = st.toggle(
            "🗺️ ReAct Planning", value=st.session_state.planning_enabled, key="planning_toggle",
        )
        st.toggle("🔒 Command Approval", value=True, key="change_approval_toggle")

        st.divider()

        # Proxy Stats
        if "proxy_stats" in st.session_state:
            stats = st.session_state.proxy_stats
            st.markdown("##### 📊 Proxy Stats")
            c1, c2 = st.columns(2)
            c1.metric("Requests", stats["total_requests"])
            c2.metric("Errors", stats["errors"])
            if stats["last_request_time"]:
                st.caption(f"Last: {stats['last_request_time']}")
            st.divider()

        if st.button("🧹 Clear Chat", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

    return DeviceStorage.get_connections_for_selected(_auto_select_devices())


def _auto_select_devices() -> dict:
    selected = {}
    for dtype in dict(DEVICE_TYPES):
        devices = DeviceStorage.get_by_type(dtype)
        selected[dtype] = devices[0]["id"] if devices else None
    return selected
