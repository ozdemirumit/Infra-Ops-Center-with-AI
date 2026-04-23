"""
Dynamic MCP Tool Registry.

Manages the state (active/inactive) of built-in and custom tools.
Provides SSH, HTTP, Python script, or schema-only backend support for custom tools.
State is stored in tools/tools_state.json.
"""

import json
import uuid
import importlib.util
import re
from pathlib import Path
from datetime import datetime
from typing import Optional

from logging_config.logger import get_logger

logger = get_logger("registry")

_STATE_FILE = Path(__file__).parent / "tools_state.json"
_CUSTOM_DIR = Path(__file__).parent / "custom"

# ── Built-in tool definitions and execute functions ──

from tools.ssh_tool import (
    execute_ssh_command,
    LINUX_OPS_TOOL, ESXI_OPS_TOOL, ROUTER_OPS_TOOL,
)
from tools.switch_tool import execute_web_command, SWITCH_OPS_TOOL
from tools.deco_tool import execute_deco_api, DECO_OPS_TOOL
from tools.commvault_tool import execute_commvault_api, COMMVAULT_OPS_TOOL
from tools.windows_tool import execute_windows_command, WINDOWS_OPS_TOOL

_BUILTIN_TOOL_DEFS: dict[str, dict] = {
    t["name"]: t
    for t in [
        LINUX_OPS_TOOL, ESXI_OPS_TOOL, ROUTER_OPS_TOOL,
        SWITCH_OPS_TOOL, DECO_OPS_TOOL, COMMVAULT_OPS_TOOL, WINDOWS_OPS_TOOL,
    ]
}

_BUILTIN_TOOL_ICONS: dict[str, str] = {
    "linux_ops": "🐧", "esxi_ops": "☁️", "router_ops": "🌐",
    "switch_ops": "🔌", "deco_ops": "📶", "commvault_ops": "💾",
    "windows_ops": "🪟",
}

# Tool → Device Type Mapping
# Each tool corresponds to a device type. Device management uses this.
_BUILTIN_DEVICE_TYPES: dict[str, dict] = {
    "linux_ops":    {"device_type": "linux",    "label": "Linux Server",    "icon": "🐧"},
    "esxi_ops":     {"device_type": "esxi",     "label": "VMware ESXi",     "icon": "☁️"},
    "router_ops":   {"device_type": "router",   "label": "Router",          "icon": "🌐"},
    "switch_ops":   {"device_type": "switch",   "label": "Switch",          "icon": "🔌"},
    "deco_ops":     {"device_type": "deco",     "label": "Deco Mesh",       "icon": "📶"},
    "commvault_ops":{"device_type": "commvault","label": "Commvault",       "icon": "💾"},
    "windows_ops":  {"device_type": "windows",  "label": "Windows Server",  "icon": "🪟"},
}


# ═══════════════════════════════════════════════════════════════════
# State Management
# ═══════════════════════════════════════════════════════════════════

def _load_state() -> dict:
    """Read the tools_state.json file (atomic)."""
    from logging_config.atomic_io import atomic_read_json
    return atomic_read_json(_STATE_FILE, default={})


def _save_state(state: dict) -> None:
    """Write to tools_state.json file (atomic)."""
    from logging_config.atomic_io import atomic_write_json
    atomic_write_json(_STATE_FILE, state)


def _ensure_state() -> dict:
    """
    If the state file doesn't exist or has missing built-ins,
    create / update with default values.
    """
    state = _load_state()

    if "builtin_tools" not in state:
        state["builtin_tools"] = {}
    if "custom_tools" not in state:
        state["custom_tools"] = []

    # Ensure all built-in tools are in state
    changed = False
    for name in _BUILTIN_TOOL_DEFS:
        if name not in state["builtin_tools"]:
            state["builtin_tools"][name] = {"enabled": True}
            changed = True

    if changed or not _STATE_FILE.exists():
        _save_state(state)

    return state


# ═══════════════════════════════════════════════════════════════════
# Public API — Read
# ═══════════════════════════════════════════════════════════════════

