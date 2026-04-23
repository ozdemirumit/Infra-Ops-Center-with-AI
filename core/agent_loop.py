"""
Agentic Loop module.
Manages the tool-call loop with Claude AI.

When change commands are detected, saves them to session_state;
main.py displays approval buttons in the chat.
"""

import streamlit as st
from proxy.ai_proxy import AIProxy
from tools.ssh_tool import execute_ssh_command
from tools.switch_tool import execute_web_command
from tools.deco_tool import execute_deco_api
from tools.commvault_tool import execute_commvault_api
from tools.windows_tool import execute_windows_command
from config.settings import settings


# ─── Change Command Detection ────────────────────────────────────────────────

CHANGE_PATTERNS = [
    "apt-get install", "apt install", "apt-get upgrade", "apt upgrade",
    "apt-get remove", "apt remove", "apt-get purge", "dpkg -i",
    "yum install", "yum remove", "dnf install",
    "systemctl start", "systemctl stop", "systemctl restart",
    "systemctl enable", "systemctl disable",
    "service start", "service stop", "service restart",
    "rm -rf", "rm -r", "rmdir", "mv ", "cp ",
    "chmod ", "chown ", "chgrp ",
    "iptables", "ufw ", "firewall-cmd",
    "ip addr add", "ip route add", "ifconfig",
    "useradd", "userdel", "usermod", "passwd", "groupadd",
    "reboot", "shutdown", "poweroff", "halt",
    "mount ", "umount ", "fdisk", "mkfs",
    "sed -i", "tee ", "echo >>", "echo >",
    "crontab", "visudo",
]


def _is_change_command(command: str) -> bool:
    cmd_lower = command.lower().strip()
    return any(p in cmd_lower for p in CHANGE_PATTERNS)


def _get_risk_info(command: str) -> tuple[str, str, str]:
    cmd = command.lower()
    if any(p in cmd for p in ["rm -rf", "reboot", "shutdown", "poweroff", "fdisk", "mkfs", "iptables", "passwd"]):
        return "critical", "🔴", "High-risk operation — cannot be undone!"
    elif any(p in cmd for p in ["install", "upgrade", "remove", "purge"]):
        return "high", "🟠", "Package changes may affect services"
    elif any(p in cmd for p in ["systemctl", "service"]):
        return "medium", "🟡", "Service state will change"
    else:
        return "medium", "🟡", "System configuration will change"


# ─── Target Server Resolution ────────────────────────────────────────────────

def _resolve_target_servers(servers: list, tool_input: dict) -> tuple[list, bool]:
    target_host = tool_input.get("target_host", "").strip()
    command = tool_input.get("command", tool_input.get("action", ""))

    if target_host:
        for s in servers:
            if target_host in (s.get("ip", ""), s.get("hostname", ""), s.get("name", "")):
                return [s], False
        for s in servers:
            if (target_host.lower() in s.get("ip", "").lower() or
                    target_host.lower() in s.get("hostname", "").lower() or
                    target_host.lower() in s.get("name", "").lower()):
                return [s], False

    for s in servers:
        ip, hostname, name = s.get("ip", ""), s.get("hostname", ""), s.get("name", "")
        if (ip and ip in command) or (hostname and hostname in command) or (name and name in command):
            return [s], False

    return servers, len(servers) > 1


# ─── Tool Dispatch ───────────────────────────────────────────────────────────

