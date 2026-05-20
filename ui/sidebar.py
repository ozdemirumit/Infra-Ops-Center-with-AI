"""
Sidebar — Modern enterprise control panel.
"""

import streamlit as st
from auth.authenticator import logout, get_current_user, is_admin
from devices.storage import DeviceStorage, DEVICE_TYPES
from config.settings import settings


def render_sidebar() -> dict:
    with st.sidebar:
        # Brand header
        st.markdown(
            """
            <div style='padding: 0.25rem 0 0.75rem 0.25rem;
                        display: flex; align-items: center; gap: 8px;
                        border-bottom: 1px solid rgba(255,255,255,0.05);
                        margin-bottom: 0.75rem;'>
                <span style='font-size: 1.25rem;'>🛡️</span>
                <span style='font-weight: 700; font-size: 0.95rem;
                             color: #f1f5f9; letter-spacing: -0.01em;'>
                    Infra Ops Center
                </span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # User info
        user = get_current_user()
        role_icon = "⚡" if is_admin() else "👁️"
        role_text = "Admin" if is_admin() else "Viewer"

        st.markdown(
            f"""
            <div style='padding: 0.4rem 0.6rem; background: rgba(255,255,255,0.03);
                        border: 1px solid rgba(255,255,255,0.06); border-radius: 8px;
                        margin-bottom: 0.5rem; font-size: 0.8rem;'>
                <div style='color: #a8b3c5;'>{role_icon} <strong>{user}</strong></div>
                <div style='color: #6b7690; font-size: 0.7rem; margin-top: 2px;'>{role_text}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if st.button("🚪 Sign Out", use_container_width=True, type="secondary"):
            logout()

        st.divider()

        # AI Model — discovered dynamically (proxy + Ollama + fallback)
        col_label, col_refresh = st.columns([4, 1])
        with col_label:
            st.markdown("##### AI Model")
        with col_refresh:
            if st.button("↻", key="refresh_models", help="Refresh model list"):
                from proxy.model_discovery import invalidate_cache
                invalidate_cache()
                st.rerun()

        # Discover available models (cached for 5 min)
        try:
            from proxy.model_discovery import get_available_models, get_provider_status
            PROVIDER_MODELS = get_available_models()
            provider_status = get_provider_status()
        except Exception as e:
            from proxy.model_discovery import FALLBACK_MODELS
            PROVIDER_MODELS = FALLBACK_MODELS
            provider_status = {}

        if not PROVIDER_MODELS:
            from proxy.model_discovery import FALLBACK_MODELS
            PROVIDER_MODELS = FALLBACK_MODELS

        # Show provider source badge (proxy / local / direct / fallback)
        if provider_status:
            badges = []
            for p, s in provider_status.items():
                if s["available"]:
                    src = s["source"]
                    icon = {"proxy": "🛡️", "local": "🏠", "direct": "🔌", "fallback": "📋"}.get(src, "•")
                    badges.append(f"{icon}`{p}`")
            if badges:
                st.caption(" ".join(badges))

        provider_list = list(PROVIDER_MODELS.keys())

        if "ai_provider" not in st.session_state:
            st.session_state.ai_provider = (
                settings.DEFAULT_PROVIDER
                if settings.DEFAULT_PROVIDER in PROVIDER_MODELS
                else provider_list[0]
            )
        if "ai_model" not in st.session_state:
            models_for_provider = PROVIDER_MODELS.get(st.session_state.ai_provider, [""])
            st.session_state.ai_model = models_for_provider[0] if models_for_provider else ""

        provider = st.selectbox(
            "Provider", provider_list,
            index=provider_list.index(st.session_state.ai_provider)
            if st.session_state.ai_provider in provider_list else 0,
            key="provider_select", label_visibility="collapsed",
        )
        if provider != st.session_state.ai_provider:
            st.session_state.ai_provider = provider
            new_models = PROVIDER_MODELS.get(provider, [])
            st.session_state.ai_model = new_models[0] if new_models else ""

        available_models = PROVIDER_MODELS.get(provider, [])
        if available_models:
            model = st.selectbox(
                "Model", available_models,
                index=available_models.index(st.session_state.ai_model)
                if st.session_state.ai_model in available_models else 0,
                key="model_select", label_visibility="collapsed",
            )
            st.session_state.ai_model = model
        else:
            st.warning(f"No models available for {provider}")

        st.divider()

        # Controls
        st.markdown("##### Controls")

        if "planning_enabled" not in st.session_state:
            st.session_state.planning_enabled = True
        st.session_state.planning_enabled = st.toggle(
            "🗺️  ReAct Planning", value=st.session_state.planning_enabled, key="planning_toggle",
        )
        st.toggle("🔒  Command Approval", value=True, key="change_approval_toggle")

        st.divider()

        # Proxy Stats
        if "proxy_stats" in st.session_state:
            stats = st.session_state.proxy_stats
            st.markdown("##### Stats")
            c1, c2 = st.columns(2)
            c1.metric("Requests", stats["total_requests"])
            c2.metric("Errors", stats["errors"])
            if stats["last_request_time"]:
                st.caption(f"Last: {stats['last_request_time']}")
            st.divider()

        if st.button("🧹  Clear Chat", use_container_width=True, type="secondary"):
            st.session_state.messages = []
            st.rerun()

    return DeviceStorage.get_connections_for_selected(_auto_select_devices())


def _auto_select_devices() -> dict:
    selected = {}
    for dtype in dict(DEVICE_TYPES):
        devices = DeviceStorage.get_by_type(dtype)
        selected[dtype] = devices[0]["id"] if devices else None
    return selected