def get_active_tools() -> list[dict]:
    """
    Returns active (enabled, not deleted) tool definitions.
    Built-in + custom, Anthropic format: {name, description, input_schema}.
    """
    state = _ensure_state()
    tools = []

    # Built-in (skip deleted)
    for name, tool_def in _BUILTIN_TOOL_DEFS.items():
        info = state["builtin_tools"].get(name, {"enabled": True})
        if info.get("deleted"):
            continue
        if info.get("enabled", True):
            tools.append(tool_def)

    # Custom
    for ct in state.get("custom_tools", []):
        if ct.get("enabled", True):
            tools.append({
                "name": ct["name"],
                "description": ct.get("description", ""),
                "input_schema": ct.get("input_schema", {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "Operation to perform"}
                    },
                    "required": ["action"],
                }),
            })

    return tools


def get_all_tools_with_status() -> list[dict]:
    """
    Returns all tools with metadata (for the UI management page).
    Excludes deleted built-in tools.
    """
    state = _ensure_state()
    result = []

    # Built-in (skip deleted)
    for name, tool_def in _BUILTIN_TOOL_DEFS.items():
        info = state["builtin_tools"].get(name, {"enabled": True})
        if info.get("deleted"):
            continue
        result.append({
            "name": name,
            "description": tool_def.get("description", ""),
            "input_schema": tool_def.get("input_schema", {}),
            "enabled": info.get("enabled", True),
            "is_builtin": True,
            "icon": _BUILTIN_TOOL_ICONS.get(name, "🛠️"),
            "execute_backend": "builtin",
        })

    # Custom
    for ct in state.get("custom_tools", []):
        result.append({
            **ct,
            "is_builtin": False,
            "icon": ct.get("icon", "🔧"),
        })

    return result


def get_deleted_builtin_tools() -> list[dict]:
    """Returns built-in tools that have been deleted (for restore UI)."""
    state = _ensure_state()
    result = []
    for name, tool_def in _BUILTIN_TOOL_DEFS.items():
        info = state["builtin_tools"].get(name, {})
        if info.get("deleted"):
            result.append({
                "name": name,
                "description": tool_def.get("description", "")[:80],
                "icon": _BUILTIN_TOOL_ICONS.get(name, "🛠️"),
            })
    return result


def get_device_types() -> dict:
    """
    Generates device types from active tools.
    Format: {"linux": {"label": "Linux Server", "icon": "🐧", "tool_name": "linux_ops"}, ...}
    Device management and sidebar use this function.
    """
    state = _ensure_state()
    device_types = {}

    # Built-in tools (skip deleted)
    for tool_name, dt_info in _BUILTIN_DEVICE_TYPES.items():
        info = state["builtin_tools"].get(tool_name, {"enabled": True})
        if info.get("deleted"):
            continue
        if info.get("enabled", True):
            dtype = dt_info["device_type"]
            device_types[dtype] = {
                "label": f"{dt_info['icon']} {dt_info['label']}",
                "icon": dt_info["icon"],
                "tool_name": tool_name,
            }

    # Custom tools — add if device_type exists
    for ct in state.get("custom_tools", []):
        if ct.get("enabled", True) and ct.get("device_type"):
            dtype = ct["device_type"]
            if dtype not in device_types:
                device_types[dtype] = {
                    "label": f"{ct.get('icon', '🔧')} {ct.get('device_type_label', dtype.title())}",
                    "icon": ct.get("icon", "🔧"),
                    "tool_name": ct["name"],
                }

    return device_types


# ═══════════════════════════════════════════════════════════════════
# Public API — Built-in Toggle
# ═══════════════════════════════════════════════════════════════════

def set_builtin_enabled(tool_name: str, enabled: bool) -> None:
    """Enable/disable a built-in tool."""
    if tool_name not in _BUILTIN_TOOL_DEFS:
        raise ValueError(f"Unknown built-in tool: {tool_name}")
    state = _ensure_state()
    state["builtin_tools"][tool_name]["enabled"] = enabled
    state["builtin_tools"][tool_name].pop("deleted", None)
    _save_state(state)
    logger.info(f"Built-in tool '{tool_name}' → {'enabled' if enabled else 'disabled'}")


def delete_builtin_tool(tool_name: str) -> None:
    """Soft-delete a built-in tool — hidden from UI and AI until restored."""
    if tool_name not in _BUILTIN_TOOL_DEFS:
        raise ValueError(f"Unknown built-in tool: {tool_name}")
    state = _ensure_state()
    state["builtin_tools"][tool_name] = {"enabled": False, "deleted": True}
    _save_state(state)
    logger.info(f"Built-in tool '{tool_name}' deleted (soft delete)")


