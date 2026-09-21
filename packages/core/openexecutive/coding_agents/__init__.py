"""Coding-agent runtimes (Cursor CLI / OpenCode). Tools, not specialists.

The Executive starts, inspects, and cancels long-running ask/plan jobs
against operator-allowlisted workspaces. Write/agent mode is refused until
P2 (human gate + isolated worktree). Do not register this package in
``SPECIALIST_REGISTRY``.
"""

from openexecutive.coding_agents.file_config import (
    get_workspace,
    load_coding_agents_file,
    validate_workspace_path,
)
from openexecutive.coding_agents.models import (
    ALLOWED_MODES,
    WRITE_MODE_DEFERRED_CODE,
    WRITE_MODES,
    CodingJob,
    JobMode,
    JobStatus,
    RuntimeName,
    WorkspaceSpec,
)
from openexecutive.coding_agents.service import (
    cancel_job,
    clear_job_task,
    get_job,
    list_jobs,
    set_job_task,
    start_job,
)
from openexecutive.coding_agents.store import initialize_coding_jobs_db
from openexecutive.coding_agents.tools import CODING_AGENT_HANDLERS, CODING_AGENT_TOOLS

__all__ = [
    "ALLOWED_MODES",
    "CODING_AGENT_HANDLERS",
    "CODING_AGENT_TOOLS",
    "CodingJob",
    "JobMode",
    "JobStatus",
    "RuntimeName",
    "WRITE_MODE_DEFERRED_CODE",
    "WRITE_MODES",
    "WorkspaceSpec",
    "cancel_job",
    "clear_job_task",
    "get_job",
    "get_workspace",
    "initialize_coding_jobs_db",
    "list_jobs",
    "load_coding_agents_file",
    "set_job_task",
    "start_job",
    "validate_workspace_path",
]
