"""
⚙️ AI Proxy & Provider Settings
Configure LLM Sentinel proxy connection, Ollama endpoint, and direct API keys.
All values are persisted to .env (and applied to the current session).
"""

import re
import os
from pathlib import Path

import httpx
import streamlit as st

from auth.authenticator import check_auth, is_admin
from ui.sidebar import render_sidebar
from config.settings import settings

st.set_page_config(page_title="Proxy Settings", page_icon="⚙️", layout="wide")

# Inject CSS
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


# ──────────────────────────────────────────────────────────────
# .env helpers
# ──────────────────────────────────────────────────────────────

def _read_env() -> str:
    return ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""


def _save_env_keys(updates: dict[str, str]) -> None:
    """Update keys in .env, preserving all other lines. Atomic write."""
    from logging_config.atomic_io import _get_lock
    import tempfile

    text = _read_env()
    lines = text.splitlines(keepends=True)
    replaced = set()

    new_lines = []
    for line in lines:
        matched = False
        for key, val in updates.items():
            if re.match(rf"^\s*{re.escape(key)}\s*=", line):
                new_lines.append(f"{key}={val}\n")
                replaced.add(key)
                matched = True
                break
        if not matched:
            new_lines.append(line)

    for key, val in updates.items():
        if key not in replaced:
            new_lines.append(f"{key}={val}\n")

    # Atomic write
    lock = _get_lock(str(ENV_PATH))
    with lock:
        fd, tmp_path = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=ENV_PATH.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        os.replace(tmp_path, ENV_PATH)

    # Apply to current process
    for k, v in updates.items():
        os.environ[k] = v


# ──────────────────────────────────────────────────────────────
# Page
# ──────────────────────────────────────────────────────────────

st.title("⚙️ AI Proxy & Provider Settings")
st.markdown("Configure how the AI agent connects to language models.")

tab_proxy, tab_direct, tab_ollama, tab_status = st.tabs(
    ["🛡️ LLM Sentinel Proxy", "🔌 Direct API Keys", "🏠 On-Prem Ollama", "📊 Status"]
)

# ══════════════════════════════════════════════════════════════
# TAB 1: PROXY
# ══════════════════════════════════════════════════════════════

