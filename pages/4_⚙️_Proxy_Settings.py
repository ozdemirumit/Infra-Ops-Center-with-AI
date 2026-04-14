"""
Proxy Management Page -- manage ai-proxy connection from the Streamlit UI.
Supports switching between External Proxy and Local (direct) mode,
and editing / saving connection settings to .env.
"""

import re
from pathlib import Path

import httpx
import streamlit as st

from config.settings import settings

st.set_page_config(page_title="Proxy Management", page_icon="⚙️", layout="wide")

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


# ── .env helpers ─────────────────────────────────────────────────────────────

def _read_env() -> str:
    return ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""


def _save_env_keys(updates: dict[str, str]) -> None:
    """Update specific keys in .env, preserving all other lines."""
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

    # Append any keys that weren't found in the file
    for key, val in updates.items():
        if key not in replaced:
            new_lines.append(f"{key}={val}\n")

    ENV_PATH.write_text("".join(new_lines), encoding="utf-8")


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _headers() -> dict:
    import os
    api_key = st.session_state.get("pm_api_key") or os.getenv("PROXY_API_KEY", settings.PROXY_API_KEY)
    h = {"Content-Type": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def _base() -> str:
    host = st.session_state.get("pm_host", settings.PROXY_HOST)
    port = st.session_state.get("pm_port", settings.PROXY_PORT)
    return f"http://{host}:{port}"


def _get(path: str, timeout: float = 4.0) -> dict:
    try:
        r = httpx.get(f"{_base()}{path}", headers=_headers(), timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def _post(path: str, payload: dict = None, timeout: float = 8.0) -> dict:
    try:
        r = httpx.post(f"{_base()}{path}", json=payload or {}, headers=_headers(), timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}


# ── Page ─────────────────────────────────────────────────────────────────────

st.title("⚙️ AI Proxy Management")

# ── Connection Settings ───────────────────────────────────────────────────────

with st.expander("🔌 Connection Settings", expanded=True):
    use_external = True  # Only enterprise proxy mode is supported

    if use_external:
        c1, c2, c3 = st.columns([2, 1, 3])
        with c1:
            pm_host = st.text_input("Proxy Host", value=settings.PROXY_HOST, key="pm_host")
        with c2:
            pm_port = st.number_input("Port", value=settings.PROXY_PORT, min_value=1, max_value=65535, step=1, key="pm_port")
        with c3:
            pm_api_key = st.text_input(
                "API Key (Bearer Token)",
                value=settings.PROXY_API_KEY,
                type="password",
                key="pm_api_key",
                placeholder="Leave blank to use .env value",
            )

    col_save, col_test = st.columns([2, 5])
    with col_save:
        if st.button("💾 Save to .env", use_container_width=True):
            updates = {
                "PROXY_ENABLED": "true" if use_external else "false",
            }
            if use_external:
                updates["PROXY_HOST"] = pm_host
                updates["PROXY_PORT"] = str(int(pm_port))
                updates["PROXY_API_KEY"] = pm_api_key
            try:
                _save_env_keys(updates)
                # Also update current process environment so changes are live immediately
                import os
                for k, v in updates.items():
                    os.environ[k] = v
                st.success("✅ Saved to .env and applied to current session.")
            except Exception as e:
                st.error(f"Failed to write .env: {e}")


st.divider()

# ── Status (only shown in External mode) ─────────────────────────────────────

if True:
    st.caption(f"Proxy URL: `{_base()}`")
    health = _get("/health")
    is_online = isinstance(health, dict) and health.get("status") == "ok"

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if is_online:
            st.success("🟢 **Online**")
        else:
            st.error("🔴 **Offline**")
    if is_online:
        with col2:
            st.metric("Provider", health.get("provider", "-").upper())
        with col3:
            st.metric("Model", health.get("model", "-"))
        with col4:
            uptime = health.get("uptime_seconds", 0)
            hrs, rem = divmod(int(uptime), 3600)
            mins, secs = divmod(rem, 60)
            st.metric("Uptime", f"{hrs:02d}:{mins:02d}:{secs:02d}")

    st.divider()

    # ── Statistics ───────────────────────────────────────────────────────────
    st.subheader("📊 Statistics")

    if is_online:
        stats = _get("/v1/stats") or {}
        if "error" not in stats:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total Requests", stats.get("total_requests", 0))
            c2.metric("Input Tokens",   f"{stats.get('total_input_tokens', 0):,}")
            c3.metric("Output Tokens",  f"{stats.get('total_output_tokens', 0):,}")
            c4.metric("Errors",         stats.get("errors", 0))
            if stats.get("last_request_time"):
                st.caption(f"Last request: {stats['last_request_time']}")
        else:
            st.warning(f"Could not fetch statistics: {stats['error']}")
    else:
        st.warning("Proxy is offline — statistics unavailable.")

    st.divider()

    # ── Provider / Model configuration ───────────────────────────────────────
    st.subheader("🔧 Provider / Model")

    if is_online:
        aliases_data = _get("/v1/aliases") or {}
        if "error" not in aliases_data:
            aliases = aliases_data if isinstance(aliases_data, list) else []
            st.caption(f"Defined model aliases: **{len(aliases)}**")
            if aliases:
                st.dataframe(
                    [{"alias": a.get("alias"), "provider": a.get("provider"), "model": a.get("model")} for a in aliases],
                    use_container_width=True,
                )
        else:
            st.warning(f"Could not fetch alias information: {aliases_data['error']}")
        st.caption("Provider and model selection is managed from the enterprise proxy admin panel.")
    else:
        st.warning("Proxy offline — configuration information unavailable.")

    st.divider()

    # ── Quick Health Ping ─────────────────────────────────────────────────────
    st.subheader("🩺 Quick Health Check")
    if st.button("🔁 Ping Proxy"):
        st.json(_get("/health"))

    st.divider()
    st.caption("For provider/model selection, API key management, and usage statistics, use the Admin UI at: "
               f"`http://{settings.PROXY_HOST}:{settings.PROXY_PORT}`.")
