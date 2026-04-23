"""
ReAct Planning Layer.

Analyzes the user's task with AI and generates a step-by-step plan
before execution. If the task is risky or multi-step, shows the plan
in the UI and requests approval.

Usage (in app.py or agent_loop.py):
    from core.planner import generate_plan, is_risky_prompt

    plan = generate_plan(prompt, connections)
    if plan:
        # Show in UI, wait until user approves
        ...
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

from config.settings import settings
from logging_config.logger import get_logger

logger = get_logger("planner")


# ─── Keywords ────────────────────────────────────────────────────────

# If the task contains any of these keywords, it will be planned first.
# Keywords are in English (primary) and Turkish (for bilingual support).
RISK_KEYWORDS = [
    # English
    "reboot", "shutdown", "poweroff", "halt",
    "format", "remove", "delete", "drop", "truncate", "destroy", "purge",
    "update", "upgrade", "install", "uninstall", "migrate",
    "restart", "stop", "kill", "disable", "rm ",
    "overwrite", "wipe", "reset", "revoke",
    # Turkish (bilingual support)
    "sil", "durdur", "kapat", "yeniden başlat", "güncelle",
    "kur", "kaldır", "devre dışı", "biçimlendir",
]


def is_risky_prompt(prompt: str) -> bool:
    """Does the prompt contain a risk keyword?"""
    pl = prompt.lower()
    return any(kw in pl for kw in RISK_KEYWORDS)


# ─── Data Model ─────────────────────────────────────────────────────

@dataclass
class PlanStep:
    order: int
    action: str
    tool: str
    reversible: bool
    risk: str          # "low" | "medium" | "high"


@dataclass
class TaskPlan:
    goal: str
    risk: str          # "low" | "medium" | "high"
    steps: list[PlanStep] = field(default_factory=list)
    prompt: str = ""
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "risk": self.risk,
            "steps": [asdict(s) for s in self.steps],
            "prompt": self.prompt,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TaskPlan":
        steps = [PlanStep(**s) for s in d.get("steps", [])]
        return cls(
            goal=d.get("goal", ""),
            risk=d.get("risk", "low"),
            steps=steps,
            prompt=d.get("prompt", ""),
            created_at=d.get("created_at", ""),
        )


# ─── Plan Generation ────────────────────────────────────────────────

PLAN_SYSTEM_PROMPT = """
You are an IT operations planning assistant.
Generate only a JSON plan for the user's task. Do not write anything else.

## Available MCP Tools
- linux_ops   : Bash command on Linux/Ubuntu servers (SSH)
- windows_ops : PowerShell command on Windows Server (SSH/WinRM)
- esxi_ops    : SSH command on VMware ESXi host (VM, datastore, vSwitch)
- router_ops  : SSH command on Router (routing, NAT, firewall)
- switch_ops  : HTTP API on Switch (VLAN, port, trunk)
- deco_ops    : Deco Mesh Wi-Fi HTTP API (device list, restart)
- commvault_ops: Commvault backup system HTTP API (job, policy, restore)

JSON format (strictly this format):
{
  "goal": "Short task summary (1 sentence)",
  "risk": "low|medium|high",
  "steps": [
    {
      "order": 1,
      "action": "Description of the operation to perform",
      "tool": "linux_ops|windows_ops|esxi_ops|router_ops|switch_ops|deco_ops|commvault_ops",
      "reversible": true|false,
      "risk": "low|medium|high"
    }
  ]
}

Risk Rules:
- Read/list/check -> risk=low, reversible=true
- service restart, package update -> risk=medium, reversible=false
- rm -rf, shutdown, format, drop -> risk=high, reversible=false
- Maximum 8 steps. Only use the tool names listed above.
"""



def generate_plan(prompt: str, connections: dict) -> Optional[TaskPlan]:
    """
    Generates a task plan from the user's prompt.

    Args:
        prompt:      The user's task description
        connections: Available device connections (determines which tools can be used)

    Returns:
        TaskPlan or None (if generation fails)
    """
    from datetime import datetime
    from proxy.ai_proxy import AIProxy

    # Specify available device types
    available_tools = []
    _tool_map = {
        "linux": "linux_ops", "windows": "windows_ops", "esxi": "esxi_ops",
        "router": "router_ops", "switch": "switch_ops", "deco": "deco_ops",
        "commvault": "commvault_ops",
    }
    for dtype, conn in connections.items():
        if conn and conn.get("ip"):
            available_tools.append(_tool_map.get(dtype, dtype))

    context = f"""
Available tools: {', '.join(available_tools) if available_tools else 'None'}
User task: {prompt}
"""

    proxy = AIProxy()
    try:
        response = proxy.chat(
            messages=[{"role": "user", "content": context}],
            tools=[],          # No tool calls — text response only
            system=PLAN_SYSTEM_PROMPT,
        )

        raw_text = ""
        for block in response.content:
            if block.type == "text":
                raw_text += block.text

        # JSON parse
        raw_text = raw_text.strip()
        if "```" in raw_text:
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]

        data = json.loads(raw_text)
        steps = [PlanStep(**s) for s in data.get("steps", [])]
        plan = TaskPlan(
            goal=data.get("goal", prompt[:80]),
            risk=data.get("risk", "low"),
            steps=steps,
            prompt=prompt,
            created_at=datetime.now().isoformat(),
        )
        logger.info(f"Plan generated: {plan.goal} | risk={plan.risk} | {len(plan.steps)} steps")
        return plan

    except json.JSONDecodeError:
        logger.debug("Plan could not be generated in JSON format — proceeding without a plan.")
        return None
    except Exception as e:
        logger.error(f"Plan generation error: {e}")
        return None


# ─── UI Helpers ─────────────────────────────────────────────────────

def format_plan_markdown(plan: TaskPlan) -> str:
    """Returns the plan in Streamlit markdown format."""
    risk_icons = {"low": "🟢", "medium": "🟡", "high": "🔴"}
    icon = risk_icons.get(plan.risk, "❓")
    lines = [
        f"**🎯 Goal:** {plan.goal}",
        f"**Risk Level:** {icon} {plan.risk.upper()}",
        "",
        "**Steps:**",
    ]
    for s in plan.steps:
        rev = "↩️ Reversible" if s.reversible else "⚠️ Irreversible"
        r_icon = risk_icons.get(s.risk, "❓")
        lines.append(f"{s.order}. `{s.tool}`: {s.action} — {r_icon} {rev}")
    return "\n".join(lines)