def restore_builtin_tool(tool_name: str) -> None:
    """Restore a soft-deleted built-in tool."""
    if tool_name not in _BUILTIN_TOOL_DEFS:
        raise ValueError(f"Unknown built-in tool: {tool_name}")
    state = _ensure_state()
    state["builtin_tools"][tool_name] = {"enabled": True}
    _save_state(state)
    logger.info(f"Built-in tool '{tool_name}' restored")


def restore_all_builtin_tools() -> int:
    """Restore all soft-deleted built-in tools. Returns count restored."""
    state = _ensure_state()
    count = 0
    for name in _BUILTIN_TOOL_DEFS:
        info = state["builtin_tools"].get(name, {})
        if info.get("deleted"):
            state["builtin_tools"][name] = {"enabled": True}
            count += 1
    if count:
        _save_state(state)
        logger.info(f"Restored {count} built-in tools")
    return count


# ═══════════════════════════════════════════════════════════════════
# Public API — Custom CRUD
# ═══════════════════════════════════════════════════════════════════

def _validate_tool_name(name: str) -> str:
    """Validate tool name: snake_case, alphanumeric + underscore."""
    name = name.strip().lower()
    if not re.match(r"^[a-z][a-z0-9_]{1,48}$", name):
        raise ValueError(
            "Tool name must start with a lowercase letter, contain only letters/digits/_, "
            "and be 2-49 characters long."
        )
    # Check for conflict with built-in names
    if name in _BUILTIN_TOOL_DEFS:
        raise ValueError(f"'{name}' is already a built-in tool name.")
    return name


def add_custom_tool(
    name: str,
    description: str,
    input_schema: dict,
    execute_backend: str = "schema_only",
    backend_config: Optional[dict] = None,
    icon: str = "🔧",
    device_type: str = "",
    device_type_label: str = "",
) -> str:
    """
    Add a new custom tool. Returns UUID id.

    execute_backend: ssh_command | http_template | python_script | schema_only
    """
    name = _validate_tool_name(name)
    state = _ensure_state()

    # Name conflict check (among custom tools)
    for ct in state["custom_tools"]:
        if ct["name"] == name:
            raise ValueError(f"A custom tool named '{name}' already exists.")

    tool_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()

    tool = {
        "id": tool_id,
        "name": name,
        "description": description,
        "input_schema": input_schema,
        "enabled": True,
        "icon": icon,
        "device_type": device_type,
        "device_type_label": device_type_label or (device_type.title() if device_type else ""),
        "execute_backend": execute_backend,
        "backend_config": backend_config or {},
        "created_at": now,
        "updated_at": now,
    }

    state["custom_tools"].append(tool)
    _save_state(state)
    logger.info(f"Custom tool added: {name} (id={tool_id}, backend={execute_backend})")
    return tool_id


def update_custom_tool(tool_id: str, **kwargs) -> None:
    """Update a custom tool. Send fields to update via kwargs."""
    state = _ensure_state()
    for ct in state["custom_tools"]:
        if ct["id"] == tool_id:
            # Validate if name is changing
            if "name" in kwargs:
                kwargs["name"] = _validate_tool_name(kwargs["name"])
            ct.update(kwargs)
            ct["updated_at"] = datetime.now().isoformat()
            _save_state(state)
            logger.info(f"Custom tool updated: {ct['name']} (id={tool_id})")
            return
    raise ValueError(f"Tool not found: {tool_id}")


def delete_custom_tool(tool_id: str) -> None:
    """Delete a custom tool. Also removes the Python script file if it exists."""
    state = _ensure_state()
    for i, ct in enumerate(state["custom_tools"]):
        if ct["id"] == tool_id:
            # Delete script file
            if ct.get("execute_backend") == "python_script":
                script_file = ct.get("backend_config", {}).get("script_filename")
                if script_file:
                    script_path = _CUSTOM_DIR / script_file
                    if script_path.exists():
                        script_path.unlink()
            state["custom_tools"].pop(i)
            _save_state(state)
            logger.info(f"Custom tool deleted: {ct['name']} (id={tool_id})")
            return
    raise ValueError(f"Tool not found: {tool_id}")


def set_custom_enabled(tool_id: str, enabled: bool) -> None:
    """Enable/disable a custom tool."""
    state = _ensure_state()
    for ct in state["custom_tools"]:
        if ct["id"] == tool_id:
            ct["enabled"] = enabled
            _save_state(state)
            logger.info(f"Custom tool '{ct['name']}' → {'enabled' if enabled else 'disabled'}")
            return
    raise ValueError(f"Tool not found: {tool_id}")


