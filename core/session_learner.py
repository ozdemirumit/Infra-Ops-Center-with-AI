"""
Cross-Session Learning Module.

Extracts knowledge that the AI should learn from completed sessions,
indexes it into the 'session_memory' RAG collection, and injects
past learnings into new session system prompts.

FLOW:
  1. When session completes -> extract_and_save(session)
     - Request short Markdown summary + decision notes from AI
     - Save to knowledge_base/session_memory/<id>.md
     - Index in RAG

  2. When new session starts -> get_context_for_session(prompt)
     - Search for topic-related session memories in RAG
     - Inject "Past Experiences" block into system prompt
"""

import re
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import settings
from logging_config.logger import get_logger

logger = get_logger("session_learner")

_MEMORY_SUBDIR = "session_memory"
_SUMMARY_SYSTEM_PROMPT = """
You are an IT operations expert.
You will be given the message history of an operations session.
Produce a short Markdown document containing the following (write nothing else):

## Problem
(1-2 sentences: what was the issue / what was requested)

## Actions Taken
- (each important command/step as a bullet point)

## Result
(successful or failed? 1-2 sentences)

## Lessons Learned
- (what to do if encountered again, things to watch out for)

Rule: Maximum 300 words.
"""


# ─── Summary Extraction ────────────────────────────────────────────────

def extract_and_save(session: dict) -> Optional[str]:
    """
    Extracts learnings from a completed session and saves to RAG.

    Called after session_completed/failed.

    Returns:
        doc_id (str) if successfully saved, None on error.
    """
    if not settings.RAG_ENABLED:
        return None

    session_id = session.get("id", "")
    title = session.get("title", "")
    messages = session.get("messages", [])

    if not messages:
        logger.info(f"Session memory skipped (no messages): {session_id[:8]}")
        return None

    # Already saved?
    memory_dir = Path(settings.KNOWLEDGE_BASE_DIR) / _MEMORY_SUBDIR
    existing = list(memory_dir.glob(f"*{session_id[:12]}*.md")) if memory_dir.exists() else []
    if existing:
        logger.info(f"Session memory already exists: {session_id[:8]}")
        return None

    # Request summary from AI
    summary_md = _generate_summary(title, messages)
    if not summary_md:
        return None

    # Write to disk
    memory_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_id = session_id[:12].replace("-", "")
    filename = f"session_{ts}_{safe_id}.md"
    file_path = memory_dir / filename

    header = (
        f"# Session Memory: {title}\n"
        f"**Session ID:** {session_id}\n"
        f"**Saved:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
    )
    file_path.write_text(header + summary_md, encoding="utf-8")
    logger.info(f"Session memory written to disk: {file_path.name}")

    # Index in RAG
    try:
        from core.rag_engine import RAGEngine
        engine = RAGEngine()
        doc_id = f"session_mem_{ts}_{safe_id}"
        result = engine.index_document(
            file_path=str(file_path),
            doc_id=doc_id,
            source="session_memory",
        )
        logger.info(f"Session memory indexed in RAG: {result}")
        return doc_id
    except Exception as e:
        logger.error(f"Session memory RAG indexing error: {e}")
        return None


# ─── Context Injection ───────────────────────────────────────────────

def get_context_for_session(prompt: str, top_k: int = 3) -> str:
    """
    Returns relevant memories from past experiences for a new session/prompt.

    Returns in Markdown block format for inclusion in the system prompt.
    Returns empty string if nothing is found.

    Args:
        prompt: The user's task description
        top_k:  Maximum number of past experiences to retrieve
    """
    if not settings.RAG_ENABLED:
        return ""

    try:
        from core.rag_engine import RAGEngine
        engine = RAGEngine()

        # Filter only session_memory source chunks
        results = engine.search(query=prompt, top_k=top_k * 3)
        session_results = [
            r for r in results
            if "session_memory" in r.get("filename", "")
               or "session_mem_" in r.get("doc_id", "")
               or _MEMORY_SUBDIR in r.get("filename", "")
        ][:top_k]

        if not session_results:
            return ""

        lines = ["\n\n## 🧠 Past Experiences (From Similar Sessions)\n"]
        for i, r in enumerate(session_results, 1):
            score = r.get("score", 0)
            text = r.get("text", "").strip()
            if score < 0.05 or not text:
                continue
            lines.append(f"### Experience {i} (similarity: {score:.2f})\n{text}\n")

        if len(lines) == 1:
            return ""

        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"Failed to get session memory context: {e}")
        return ""


# ─── Helper ─────────────────────────────────────────────────────────

def _generate_summary(title: str, messages: list) -> Optional[str]:
    """Requests a session summary from AI."""
    try:
        from proxy.ai_proxy import AIProxy

        # Extract text from message history (to avoid using too many tokens)
        transcript = _build_transcript(messages, max_chars=6000)

        proxy = AIProxy()
        response = proxy.chat(
            messages=[{
                "role": "user",
                "content": f"Session title: {title}\n\nMessage history:\n{transcript}"
            }],
            tools=[],
            system=_SUMMARY_SYSTEM_PROMPT,
        )

        summary = ""
        for block in response.content:
            if block.type == "text":
                summary += block.text

        return summary.strip() if summary.strip() else None

    except Exception as e:
        logger.error(f"Failed to generate session summary: {e}")
        return None


def _build_transcript(messages: list, max_chars: int = 6000) -> str:
    """Converts the message list to a readable text format."""
    lines = []
    total = 0

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if isinstance(content, str):
            line = f"[{role.upper()}]: {content[:500]}"
            lines.append(line)
            total += len(line)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                if btype == "text":
                    line = f"[{role.upper()}]: {block.get('text', '')[:500]}"
                    lines.append(line)
                    total += len(line)
                elif btype == "tool_use":
                    inp = block.get("input", {})
                    cmd = inp.get("command", inp.get("action", str(inp)))[:200]
                    line = f"[TOOL {block.get('name', '?')}]: {cmd}"
                    lines.append(line)
                    total += len(line)
                elif btype == "tool_result":
                    res = str(block.get("content", ""))[:300]
                    line = f"[RESULT]: {res}"
                    lines.append(line)
                    total += len(line)

        if total > max_chars:
            lines.append("... (truncated)")
            break

    return "\n".join(lines)
