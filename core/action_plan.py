"""
Action plan generator.

Given a free-form problem description, returns a human-readable remediation
plan as Markdown: numbered steps, each with an explanation, the exact
commands to run, expected output, and risk notes. Capability gaps are
flagged with manual instructions.

This is NOT a workflow YAML generator — by design. The output is a guide
a human (or the agent loop) can follow / paste / paraphrase, not a recipe
the engine consumes. For automated execution, build a workflow YAML in
the Editor tab and trigger it from the Library.
"""

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional

from logging_config.logger import get_logger

logger = get_logger("action_plan")


@dataclass
class ActionPlan:
    problem: str
    plan_markdown: str
    available_mcps: list[str]
    target: dict          # {device_type, name, ip, ...}  — best-effort
    risk: str             # "low" | "medium" | "high"
    created_at: str
    model: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


_SYSTEM_PROMPT = """\
You are a senior systems & network engineer. Produce a remediation plan
for the user's problem as **Markdown only** — no JSON, no YAML, no code
fences around the whole document.

## Output structure (use these exact headings)

# Plan: <short title>

**Risk:** low | medium | high   (single word)

## Context
A 2-3 sentence read of what the problem most likely is and what data
you'll need.

## Steps

For every step use this exact template:

### Step <n> — <short title>

**What this does:** one short sentence.

**Run via MCP:** `<mcp_name>` _(or "manual — no MCP supports this")_

**Command(s):**
```bash
<the exact command, ready to paste>
```

**Expected output / success criteria:** one short sentence.

**If it fails:** one short sentence explaining the next move.

## Rollback
A short paragraph or numbered list describing how to undo destructive
steps. If nothing was destructive, write "Not needed — read-only plan."

## Notes
Any risks, prerequisites, or follow-up checks the operator should know.

---

## Rules

1. Use ONLY MCPs that appear in the "Available MCPs" list below. If
   something cannot be done by any listed MCP, write
   `**Run via MCP:** manual — no MCP supports this` and describe the
   manual procedure as the command block (e.g. "Open the iLO web UI,
   then…"). DO NOT invent MCP names.
2. Commands must be runnable verbatim. Use generic placeholders only
   when the user did not give specifics, and mark them clearly like
   `<replace-with-server-ip>`.
3. Keep steps small and reversible where possible. Always start with
   read-only diagnostics before any change.
4. If the problem is ambiguous, list the most likely cause first and
   note alternative branches in **Notes**.
5. Maximum 10 steps. If more are needed, group them.
6. NEVER include credentials, tokens, or private network details that
   weren't given in the input.
7. The risk word at the top must reflect the most destructive step in
   the plan, not the average.
"""


def _estimate_risk(text: str) -> str:
    """Very small fallback risk classifier from the markdown body."""
    lower = text.lower()
    if any(w in lower for w in ("rm -rf", "drop ", "format ", "shutdown",
                                "destroy", "wipe", "truncate", "factory reset")):
        return "high"
    if any(w in lower for w in ("restart", "reboot", "stop ", "kill ",
                                "upgrade", "install ", "disable")):
        return "medium"
    return "low"


def _detect_risk_header(md: str) -> Optional[str]:
    """Pull the 'Risk: low|medium|high' line if Claude emitted it."""
    import re
    m = re.search(r"\*\*Risk:\*\*\s*(low|medium|high)",
                  md, re.IGNORECASE)
    return m.group(1).lower() if m else None


def generate_action_plan(
    problem: str,
    target: Optional[dict] = None,
    *,
    extra_context: str = "",
) -> ActionPlan:
    """
    Build an action plan for `problem`.

    Args:
        problem:        Free-form description of what's wrong / what to do.
        target:         Optional device info dict {name, ip, device_type, ...}.
        extra_context:  Additional notes prepended to the user message.

    Returns:
        ActionPlan — even on partial failures, with a degraded plan_markdown.
    """
    from proxy.ai_proxy import AIProxy
    from tools.registry import get_active_tools

    tools = get_active_tools()
    mcp_lines = []
    for t in tools:
        name = t.get("name", "")
        desc = (t.get("description") or "").strip().splitlines()[0][:120]
        if name:
            mcp_lines.append(f"- `{name}` — {desc}")
    available_mcps = [t.get("name", "") for t in tools if t.get("name")]

    user_lines = []
    if target:
        bits = ", ".join(f"{k}={v}" for k, v in target.items()
                         if v and k in ("name", "ip", "device_type",
                                        "hostname", "os"))
        if bits:
            user_lines.append(f"Target: {bits}")
    if extra_context:
        user_lines.append(extra_context)
    user_lines.append(f"Problem: {problem}")

    system = (
        _SYSTEM_PROMPT
        + "\n\n## Available MCPs (use ONLY these tool names)\n"
        + ("\n".join(mcp_lines) if mcp_lines else "_(none currently registered)_")
    )

    proxy = AIProxy()
    model_used = ""
    plan_md = ""
    try:
        resp = proxy.chat(
            messages=[{"role": "user", "content": "\n".join(user_lines)}],
            tools=[],
            system=system,
        )
        for block in resp.content:
            if block.type == "text":
                plan_md += block.text
        model_used = getattr(resp, "model", "") or ""
    except Exception as e:
        logger.error(f"Action plan generation failed: {e}")
        plan_md = (
            f"# Plan generation failed\n\n"
            f"**Error:** `{type(e).__name__}: {e}`\n\n"
            "Try simplifying the problem statement or retrying once the "
            "AI provider is reachable."
        )

    risk = _detect_risk_header(plan_md) or _estimate_risk(plan_md)

    plan = ActionPlan(
        problem=problem,
        plan_markdown=plan_md.strip(),
        available_mcps=available_mcps,
        target=target or {},
        risk=risk,
        created_at=datetime.now().isoformat(),
        model=model_used,
    )
    logger.info(
        f"Action plan generated for: {problem[:80]!r}  "
        f"risk={risk}  mcps={len(available_mcps)}  len={len(plan_md)}"
    )
    return plan


def save_plan_as_runbook(plan: ActionPlan) -> Optional[str]:
    """
    Persist an ActionPlan to /knowledge_base/runbooks/ as Markdown and
    index it in RAG. Returns the file path or None on failure.
    """
    try:
        from pathlib import Path
        from config.settings import settings

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Safe filename — alphanum/underscore only, capped at 40 chars
        safe = "".join(c if c.isalnum() else "_" for c in plan.problem)[:40].strip("_")
        path = Path(settings.KNOWLEDGE_BASE_DIR) / "runbooks" / f"plan_{safe}_{ts}.md"
        path.parent.mkdir(parents=True, exist_ok=True)

        body = (
            f"<!-- Generated by action_plan -->\n"
            f"<!-- Problem: {plan.problem} -->\n"
            f"<!-- Created: {plan.created_at} -->\n"
            f"<!-- Risk: {plan.risk} -->\n\n"
            f"{plan.plan_markdown}\n\n"
            f"---\n"
            f"**Available MCPs at generation time:** "
            f"{', '.join(f'`{m}`' for m in plan.available_mcps) or '_none_'}\n"
        )
        path.write_text(body, encoding="utf-8")

        # Best-effort RAG indexing
        try:
            from core.rag_engine import RAGEngine
            RAGEngine().index_document(
                file_path=str(path),
                doc_id=f"plan_{ts}",
                source="action_plan",
            )
        except Exception as e:
            logger.debug(f"RAG indexing skipped: {e}")

        return str(path)
    except Exception as e:
        logger.error(f"Failed to save plan as runbook: {e}")
        return None
