"""
YAML workflow loader + validator.

Workflows live in /workflows/*.yaml. Each file defines a single workflow with:

    name: identifier
    description: human-readable summary
    trigger:                   # optional
      type: incident | schedule | manual
      ...trigger-specific fields...
    inputs:                    # optional default inputs
      key: value
    steps:
      - id: step_id
        type: agent|tool|metric_check|wait_approval|branch|notify|sleep|set|close_incident
        ...type-specific fields...

Tool steps reference MCP tools by name. The names are NOT validated against
the registry at load time — that lets users author workflows for MCPs that
will only be installed later. They are resolved at execution time.
"""

from pathlib import Path
from typing import Optional

import yaml

from logging_config.logger import get_logger

logger = get_logger("workflow.loader")

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent.parent / "workflows"
WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)


VALID_STEP_TYPES = {
    "agent", "tool", "metric_check", "wait_approval",
    "branch", "notify", "sleep", "set", "close_incident",
}


def list_workflows() -> list[dict]:
    """Discover all *.yaml workflows. Returns lightweight metadata."""
    out = []
    for p in sorted(WORKFLOWS_DIR.glob("*.y*ml")):
        try:
            wf = load_workflow(p.stem)
            out.append({
                "file": p.name,
                "name": wf.get("name", p.stem),
                "description": wf.get("description", ""),
                "trigger": wf.get("trigger", {}),
                "step_count": len(wf.get("steps", [])),
                "errors": validate_workflow(wf),
            })
        except Exception as e:
            logger.warning(f"Failed to read {p}: {e}")
            out.append({
                "file": p.name, "name": p.stem, "description": "",
                "trigger": {}, "step_count": 0,
                "errors": [f"YAML parse error: {e}"],
            })
    return out


def load_workflow(name_or_path: str) -> dict:
    """Load a workflow by name (filename without extension) or absolute path."""
    p = Path(name_or_path)
    if not p.is_absolute():
        # Try .yaml then .yml
        for ext in (".yaml", ".yml"):
            candidate = WORKFLOWS_DIR / f"{name_or_path}{ext}"
            if candidate.exists():
                p = candidate
                break
        else:
            # Last resort — maybe user passed full filename
            candidate = WORKFLOWS_DIR / name_or_path
            if candidate.exists():
                p = candidate
            else:
                raise FileNotFoundError(f"Workflow not found: {name_or_path}")

    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"{p}: workflow root must be a mapping")

    data["_source_file"] = str(p)
    if "name" not in data:
        data["name"] = p.stem
    return data


def validate_workflow(wf: dict) -> list[str]:
    """Return a list of human-readable errors. Empty list = valid."""
    errors: list[str] = []

    if not isinstance(wf, dict):
        return ["workflow must be a mapping"]

    if not wf.get("name"):
        errors.append("missing required field: name")

    steps = wf.get("steps", [])
    if not isinstance(steps, list) or not steps:
        errors.append("steps must be a non-empty list")
        return errors

    seen_ids = set()
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"step {i}: must be a mapping")
            continue
        sid = step.get("id")
        if not sid:
            errors.append(f"step {i}: missing id")
        elif sid in seen_ids:
            errors.append(f"step {i}: duplicate id '{sid}'")
        else:
            seen_ids.add(sid)

        stype = step.get("type")
        if stype not in VALID_STEP_TYPES:
            errors.append(f"step {sid or i}: invalid type '{stype}' "
                          f"(valid: {sorted(VALID_STEP_TYPES)})")
            continue

        # Type-specific minimum requirements
        if stype == "tool" and not step.get("tool"):
            errors.append(f"step {sid}: tool step requires 'tool: <mcp_tool_name>'")
        if stype == "agent" and not step.get("prompt"):
            errors.append(f"step {sid}: agent step requires 'prompt'")
        if stype == "metric_check" and not step.get("metric"):
            errors.append(f"step {sid}: metric_check step requires 'metric'")
        if stype == "branch":
            if not step.get("when"):
                errors.append(f"step {sid}: branch step requires 'when'")
            if not step.get("then") and not step.get("else"):
                errors.append(f"step {sid}: branch step needs 'then' or 'else'")
        if stype == "notify" and not step.get("message"):
            errors.append(f"step {sid}: notify step requires 'message'")
        if stype == "set" and not isinstance(step.get("values"), dict):
            errors.append(f"step {sid}: set step requires 'values: <mapping>'")

    return errors


def save_workflow(name: str, content: str) -> Path:
    """Write a workflow YAML file. Returns the saved path."""
    safe_name = "".join(c for c in name if c.isalnum() or c in "_-").lower()
    if not safe_name:
        raise ValueError("workflow name must contain at least one alphanumeric character")
    p = WORKFLOWS_DIR / f"{safe_name}.yaml"
    # Validate before write
    parsed = yaml.safe_load(content)
    if not isinstance(parsed, dict):
        raise ValueError("workflow must parse to a mapping")
    errs = validate_workflow(parsed)
    if errs:
        raise ValueError("Validation failed:\n  - " + "\n  - ".join(errs))
    p.write_text(content, encoding="utf-8")
    return p


def delete_workflow(name: str) -> bool:
    for ext in (".yaml", ".yml"):
        p = WORKFLOWS_DIR / f"{name}{ext}"
        if p.exists():
            p.unlink()
            return True
    return False