def _dispatch_tool(tool_name: str, tool_input: dict, connections: dict) -> str:
    t_input = tool_input.get("command", tool_input.get("action"))
    if not t_input:
        return "⚠️ Command parameter is empty."

    if tool_name == "linux_ops":
        from devices.storage import DeviceStorage
        servers = DeviceStorage.get_by_type("linux")
        if not servers:
            return "❌ No registered Linux server found."
        targets, _ = _resolve_target_servers(servers, tool_input)
        results = []
        for s in targets:
            results.append(f"=== {s['name']} ({s['ip']}) ===")
            results.append(execute_ssh_command(s["ip"], s["user"], s["password"], t_input))
        return "\n".join(results)

    elif tool_name == "esxi_ops":
        c = connections.get("esxi", {})
        return execute_ssh_command(c.get("ip", ""), c.get("user", ""), c.get("pwd", ""), t_input)
    elif tool_name == "router_ops":
        c = connections.get("router", {})
        return execute_ssh_command(c.get("ip", ""), c.get("user", ""), c.get("pwd", ""), t_input)
    elif tool_name == "switch_ops":
        c = connections.get("switch", {})
        return execute_web_command(c.get("ip", ""), c.get("user", ""), c.get("pwd", ""), t_input)
    elif tool_name == "deco_ops":
        c = connections.get("deco", {})
        return execute_deco_api(c.get("ip", ""), c.get("user", ""), c.get("pwd", ""), t_input)
    elif tool_name == "commvault_ops":
        c = connections.get("commvault", {})
        return execute_commvault_api(c.get("ip", ""), c.get("user", ""), c.get("pwd", ""), t_input)
    elif tool_name == "windows_ops":
        from devices.storage import DeviceStorage
        servers = DeviceStorage.get_by_type("windows")
        if not servers:
            return "❌ No registered Windows server found."
        targets, _ = _resolve_target_servers(servers, tool_input)
        results = []
        for s in targets:
            results.append(f"=== {s['name']} ({s['ip']}) ===")
            results.append(execute_windows_command(s["ip"], s["user"], s["password"], t_input))
        return "\n".join(results)
    else:
        from tools.registry import dispatch_custom_tool
        return dispatch_custom_tool(tool_name, tool_input, connections)


TOOL_ICONS = {
    "linux_ops": "🐧", "esxi_ops": "☁️", "router_ops": "🌐",
    "switch_ops": "🔌", "deco_ops": "📶", "commvault_ops": "💾",
    "windows_ops": "🪟",
}


def _save_session_memory(session: dict):
    try:
        import logging
        logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").setLevel(logging.ERROR)
        from core.session_learner import extract_and_save
        extract_and_save(session)
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════════════
# MAIN AGENTIC LOOP
# ═════════════════════════════════════════════════════════════════════════════