with tab_proxy:
    st.markdown("##### Enterprise proxy with rate limiting, content filtering, and centralized key management.")

    proxy_enabled = st.toggle(
        "Enable LLM Sentinel Proxy",
        value=settings.PROXY_ENABLED,
        key="cfg_proxy_enabled",
        help="When enabled, all AI calls (except Ollama) route through the proxy.",
    )

    c1, c2, c3 = st.columns([2, 1, 3])
    with c1:
        pm_host = st.text_input("Proxy Host", value=settings.PROXY_HOST, key="cfg_proxy_host")
    with c2:
        pm_port = st.number_input(
            "Port", value=settings.PROXY_PORT,
            min_value=1, max_value=65535, step=1, key="cfg_proxy_port",
        )
    with c3:
        pm_api_key = st.text_input(
            "Bearer Token (client)", value=settings.PROXY_API_KEY,
            type="password", key="cfg_proxy_key",
            placeholder="sk-proxy-...",
            help="Used for chat completions, model list, etc.",
        )

    # Admin token (optional) — needed to see custom provider endpoints
    pm_admin_key = st.text_input(
        "Admin API Key (optional)",
        value=settings.PROXY_ADMIN_KEY,
        type="password", key="cfg_proxy_admin_key",
        placeholder="sk-proxy-admin-... (only required to see custom providers)",
        help=(
            "LLM Sentinel's /v1/providers and /v1/aliases endpoints require admin role. "
            "Provide an admin token here to enable discovery of custom providers like 'Heimdal'. "
            "If left blank, only standard cloud providers will appear in the model dropdown."
        ),
    )

    col_save, col_test, _ = st.columns([1, 1, 2])
    with col_save:
        if st.button("💾 Save", use_container_width=True, type="primary"):
            try:
                _save_env_keys({
                    "PROXY_ENABLED": "true" if proxy_enabled else "false",
                    "PROXY_HOST": pm_host,
                    "PROXY_PORT": str(int(pm_port)),
                    "PROXY_API_KEY": pm_api_key,
                    "PROXY_ADMIN_KEY": pm_admin_key,
                })
                from proxy.model_discovery import invalidate_cache
                invalidate_cache()
                st.success("✅ Saved to .env and applied.")
                st.rerun()
            except Exception as e:
                st.error(f"Failed to save: {e}")

    with col_test:
        if st.button("🔌 Test Connection", use_container_width=True):
            with st.spinner("Connecting…"):
                try:
                    base = f"http://{pm_host}:{int(pm_port)}"
                    headers = {"Authorization": f"Bearer {pm_api_key}"} if pm_api_key else {}
                    r = httpx.get(f"{base}/health", headers=headers, timeout=5.0)
                    if r.status_code == 200:
                        st.success(f"✅ Proxy reachable: {r.json()}")
                    else:
                        st.error(f"❌ HTTP {r.status_code}: {r.text[:200]}")
                except Exception as e:
                    st.error(f"❌ Connection failed: {type(e).__name__}: {e}")

    if proxy_enabled:
        st.divider()
        st.markdown("##### 📋 Models discovered through this proxy")

        # Try to fetch live model list
        with st.spinner("Discovering models from proxy…"):
            try:
                base = f"http://{pm_host}:{int(pm_port)}"
                headers = {"Authorization": f"Bearer {pm_api_key}"} if pm_api_key else {}
                admin_headers = (
                    {"Authorization": f"Bearer {pm_admin_key}"}
                    if pm_admin_key else headers
                )

                # Try /v1/models
                models_data = None
                try:
                    r = httpx.get(f"{base}/v1/models", headers=headers, timeout=5.0)
                    if r.status_code == 200:
                        models_data = r.json()
                except Exception:
                    pass

                if models_data:
                    # Group by provider
                    grouped = {}
                    if isinstance(models_data, dict) and "data" in models_data:
                        for m in models_data["data"]:
                            p = m.get("owned_by", "unknown")
                            grouped.setdefault(p, []).append(m.get("id", "?"))
                    elif isinstance(models_data, dict):
                        grouped = models_data

                    if grouped:
                        for provider, models in grouped.items():
                            if not models:
                                continue
                            with st.expander(f"📦 {provider} ({len(models)} models)", expanded=False):
                                for m in models:
                                    st.text(f"• {m if isinstance(m, str) else m.get('id', m.get('name', '?'))}")
                    else:
                        st.info("Proxy reachable but no models discovered.")
                else:
                    st.info("Could not query /v1/models endpoint.")

                # Show aliases
                try:
                    r = httpx.get(f"{base}/v1/aliases", headers=admin_headers, timeout=5.0)
                    if r.status_code == 200:
                        aliases = r.json()
                        if isinstance(aliases, list) and aliases:
                            with st.expander(f"🏷️ Aliases ({len(aliases)})"):
                                for a in aliases:
                                    st.text(f"• {a.get('alias', '?')} → {a.get('provider', '?')}/{a.get('model', '?')}")
                except Exception:
                    pass

                # Show providers (admin endpoint)
                try:
                    r = httpx.get(f"{base}/v1/providers", headers=admin_headers, timeout=5.0)
                    if r.status_code == 403:
                        st.warning(
                            "🔒 `/v1/providers` requires admin role. "
                            "Set the **Admin API Key** above to see custom providers like Heimdal."
                        )
                    if r.status_code == 200:
                        provs = r.json()
                        provs_list = provs if isinstance(provs, list) else provs.get("providers", [])
                        if provs_list:
                            with st.expander(f"🌐 Custom Provider Endpoints ({len(provs_list)})"):
                                for p in provs_list:
                                    if isinstance(p, dict):
                                        name = p.get("name") or p.get("id") or "?"
                                        ptype = p.get("type") or p.get("provider") or "—"
                                        url = p.get("base_url") or p.get("endpoint") or p.get("url") or "—"
                                        default_model = p.get("default_model") or p.get("model") or "—"
                                        st.text(f"• {name}  [{ptype}]  → {url}  (default: {default_model})")
                                    else:
                                        st.text(f"• {p}")
                except Exception:
                    pass

            except Exception as e:
                st.warning(f"⚠️ Cannot reach proxy: {e}")

        # ─── Debug: Raw API responses ───
        st.divider()
        with st.expander("🔧 Debug — Raw API Responses (paste output here if a provider is missing)", expanded=False):
            st.caption(
                "If a provider configured in your proxy isn't showing up in the sidebar dropdown, "
                "inspect the raw responses below and share them so the parser can be updated."
            )

            # Mapping: which endpoints need admin token
            admin_endpoints = {"/v1/providers", "/v1/aliases"}
            for endpoint in ["/v1/models", "/v1/providers", "/v1/aliases", "/health"]:
                use_admin = endpoint in admin_endpoints
                h = admin_headers if use_admin else headers
                label_suffix = " *(admin)*" if use_admin and admin_headers != headers else ""
                try:
                    r = httpx.get(f"{base}{endpoint}", headers=h, timeout=5.0)
                    st.markdown(f"**GET {endpoint}**{label_suffix} → HTTP {r.status_code}")
                    try:
                        data = r.json()
                        st.json(data)
                    except Exception:
                        st.code(r.text[:1000], language="text")
                except Exception as e:
                    st.markdown(f"**GET {endpoint}** → ❌ {type(e).__name__}: {e}")

            # Try to discover per-provider model endpoints (also admin)
            try:
                r = httpx.get(f"{base}/v1/providers", headers=admin_headers, timeout=5.0)
                if r.status_code == 200:
                    data = r.json()
                    plist = data if isinstance(data, list) else data.get("providers", data.get("data", []))
                    if isinstance(plist, list):
                        for p in plist[:5]:  # max 5 to avoid spamming
                            if isinstance(p, dict):
                                pname = p.get("name") or p.get("id")
                                if pname:
                                    try:
                                        r2 = httpx.get(f"{base}/v1/providers/{pname}/models",
                                                       headers=admin_headers, timeout=5.0)
                                        st.markdown(f"**GET /v1/providers/{pname}/models** → HTTP {r2.status_code}")
                                        try:
                                            st.json(r2.json())
                                        except Exception:
                                            st.code(r2.text[:500])
                                    except Exception:
                                        pass
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════
# TAB 2: DIRECT API
# ══════════════════════════════════════════════════════════════

