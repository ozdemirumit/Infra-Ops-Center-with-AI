"""
Automatic Runbook Saving Module.

When an agent conversation completes, extracts tool steps from turn_messages
into a readable Markdown format and saves to the RAG engine.
"""

import re
from datetime import datetime
from pathlib import Path
from config.settings import settings
from logging_config.logger import get_logger

logger = get_logger("runbook_saver")

# Subdirectory where runbooks are written
_RUNBOOK_SUBDIR = "runbooks"

# Minimum number of tool steps required to save
_MIN_TOOL_STEPS = 1


def extract_runbook(prompt: str, turn_messages: list) -> str | None:
    """
    Generates a readable Markdown runbook text from tool_use + tool_result
    blocks in the turn_messages list.

    Args:
        prompt:        The user's initial message (problem description)
        turn_messages: Temporary message list from run_agent_loop

    Returns:
        Runbook text (str) or None if not worth saving
    """
    steps = []
    final_answer = ""

    # Scan messages: match tool_use + tool_result
    # turn_messages format:
    #   [user_msg, assistant_msg, user_tool_results, assistant_msg, ...]
    tool_results_map: dict[str, str] = {}

    for msg in turn_messages:
        role = msg.get("role", "")
        content = msg.get("content", [])

        if not isinstance(content, list):
            # Capture the last text response (if string)
            if role == "assistant" and isinstance(content, str):
                final_answer = content
            continue

        for block in content:
            if not isinstance(block, dict):
                continue

            btype = block.get("type", "")

            if btype == "tool_result":
                tool_results_map[block.get("tool_use_id", "")] = _truncate(
                    block.get("content", ""), max_chars=800
                )

            elif btype == "tool_use":
                tool_input = block.get("input", {})
                command = tool_input.get("command") or tool_input.get("action") or str(tool_input)
                steps.append({
                    "id": block.get("id", ""),
                    "tool": block.get("name", "unknown"),
                    "command": _truncate(command, 400),
                    "target": tool_input.get("target_host", ""),
                })

            elif btype == "text" and role == "assistant":
                text = block.get("text", "").strip()
                if text:
                    final_answer = text  # Last text response

    if len(steps) < _MIN_TOOL_STEPS:
        return None  # Don't save conversations that ended without tool usage

    # Match results to tool steps
    lines = [
        f"# Runbook: {_summarize(prompt)}",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Problem:** {prompt}\n",
        "## Steps\n",
    ]

    for i, step in enumerate(steps, 1):
        result = tool_results_map.get(step["id"], "_result not available_")
        target_line = f"\n**Target:** `{step['target']}`" if step["target"] else ""
        lines.append(
            f"### Step {i} — {step['tool']}{target_line}\n"
            f"**Command:**\n```\n{step['command']}\n```\n"
            f"**Result:**\n```\n{result}\n```\n"
        )

    if final_answer:
        lines.append(f"## Conclusion\n{_truncate(final_answer, 1000)}\n")

    lines.append(f"**Tags:** {_extract_tags(prompt, steps)}")

    return "\n".join(lines)


def save_runbook_to_rag(runbook_text: str, problem_summary: str) -> dict:
    """
    Writes the runbook text to knowledge_base/runbooks/ and indexes in RAG.

    Args:
        runbook_text:    Output of extract_runbook()
        problem_summary: Short summary for generating the filename

    Returns:
        {"doc_id": "...", "chunks": N, "status": "ok"} or error dict
    """
    try:
        # Create target directory
        runbook_dir = Path(settings.KNOWLEDGE_BASE_DIR) / _RUNBOOK_SUBDIR
        runbook_dir.mkdir(parents=True, exist_ok=True)

        # Filename: date + safe summary
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = re.sub(r"[^\w\-]", "_", _summarize(problem_summary, max_words=6))
        filename = f"runbook_{ts}_{safe_name}.md"
        file_path = runbook_dir / filename

        # Write to disk
        file_path.write_text(runbook_text, encoding="utf-8")
        logger.info(f"Runbook file written: {file_path}")

        # Index in RAG
        from core.rag_engine import RAGEngine
        engine = RAGEngine()
        result = engine.index_document(
            file_path=str(file_path),
            doc_id=f"runbook_{ts}",
            source="runbook",
        )
        logger.info(f"Runbook indexed in RAG: {result}")
        return result

    except Exception as e:
        logger.error(f"Failed to save runbook: {e}")
        return {"status": "error", "error": str(e)}


# ─── Helper Functions ───────────────────────────────────────────────

def _truncate(text: str, max_chars: int) -> str:
    """Limits text to max_chars."""
    text = str(text).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... (truncated)"


def _summarize(text: str, max_words: int = 8) -> str:
    """Returns the first max_words words."""
    words = text.strip().split()
    return " ".join(words[:max_words])


def _extract_tags(prompt: str, steps: list) -> str:
    """Generates simple tags from the problem and tools used."""
    tags = set()
    for step in steps:
        tags.add(step["tool"])
    # Common IT terms found in the prompt
    keywords = ["disk", "memory", "ram", "cpu", "network", "ssh", "log",
                "backup", "service", "restart", "port", "firewall", "dns"]
    prompt_lower = prompt.lower()
    for kw in keywords:
        if kw in prompt_lower:
            tags.add(kw)
    return ", ".join(sorted(tags))
