"""
Chat component — Renders message history with tool calls and results.
"""

import streamlit as st

_TOOL_ICONS = {
    "linux_ops": "🐧", "esxi_ops": "☁️", "router_ops": "🌐",
    "switch_ops": "🔌", "deco_ops": "📶", "commvault_ops": "💾",
    "windows_ops": "🪟",
}


def render_chat_history():
    """Render chat message history with tool calls and outputs."""
    for msg in st.session_state.get("messages", []):
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "user":
            if isinstance(content, str):
                with st.chat_message("user"):
                    st.markdown(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        result = block.get("content", "")
                        if result and not result.startswith("⏳"):
                            with st.chat_message("assistant"):
                                st.markdown("**📋 Command Output:**")
                                st.code(result, language="bash")

        elif role == "assistant":
            blocks = content if isinstance(content, list) else []
            if not blocks:
                continue
            with st.chat_message("assistant"):
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        st.markdown(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        name = block.get("name", "")
                        inp = block.get("input", {})
                        cmd = inp.get("command", inp.get("action", "—"))
                        icon = _TOOL_ICONS.get(name, "🛠️")
                        st.markdown(f"**{icon} {name}** → `{cmd}`")
