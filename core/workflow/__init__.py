"""
Workflow engine — multi-step, conditional, approval-aware automation.

Workflows are YAML files in /workflows/.  Each workflow is a list of steps,
where each step has a `type`: agent, tool, metric_check, wait_approval,
branch, notify, sleep, set, close_incident.

Tool steps reference MCP tools by name and look them up in the live registry
at execution time — so the engine works with whatever tools are installed,
without code changes when new MCPs are added.
"""

from core.workflow.engine import (
    WorkflowEngine,
    STATUS_PENDING, STATUS_RUNNING, STATUS_WAITING_APPROVAL,
    STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED,
)
from core.workflow.loader import (
    load_workflow, list_workflows, validate_workflow, WORKFLOWS_DIR,
)
from core.workflow.storage import (
    list_runs, get_run, save_run, delete_run, RUNS_FILE,
)
from core.workflow.scheduler import (
    start_workflow_scheduler, reload_workflow_jobs,
    stop_workflow_scheduler, get_scheduled_jobs_info,
)

__all__ = [
    "WorkflowEngine",
    "load_workflow", "list_workflows", "validate_workflow", "WORKFLOWS_DIR",
    "list_runs", "get_run", "save_run", "delete_run", "RUNS_FILE",
    "STATUS_PENDING", "STATUS_RUNNING", "STATUS_WAITING_APPROVAL",
    "STATUS_COMPLETED", "STATUS_FAILED", "STATUS_CANCELLED",
    "start_workflow_scheduler", "reload_workflow_jobs",
    "stop_workflow_scheduler", "get_scheduled_jobs_info",
]
