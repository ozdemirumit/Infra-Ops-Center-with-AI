"""
MCP Tool definitions — Dynamic Tool Registry.

Built-in and custom tools are managed via tools/registry.py.
ALL_TOOLS can still be imported for backward compatibility.
"""

from tools.registry import get_active_tools

# Backward compatibility: ALL_TOOLS can still be used directly.
# However, agent_loop should call get_active_tools() to get the current list.
ALL_TOOLS = get_active_tools()

# Built-in execute functions can also be imported from here
from tools.ssh_tool import execute_ssh_command
from tools.switch_tool import execute_web_command
from tools.deco_tool import execute_deco_api
from tools.commvault_tool import execute_commvault_api
from tools.windows_tool import execute_windows_command