with tab_direct:
    st.markdown("##### Connect directly to provider APIs (used when proxy is disabled or for the selected provider).")

    c1, c2 = st.columns(2)
    with c1:
        ant_key = st.text_input(
            "Anthropic API Key",
            value=settings.ANTHROPIC_API_KEY,
            type="password",
            key="cfg_ant_key",
            placeholder="sk-ant-api03-...",
        )
        openai_key = st.text_input(
            "OpenAI API Key",
            value=settings.OPENAI_API_KEY,
            type="password",
            key="cfg_openai_key",
            placeholder="sk-proj-...",
        )
    with c2:
        gemini_key = st.text_input(
            "Google Gemini API Key",
            value=settings.GEMINI_API_KEY,
            type="password",
            key="cfg_gemini_key",
            placeholder="AIza...",
        )
        default_provider = st.selectbox(
            "Default Provider",
            ["anthropic", "openai", "gemini", "ollama"],
            index=["anthropic", "openai", "gemini", "ollama"].index(settings.DEFAULT_PROVIDER)
            if settings.DEFAULT_PROVIDER in ["anthropic", "openai", "gemini", "ollama"] else 0,
            key="cfg_default_provider",
        )

    if st.button("💾 Save Direct API Settings", type="primary", use_container_width=True):
        try:
            _save_env_keys({
                "ANTHROPIC_API_KEY": ant_key,
                "OPENAI_API_KEY": openai_key,
                "GEMINI_API_KEY": gemini_key,
                "DEFAULT_PROVIDER": default_provider,
            })
            from proxy.model_discovery import invalidate_cache
            invalidate_cache()
            st.success("✅ Saved.")
            st.rerun()
        except Exception as e:
            st.error(f"Failed: {e}")


