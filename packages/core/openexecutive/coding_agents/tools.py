"""Anthropic tool definitions + handlers for coding-agent jobs.

JSON in / JSON out. User-facing Executive text must not name Cursor or
OpenCode — these descriptions tell the model that, and name the vendors only
as words it must not say to the principal.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from openexecutive.coding_agents import service

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Awaitable[str]]


START_CODING_JOB_TOOL: dict[str, Any] = {
    "name": "start_coding_job",
    "description": (
        "Start a long-running implementation or analysis job against one "
        "allowlisted repository. Use this only when the principal wants code "
        "read, explained, or planned in a known workspace — never for general "
        "business questions. "
        "`workspace_id` is an operator-defined slug, NOT a filesystem path "
        "(do not invent paths or pass `/repos/...`). "
        "Modes are ask (read/analyze) and plan (propose a change). Do not "
        "claim you will edit, commit, or open a pull request — write/agent "
        "mode is not available. "
        "This returns a job_id immediately; poll get_coding_job for the "
        "artifact. In user-facing replies, do not name Cursor, OpenCode, or "
        "any vendor coding agent — say you started an analysis job."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": ("Allowlisted workspace slug (e.g. 'product'). Never a path."),
            },
            "task": {
                "type": "string",
                "description": "What to analyze or plan in that repository.",
            },
            "mode": {
                "type": "string",
                "enum": ["ask", "plan"],
                "description": "ask = read/analyze; plan = propose a change. Not write.",
            },
            "runtime": {
                "type": "string",
                "enum": ["cursor", "opencode"],
                "description": (
                    "Optional backend. Omit to use the workspace default. "
                    "Do not mention this name to the user."
                ),
            },
        },
        "required": ["workspace_id", "task", "mode"],
    },
}


GET_CODING_JOB_TOOL: dict[str, Any] = {
    "name": "get_coding_job",
    "description": (
        "Fetch status and (when finished) the artifact for a coding job "
        "started with start_coding_job. Use the job_id that tool returned. "
        "Relay the analysis in your own words. Do not name Cursor, OpenCode, "
        "or any vendor coding agent in user-facing text."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "The job_id returned by start_coding_job.",
            },
        },
        "required": ["job_id"],
    },
}


CANCEL_CODING_JOB_TOOL: dict[str, Any] = {
    "name": "cancel_coding_job",
    "description": (
        "Cancel a queued or running coding job. Use when the principal "
        "withdraws the request or the job is no longer useful. Do not name "
        "Cursor, OpenCode, or any vendor coding agent in user-facing text."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "The job_id returned by start_coding_job.",
            },
        },
        "required": ["job_id"],
    },
}


# Sorted by name so the cached tools block stays stable (cancel, get, start).
CODING_AGENT_TOOLS: list[dict[str, Any]] = sorted(
    [CANCEL_CODING_JOB_TOOL, GET_CODING_JOB_TOOL, START_CODING_JOB_TOOL],
    key=lambda tool: tool["name"],
)


def _dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _bad_input(message: str) -> str:
    return _dumps({"error": message, "code": "invalid_input"})


async def handle_start_coding_job(tool_input: dict[str, Any]) -> str:
    workspace_id = tool_input.get("workspace_id")
    task = tool_input.get("task")
    mode = tool_input.get("mode")
    runtime = tool_input.get("runtime")
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        return _bad_input("workspace_id is required and must be a slug, not a path.")
    if any(sep in workspace_id for sep in ("/", "\\")) or ".." in workspace_id:
        return _bad_input("workspace_id must be an allowlisted slug, not a filesystem path.")
    if not isinstance(task, str) or not task.strip():
        return _bad_input("task is required.")
    if not isinstance(mode, str) or not mode.strip():
        return _bad_input("mode is required (ask or plan).")
    if runtime is not None and not isinstance(runtime, str):
        return _bad_input("runtime must be a string when provided.")
    try:
        result = await service.start_job(
            workspace_id.strip(),
            task.strip(),
            mode.strip(),
            runtime.strip() if isinstance(runtime, str) and runtime.strip() else None,
        )
    except Exception:
        logger.exception("start_coding_job failed")
        return _dumps({"error": "Failed to start coding job.", "code": "internal_error"})
    return _dumps(result)


async def handle_get_coding_job(tool_input: dict[str, Any]) -> str:
    job_id = tool_input.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        return _bad_input("job_id is required.")
    try:
        result = await service.get_job(job_id.strip())
    except Exception:
        logger.exception("get_coding_job failed")
        return _dumps({"error": "Failed to fetch coding job.", "code": "internal_error"})
    return _dumps(result)


async def handle_cancel_coding_job(tool_input: dict[str, Any]) -> str:
    job_id = tool_input.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        return _bad_input("job_id is required.")
    try:
        result = await service.cancel_job(job_id.strip())
    except Exception:
        logger.exception("cancel_coding_job failed")
        return _dumps({"error": "Failed to cancel coding job.", "code": "internal_error"})
    return _dumps(result)


CODING_AGENT_HANDLERS: dict[str, Handler] = {
    "cancel_coding_job": handle_cancel_coding_job,
    "get_coding_job": handle_get_coding_job,
    "start_coding_job": handle_start_coding_job,
}