def get_custom_tool(tool_id: str) -> Optional[dict]:
    """Get a single custom tool by id."""
    state = _ensure_state()
    for ct in state["custom_tools"]:
        if ct["id"] == tool_id:
            return ct
    return None


# ═══════════════════════════════════════════════════════════════════
# Tool Generation from Documentation
# ═══════════════════════════════════════════════════════════════════

_GENERATE_PROMPT = """\
You are an MCP (Model Context Protocol) tool designer.
Analyze the following API/CLI documentation and generate MCP tool definitions.

The user can choose between two modes:
- "single": Create a single tool. AI selects the correct endpoint/command based on the action parameter.
- "multi": Create a separate tool for each important endpoint/command.

Mode: {mode}

## Rules
1. For each tool, generate name (snake_case), description (explain what it does),
   input_schema (JSON Schema), execute_backend, and backend_config.
2. execute_backend must be one of: "ssh_command", "http_template", "schema_only"
3. http_template backend_config: method, url_template, headers, body_template, timeout
4. ssh_command backend_config: device_type, command_template
5. Return your response ONLY as a JSON array, do not add any other text.

## Expected Output Format
```json
[
  {{
    "name": "tool_name_ops",
    "description": "Tool description",
    "input_schema": {{
      "type": "object",
      "properties": {{
        "action": {{"type": "string", "description": "parameter description"}}
      }},
      "required": ["action"]
    }},
    "execute_backend": "http_template",
    "backend_config": {{
      "method": "GET",
      "url_template": "https://example.com/api/{{{{action}}}}",
      "headers": {{}},
      "timeout": 30
    }}
  }}
]
```

## Documentation Content
{doc_text}
"""