def run_agent_loop(prompt: str, connections: dict, session_id: str = None):
    """
    Agentic loop. When a change command is detected, saves it to session_state
    and the loop ends normally. main.py displays the approval buttons.
    """
    with st.chat_message("user"):
        st.markdown(prompt)

    turn_messages = [{"role": "user", "content": prompt}]
    proxy = AIProxy()

    # RAG
    rag_context = ""
    if settings.RAG_ENABLED:
        try:
            from core.rag_engine import get_rag_engine
            rag_context = get_rag_engine().get_context_for_prompt(prompt)
        except Exception:
            pass

    session_memory_context = ""
    if session_id and settings.RAG_ENABLED:
        try:
            from core.session_learner import get_context_for_session
            session_memory_context = get_context_for_session(prompt, top_k=3)
        except Exception:
            pass

    # System prompt
    device_lines = []
    type_labels = {
        "linux": "Linux Server", "esxi": "VMware ESXi", "router": "Router",
        "switch": "Switch", "deco": "Deco Mesh", "commvault": "Commvault",
        "windows": "Windows Server",
    }
    for dtype, conn in connections.items():
        if conn.get("ip"):
            name = conn.get("name") or conn.get("hostname") or conn["ip"]
            hostname = conn.get("hostname", "")
            role = conn.get("role", "")
            label = type_labels.get(dtype, dtype)
            line = f"- {name} ({label}, IP: {conn['ip']}"
            if hostname and hostname != name:
                line += f", hostname: {hostname}"
            if role:
                line += f", role: {role}"
            line += ")"
            device_lines.append(line)

    system_prompt = settings.SYSTEM_PROMPT
    if device_lines:
        system_prompt += "\n\nActive connected devices:\n" + "\n".join(device_lines)
    if rag_context:
        system_prompt += rag_context
    if session_memory_context:
        system_prompt += session_memory_context

    with st.chat_message("assistant"):
        try:
            step = 0
            while step < settings.MAX_AGENT_STEPS:
                step += 1

                current_messages = st.session_state.messages + turn_messages

                from tools.registry import get_active_tools
                _spinner_msg = f"💭 Thinking (step {step}/{settings.MAX_AGENT_STEPS})…"
                with st.spinner(_spinner_msg):
                    response = proxy.chat(
                        messages=current_messages,
                        tools=get_active_tools(),
                        system=system_prompt,
                    )

                assistant_message = {"role": "assistant", "content": []}
                tool_uses_this_turn = []

                for block in response.content:
                    if block.type == "text":
                        st.markdown(block.text)
                        assistant_message["content"].append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        assistant_message["content"].append({
                            "type": "tool_use", "id": block.id,
                            "name": block.name, "input": block.input,
                        })
                        tool_uses_this_turn.append(block)

                turn_messages.append(assistant_message)

                if not tool_uses_this_turn:
                    break

                # ── Execute tools ──
                tool_results = []
                needs_approval = False

                for tu in tool_uses_this_turn:
                    command_text = tu.input.get("command", tu.input.get("action", ""))
                    icon = TOOL_ICONS.get(tu.name, "🛠️")

                    # Multi-server warning
                    if tu.name in ("linux_ops", "windows_ops"):
                        from devices.storage import DeviceStorage
                        srv_type = "linux" if tu.name == "linux_ops" else "windows"
                        all_servers = DeviceStorage.get_by_type(srv_type)
                        _, is_multi = _resolve_target_servers(all_servers, tu.input)
                        if is_multi:
                            names = ", ".join(f"{s['name']} ({s['ip']})" for s in all_servers)
                            st.warning(f"⚠️ Will run on ALL {srv_type.upper()} servers: `{names}`")

                    # ── Change command → send for approval ──
                    if _is_change_command(command_text):
                        risk, risk_icon, impact = _get_risk_info(command_text)

                        # Save to session state — main.py will display the buttons
                        st.session_state["pending_command"] = {
                            "tool_use_id": tu.id,
                            "tool_name": tu.name,
                            "tool_input": tu.input,
                            "command_text": command_text,
                            "risk": risk,
                            "risk_icon": risk_icon,
                            "impact": impact,
                            "connections": connections,
                            "session_id": session_id,
                        }

                        # Save messages (so they persist after rerun)
                        st.session_state.messages.extend(turn_messages)
                        if session_id:
                            from sessions.storage import save_session, get_session
                            s = get_session(session_id)
                            if s:
                                s["messages"] = st.session_state.messages
                                s["status"] = "active"
                                save_session(s)

                        st.info(
                            f"{risk_icon} **Change command detected:** `{command_text}`\n\n"
                            f"**Risk:** {risk.upper()} · {impact}\n\n"
                            "Use the buttons below to approve or reject."
                        )
                        needs_approval = True
                        break  # for loop — wait for approval

                    else:
                        # Normal command — show expanding status with progress
                        status_label = f"{icon} {tu.name} → `{command_text[:80]}`"
                        with st.status(status_label, expanded=True, state="running") as _status:
                            st.caption("⏳ Executing…")
                            raw_result = _dispatch_tool(tu.name, tu.input, connections)
                            result = proxy.filter_ssh_output(raw_result)
                            st.code(result, language="bash")
                            # Final state
                            success = not (result.startswith("❌") or "error" in result.lower()[:50])
                            _status.update(
                                label=f"{icon} {tu.name} {'✓' if success else '✗'} `{command_text[:60]}`",
                                state="complete" if success else "error",
                                expanded=success is False,
                            )

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": result,
                        })

                if needs_approval:
                    return  # Exit loop — main.py will display the buttons

                # Send tool results back to AI
                turn_messages.append({"role": "user", "content": tool_results})

            # ── Loop finished → save ──
            st.session_state.messages.extend(turn_messages)

            if session_id:
                try:
                    from sessions.storage import set_session_completed, get_session
                    set_session_completed(session_id, st.session_state.messages)
                    import threading
                    sess = get_session(session_id)
                    if sess:
                        threading.Thread(target=_save_session_memory, args=(sess,), daemon=True).start()
                except Exception:
                    pass

            if settings.RAG_ENABLED:
                try:
                    from core.runbook_saver import extract_runbook, save_runbook_to_rag
                    runbook = extract_runbook(prompt, turn_messages)
                    if runbook:
                        save_runbook_to_rag(runbook, prompt)
                except Exception:
                    pass

        except Exception as e:
            st.error(f"❌ System/API Error: {str(e)}")
            if session_id:
                try:
                    from sessions.storage import set_session_failed
                    set_session_failed(session_id, st.session_state.messages + turn_messages, str(e))
                except Exception:
                    pass