# ══════════════════════════════════════════════════════════════
# TAB 3: OLLAMA
# ══════════════════════════════════════════════════════════════

with tab_ollama:
    st.markdown("##### On-premise Ollama instance — runs LLMs locally on your own hardware.")

    ollama_url = st.text_input(
        "Ollama URL", value=settings.OLLAMA_URL, key="cfg_ollama_url",
        placeholder="http://localhost:11434",
        help="URL of the Ollama HTTP API",
    )
    ollama_model = st.text_input(
        "Default Model", value=settings.OLLAMA_MODEL, key="cfg_ollama_model",
        placeholder="llama3.1",
    )

    col_save, col_test, _ = st.columns([1, 1, 2])
    with col_save:
        if st.button("💾 Save Ollama Settings", use_container_width=True, type="primary"):
            try:
                _save_env_keys({
                    "OLLAMA_URL": ollama_url,
                    "OLLAMA_MODEL": ollama_model,
                })
                from proxy.model_discovery import invalidate_cache
                invalidate_cache()
                st.success("✅ Saved.")
                st.rerun()
            except Exception as e:
                st.error(f"Failed: {e}")

    with col_test:
        if st.button("🔌 Test & List Models", use_container_width=True):
            with st.spinner("Connecting to Ollama…"):
                try:
                    r = httpx.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=5.0)
                    if r.status_code == 200:
                        models = r.json().get("models", [])
                        if models:
                            st.success(f"✅ Connected! Found {len(models)} model(s):")
                            for m in models:
                                size_gb = m.get("size", 0) / (1024**3)
                                st.text(f"• {m.get('name', '?')} ({size_gb:.1f} GB)")
                        else:
                            st.warning("Connected but no models installed. Run: `ollama pull llama3.1`")
                    else:
                        st.error(f"HTTP {r.status_code}: {r.text[:200]}")
                except Exception as e:
                    st.error(f"❌ Cannot reach Ollama: {type(e).__name__}: {e}")


# ══════════════════════════════════════════════════════════════
# TAB 4: STATUS
# ══════════════════════════════════════════════════════════════

with tab_status:
    st.markdown("##### Current configuration status")

    from proxy.model_discovery import get_provider_status, get_available_models

    status = get_provider_status()
    all_models = get_available_models()

    # Provider status grid
    cols = st.columns(4)
    for i, (provider, info) in enumerate(status.items()):
        with cols[i]:
            available = info.get("available", False)
            source = info.get("source", "fallback")
            source_icons = {
                "proxy": "🛡️ Proxy",
                "local": "🏠 Local",
                "direct": "🔌 Direct",
                "fallback": "📋 Fallback",
            }

            if available:
                st.success(f"**{provider}**\n\n{source_icons.get(source, source)}")
            else:
                st.error(f"**{provider}**\n\nNot configured")

            count = len(all_models.get(provider, []))
            st.caption(f"{count} model(s) available")

    st.divider()

    # Total model count
    total_models = sum(len(v) for v in all_models.values())
    st.markdown(f"##### 📦 Total: {total_models} models across {len(all_models)} providers")

    # Refresh button
    if st.button("↻ Refresh All Discovery"):
        from proxy.model_discovery import invalidate_cache
        invalidate_cache()
        st.rerun()

    # Full breakdown
    with st.expander("📋 Detailed Model List", expanded=False):
        for provider, models in all_models.items():
            st.markdown(f"**{provider}** ({len(models)})")
            for m in models:
                st.text(f"  • {m}")
