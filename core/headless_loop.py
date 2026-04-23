"""
Headless (UI-less) Agent Loop.

Called by APScheduler / incident_manager.
Has no st.* dependency — writes to log files.
Updates session messages and calls set_session_completed when done.
"""

import logging
from typing import Optional

from config.settings import settings
from logging_config.logger import get_logger

logger = get_logger("headless_loop")


def _save_headless_memory(session: dict):
    """Saves the completed headless session to RAG session memory."""
    try:
        from core.session_learner import extract_and_save
        extract_and_save(session)
    except Exception as e:
        logger.warning(f"[headless] Failed to save session memory: {e}")


def run_headless_loop(prompt: str, connections: dict, session_id: str):

    """
    Runs the agent loop without Streamlit.

    Args:
        prompt:      Task text created by user/system
        connections: Device connections
        session_id:  Target session ID
    """
    logger.info(f"[headless] Starting: {session_id} | {prompt[:80]}")

    from proxy.ai_proxy import AIProxy
    from core.agent_loop import _dispatch_tool, _resolve_target_servers, TOOL_ICONS
    from core.agent_loop import _is_change_command as is_change_command
    from sessions.storage import (
        get_session, save_session, update_session_messages,
        set_session_completed, set_session_failed,
        STATUS_ACTIVE
    )
    from tools.registry import get_active_tools

    session = get_session(session_id)
    if not session:
        logger.error(f"[headless] Session not found: {session_id}")
        return

    proxy = AIProxy()

    # System prompt
    system_prompt = settings.SYSTEM_PROMPT
    type_labels = {
        "linux": "Linux Server", "esxi": "VMware ESXi", "router": "Router",
        "switch": "Switch", "deco": "Deco Mesh", "commvault": "Commvault",
        "windows": "Windows Server",
    }
    device_lines = []
    for dtype, conn in connections.items():
        if conn and conn.get("ip"):
            name = conn.get("name") or conn.get("hostname") or conn["ip"]
            label = type_labels.get(dtype, dtype)
            device_lines.append(f"- {name} ({label}, IP: {conn['ip']})")
    if device_lines:
        system_prompt += "\n\nActive connected devices:\n" + "\n".join(device_lines)

    # RAG context
    if settings.RAG_ENABLED:
        try:
            from core.rag_engine import get_rag_engine
            rag_context = get_rag_engine().get_context_for_prompt(prompt)
            if rag_context:
                system_prompt += rag_context
        except Exception:
            pass

    # Past session memory context
    if settings.RAG_ENABLED:
        try:
            from core.session_learner import get_context_for_session
            mem_ctx = get_context_for_session(prompt, top_k=3)
            if mem_ctx:
                system_prompt += mem_ctx
        except Exception:
            pass

    turn_messages = [{"role": "user", "content": prompt}]
    all_messages = session.get("messages", [])

    try:
        step = 0
        while step < settings.MAX_AGENT_STEPS:
            step += 1

            current_messages = all_messages + turn_messages
            response = proxy.chat(
                messages=current_messages,
                tools=get_active_tools(),
                system=system_prompt
            )

            assistant_message = {"role": "assistant", "content": []}
            tool_uses_this_turn = []

            for block in response.content:
                if block.type == "text":
                    logger.info(f"[headless] AI: {block.text[:200]}")
                    assistant_message["content"].append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    logger.info(f"[headless] Tool: {block.name} | {str(block.input)[:120]}")
                    assistant_message["content"].append({
                        "type": "tool_use", "id": block.id,
                        "name": block.name, "input": block.input
                    })
                    tool_uses_this_turn.append(block)

            turn_messages.append(assistant_message)

            if not tool_uses_this_turn:
                logger.info("[headless] AI did not use any tools — loop ended.")
                break

            tool_results = []
            for tu in tool_uses_this_turn:
                command_text = tu.input.get("command", tu.input.get("action", ""))
                logger.info(f"[headless] Executing: {tu.name} -> {command_text[:100]}")

                # Skip change commands in headless mode (no ticket flow)
                if is_change_command(command_text):
                    logger.warning(
                        f"[headless] Change command skipped "
                        f"(no ticket flow in headless mode): {command_text[:80]}"
                    )
                    raw_result = (
                        "⚠️ This command was skipped in headless mode because it contains changes. "
                        "Check the session for user approval."
                    )
                else:
                    try:
                        raw_result = _dispatch_tool(tu.name, tu.input, connections)
                    except Exception as e:
                        raw_result = f"❌ Tool error: {str(e)}"

                if hasattr(proxy, "filter_ssh_output"):
                    result = proxy.filter_ssh_output(raw_result)
                else:
                    result = raw_result

                logger.info(f"[headless] Result ({tu.name}): {result[:200]}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result
                })

            turn_messages.append({"role": "user", "content": tool_results})

            # Update session after each turn
            all_messages_so_far = all_messages + turn_messages
            update_session_messages(session_id, all_messages_so_far)

        # Complete
        final_messages = all_messages + turn_messages
        set_session_completed(session_id, final_messages)
        logger.info(f"[headless] Completed: {session_id}")

        # Save to session memory (in background)
        try:
            import threading
            _final_sess = get_session(session_id)
            if _final_sess:
                threading.Thread(
                    target=_save_headless_memory,
                    args=(_final_sess,),
                    daemon=True,
                ).start()
        except Exception:
            pass

    except Exception as e:
        logger.error(f"[headless] Error: {e}", exc_info=True)
        final_messages = all_messages + turn_messages
        set_session_failed(session_id, final_messages, str(e))