def generate_tools_from_doc(
    doc_text: str,
    mode: str = "single",
    proxy_base: str = None,
    proxy_api_key: str = None,
) -> list[dict]:
    """
    Generates MCP tool definitions from documentation text using AI.

    Args:
        doc_text: API/CLI documentation text
        mode: "single" (single tool) or "multi" (multiple tools)
        proxy_base: AI Proxy base URL (None to use settings)
        proxy_api_key: AI Proxy API key (None to use settings)

    Returns:
        List of generated tool definitions
    """
    from config.settings import settings
    import httpx

    base = proxy_base or f"http://{settings.PROXY_HOST}:{settings.PROXY_PORT}"
    key = proxy_api_key or settings.PROXY_API_KEY

    # Truncate if document is too long (token limit)
    max_chars = 30000
    if len(doc_text) > max_chars:
        doc_text = doc_text[:max_chars] + "\n\n[... remainder of document truncated ...]"

    prompt = _GENERATE_PROMPT.format(mode=mode, doc_text=doc_text)

    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "system": "You are a JSON generator assistant. Return ONLY a valid JSON array, do not add any explanation.",
    }

    resp = httpx.post(
        f"{base}/v1/chat",
        json=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    data = resp.json()

    # Extract text content from response
    raw_text = ""
    content = data.get("content", [])
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                raw_text += block.get("text", "")
    elif isinstance(content, str):
        raw_text = content

    # Parse JSON array
    raw_text = raw_text.strip()
    # Clean up ```json ... ``` blocks
    if "```" in raw_text:
        import re
        match = re.search(r"```(?:json)?\s*\n?(.*?)```", raw_text, re.DOTALL)
        if match:
            raw_text = match.group(1).strip()

    tools = json.loads(raw_text)

    if not isinstance(tools, list):
        tools = [tools]

    # Validation
    validated = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        if not t.get("name") or not t.get("description"):
            continue
        # Add default input_schema
        if "input_schema" not in t:
            t["input_schema"] = {
                "type": "object",
                "properties": {"action": {"type": "string", "description": "Operation to perform"}},
                "required": ["action"],
            }
        if "execute_backend" not in t:
            t["execute_backend"] = "schema_only"
        if "backend_config" not in t:
            t["backend_config"] = {}
        validated.append(t)

    logger.info(f"{len(validated)} tools generated from documentation (mode={mode})")
    return validated


# ═══════════════════════════════════════════════════════════════════
# Custom Tool Execution
# ═══════════════════════════════════════════════════════════════════

def dispatch_custom_tool(tool_name: str, tool_input: dict, connections: dict) -> str:
    """
    Execute a custom tool based on its backend type.
    Built-in tools are NOT executed with this function — agent_loop uses its own dispatch.
    """
    state = _ensure_state()
    tool = None
    for ct in state["custom_tools"]:
        if ct["name"] == tool_name:
            tool = ct
            break

    if not tool:
        return f"❌ Unknown tool: {tool_name}"

    backend = tool.get("execute_backend", "schema_only")
    config = tool.get("backend_config", {})
    action = tool_input.get("action", tool_input.get("command", ""))

    try:
        if backend == "ssh_command":
            return _exec_ssh_backend(config, action, connections)
        elif backend == "http_template":
            return _exec_http_backend(config, action, tool_input)
        elif backend == "python_script":
            return _exec_python_backend(config, tool_input, connections)
        elif backend == "schema_only":
            return "⚠️ This tool's execution backend has not been configured yet."
        else:
            return f"❌ Unknown backend type: {backend}"
    except Exception as e:
        logger.error(f"Custom tool execution error ({tool_name}): {e}")
        return f"❌ Tool execution error: {str(e)}"


def _exec_ssh_backend(config: dict, action: str, connections: dict) -> str:
    """SSH command backend."""
    from devices.storage import DeviceStorage

    device_type = config.get("device_type", "linux")
    command_template = config.get("command_template", "{{action}}")
    command = command_template.replace("{{action}}", action)

    servers = DeviceStorage.get_by_type(device_type)
    if not servers:
        return f"❌ No registered {device_type} server found."

    results = []
    for server in servers:
        results.append(f"=== {server['name']} ({server['ip']}) ===")
        res = execute_ssh_command(server["ip"], server["user"], server["password"], command)
        results.append(res)
    return "\n".join(results)


def _exec_http_backend(config: dict, action: str, tool_input: dict) -> str:
    """HTTP request backend."""
    import requests

    method = config.get("method", "GET").upper()
    url_template = config.get("url_template", "")
    headers = config.get("headers", {})
    body_template = config.get("body_template")
    timeout = config.get("timeout", 30)

    # Template substitution
    url = url_template.replace("{{action}}", action)
    for key, val in tool_input.items():
        url = url.replace(f"{{{{{key}}}}}", str(val))

    kwargs = {"headers": headers, "timeout": timeout}
    if body_template and method in ("POST", "PUT", "PATCH"):
        body_str = json.dumps(body_template) if isinstance(body_template, dict) else str(body_template)
        body_str = body_str.replace("{{action}}", action)
        for key, val in tool_input.items():
            body_str = body_str.replace(f"{{{{{key}}}}}", str(val))
        try:
            kwargs["json"] = json.loads(body_str)
        except json.JSONDecodeError:
            kwargs["data"] = body_str

    resp = requests.request(method, url, **kwargs)
    output = resp.text[:5000]
    return f"HTTP {resp.status_code}\n{output}"


def _exec_python_backend(config: dict, tool_input: dict, connections: dict) -> str:
    """Uploaded Python script backend."""
    script_filename = config.get("script_filename")
    if not script_filename:
        return "❌ Python script filename is not configured."

    # Security: only filename, prevent path traversal
    script_filename = Path(script_filename).name
    script_path = _CUSTOM_DIR / script_filename

    if not script_path.exists():
        return f"❌ Script file not found: {script_filename}"

    spec = importlib.util.spec_from_file_location("custom_tool_exec", str(script_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    if not hasattr(mod, "execute"):
        return f"❌ 'execute(tool_input, connections)' function not found in script."

    result = mod.execute(tool_input, connections)
    return str(result)[:5000]


# ═══════════════════════════════════════════════════════════════════
# Dynamic System Prompt Section
# ═══════════════════════════════════════════════════════════════════

def get_dynamic_system_prompt_section() -> str:
    """
    Generates the '## Available MCP Tools' section for SYSTEM_PROMPT from active tools.
    """
    tools = get_active_tools()
    if not tools:
        return "\n## Available MCP Tools\nNo active tools currently available.\n"

    lines = ["## Available MCP Tools"]
    for t in tools:
        icon = _BUILTIN_TOOL_ICONS.get(t["name"], "🔧")
        # Reduce description to a single line
        desc = t.get("description", "").replace("\n", " ").strip()
        if len(desc) > 200:
            desc = desc[:197] + "..."
        lines.append(f"- **{t['name']}**: {desc}")

    return "\n".join(lines) + "\n"
