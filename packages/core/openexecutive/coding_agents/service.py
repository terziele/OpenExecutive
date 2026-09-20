"""Start / get / cancel / list coding-agent jobs. Jobs run as asyncio tasks."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from openexecutive.coding_agents import audit, store
from openexecutive.coding_agents.cursor_cli import CursorCliRuntime
from openexecutive.coding_agents.file_config import (
    CodingAgentsConfigError,
    get_workspace,
    load_coding_agents_file,
    resolve_runtime_choice,
    resolved_runtime_spec,
    validate_workspace_path,
)
from openexecutive.coding_agents.models import (
    ALLOWED_MODES,
    WRITE_MODE_DEFERRED_CODE,
    WRITE_MODE_DEFERRED_MESSAGE,
    WRITE_MODES,
    CodingJob,
    WorkspaceSpec,
)
from openexecutive.coding_agents.opencode import OpenCodeRuntime
from openexecutive.coding_agents.runtimes import (
    CodingRuntime,
    CodingRuntimeError,
    get_runtime,
)
from openexecutive.config import get_settings

logger = logging.getLogger(__name__)

# Imported so cursor_cli / opencode register their RUNTIME_BUILDERS on load.
_RUNTIME_IMPLS = (CursorCliRuntime, OpenCodeRuntime)

# Principal-visible cards for these statuses only. cancelled is a withdrawal
# and must not land in /today "Needs you".
_COMPLETION_NOTIFY_STATUSES = frozenset({"succeeded", "failed", "timed_out"})
_COMPLETION_HEADLINES = {
    "succeeded": "Coding analysis ready",
    "failed": "Coding analysis failed",
    "timed_out": "Coding analysis timed out",
}
# Principal cards must not copy runtime/stderr (vendor names, paths).
_PRINCIPAL_ERROR_DETAIL = {
    "failed": (
        "The analysis job did not finish successfully. "
        "Ask me for the details if you need them."
    ),
    "timed_out": "The analysis job exceeded its time limit.",
}
_ALERT_BODY_MAX = 8000
_HEADLINE_MAX = 160
_TRUNCATION_SUFFIX = "\n...[truncated]"

_JOB_TASKS: dict[str, asyncio.Task[Any]] = {}
_JOB_RUNTIMES: dict[str, CodingRuntime] = {}
_start_lock: asyncio.Lock | None = None


def set_job_task(job_id: str, task: asyncio.Task[Any]) -> None:
    _JOB_TASKS[job_id] = task


def clear_job_task(job_id: str | None = None) -> None:
    """Drop one tracked task, or all of them when ``job_id`` is omitted."""
    if job_id is None:
        _JOB_TASKS.clear()
        _JOB_RUNTIMES.clear()
        return
    _JOB_TASKS.pop(job_id, None)
    _JOB_RUNTIMES.pop(job_id, None)


def job_tasks() -> dict[str, asyncio.Task[Any]]:
    return _JOB_TASKS


def _lock() -> asyncio.Lock:
    global _start_lock
    if _start_lock is None:
        _start_lock = asyncio.Lock()
    return _start_lock


def _error(message: str, code: str, **extra: Any) -> dict[str, Any]:
    return {"error": message, "code": code, **extra}


def _truncate_for_tool(job: dict[str, Any]) -> dict[str, Any]:
    max_chars = get_settings().tool_result_max_chars
    out = dict(job)
    artifact = out.get("artifact") or ""
    if isinstance(artifact, str) and len(artifact) > max_chars:
        out["artifact"] = artifact[: max_chars - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX
    events = list(out.get("events") or [])
    kept: list[str] = []
    used = 0
    for event in events:
        if used + len(event) > max_chars:
            break
        kept.append(event)
        used += len(event)
    out["events"] = kept
    return out


async def start_job(
    workspace_id: str,
    task: str,
    mode: str,
    runtime: str | None = None,
    *,
    has_user_consent: bool = False,
) -> dict[str, Any]:
    """Queue an ask/plan job. Returns the job dict or a JSON-serializable error.

    ``has_user_consent`` is accepted for the P2 write-gate and recorded on the
    start audit row. P1 never runs write/agent mode even when it is True.
    """
    settings = get_settings()
    if not settings.coding_agents_enabled:
        return _error("Coding agents are not enabled.", "disabled")

    mode_norm = (mode or "").strip().lower()
    if mode_norm in WRITE_MODES:
        return _error(WRITE_MODE_DEFERRED_MESSAGE, WRITE_MODE_DEFERRED_CODE)
    if mode_norm not in ALLOWED_MODES:
        return _error(
            "mode must be 'ask' or 'plan' (write/agent is deferred to P2).",
            "invalid_mode",
        )
    if not (task or "").strip():
        return _error("task must be non-empty.", "invalid_input")
    if not (workspace_id or "").strip():
        return _error("workspace_id is required.", "invalid_input")

    config = load_coding_agents_file(settings.coding_agents_config_path)
    if config is None:
        return _error(
            "Coding-agents config is missing or invalid.",
            "config_unavailable",
        )

    try:
        workspace = get_workspace(config, workspace_id.strip())
        chosen = resolve_runtime_choice(config, workspace, runtime)
        resolved_path = validate_workspace_path(workspace.path)
    except CodingAgentsConfigError as exc:
        return _error(str(exc), exc.code)

    workspace = workspace.model_copy(update={"path": resolved_path})
    spec = resolved_runtime_spec(
        config,
        chosen,
        default_binary=(
            settings.coding_cursor_binary if chosen == "cursor" else settings.coding_opencode_binary
        ),
        default_serve_url=settings.coding_opencode_serve_url,
    )

    async with _lock():
        if store.count_active_jobs() >= settings.coding_max_concurrent:
            return _error(
                "Too many coding jobs are already running or queued.",
                "concurrency_cap",
            )
        job_id = uuid.uuid4().hex
        job = store.insert_job(
            job_id,
            workspace.id,
            chosen,
            mode_norm,
            task.strip(),
        )

    audit.job_started(
        job_id,
        workspace_id=workspace.id,
        runtime=chosen,
        mode=mode_norm,
        has_user_consent=has_user_consent,
    )
    try:
        runner = get_runtime(
            chosen,
            binary=spec.binary,
            serve_url=spec.serve_url,
            timeout_s=float(settings.coding_job_timeout_s),
        )
    except ValueError as exc:
        store.update_job(job_id, status="failed", error=str(exc))
        return _error(str(exc), "unknown_runtime")

    _JOB_RUNTIMES[job_id] = runner
    spawned = asyncio.create_task(
        _execute_job(job_id, runner, workspace, settings.coding_job_timeout_s),
        name=f"coding-job-{job_id}",
    )
    set_job_task(job_id, spawned)
    spawned.add_done_callback(lambda _task: _JOB_TASKS.pop(job_id, None))
    return job


async def _execute_job(
    job_id: str,
    runner: CodingRuntime,
    workspace: WorkspaceSpec,
    timeout_s: int,
) -> None:
    """Run one job to a terminal store row, then maybe notify.

    Terminal writes (succeeded / failed / timed_out / cancelled) honor
    ``update_job``'s compare-and-swap boolean so a cancel that claimed first
    cannot be overwritten, and a lost CAS never audits or notifies.
    """
    row = store.get_job(job_id)
    if row is None:
        _JOB_RUNTIMES.pop(job_id, None)
        return
    job = CodingJob.model_validate(row)
    claimed = store.update_job(job_id, status="running", expected_statuses=("queued",))
    if not claimed:
        _JOB_RUNTIMES.pop(job_id, None)
        return
    try:
        artifact, events = await asyncio.wait_for(
            runner.run(job, workspace),
            timeout=timeout_s,
        )
    except TimeoutError as exc:
        await _safe_abort(runner)
        if not store.update_job(
            job_id,
            status="timed_out",
            error=str(exc).strip() or f"Job exceeded {timeout_s}s",
            expected_statuses=("queued", "running"),
        ):
            return
        store.append_event(job_id, "timed_out")
        audit.job_timed_out(job_id, runtime=job.runtime)
        _notify_job_completion(job_id)
        return
    except asyncio.CancelledError:
        await _safe_abort(runner)
        if store.update_job(
            job_id,
            status="cancelled",
            expected_statuses=("queued", "running"),
        ):
            store.append_event(job_id, "cancelled")
            audit.job_cancelled(job_id, runtime=job.runtime)
        raise
    except CodingRuntimeError as exc:
        _fail_job(job_id, job, str(exc), events=exc.events)
        return
    except Exception as exc:
        logger.exception("coding job %s crashed", job_id)
        _fail_job(job_id, job, str(exc))
        return
    finally:
        _JOB_RUNTIMES.pop(job_id, None)

    if not store.update_job(
        job_id,
        status="succeeded",
        artifact=artifact,
        events=events,
        expected_statuses=("queued", "running"),
    ):
        return
    audit.job_completed(job_id, runtime=job.runtime, workspace_id=job.workspace_id)
    _notify_job_completion(job_id)


def _fail_job(
    job_id: str,
    job: CodingJob,
    error: str,
    *,
    events: list[str] | None = None,
) -> None:
    if not store.update_job(
        job_id,
        status="failed",
        error=error,
        events=events,
        expected_statuses=("queued", "running"),
    ):
        return
    audit.job_failed(job_id, error=error, runtime=job.runtime)
    _notify_job_completion(job_id)


def _notify_job_completion(job_id: str) -> None:
    """Persist a principal-facing /today card. Never DM. Skip cancelled.

    Re-reads the store so a lost compare-and-swap (cancel won the race)
    cannot notify for a status we did not actually persist. Failures here
    must not change the job row.
    """
    try:
        _emit_job_completion_alert(job_id)
    except Exception:
        logger.exception("coding job %s completion notify failed", job_id)


def _emit_job_completion_alert(job_id: str) -> None:
    from openexecutive.alerts.models import AlertSeverity
    from openexecutive.alerts.store import insert_alert
    from openexecutive.people.store import find_principal_person

    row = store.get_job(job_id)
    if row is None:
        return
    status = str(row.get("status") or "")
    if status not in _COMPLETION_NOTIFY_STATUSES:
        return

    workspace_id = str(row.get("workspace_id") or "")
    headline = f"{_COMPLETION_HEADLINES[status]} ({workspace_id})"[:_HEADLINE_MAX]
    if status == "succeeded":
        detail = str(row.get("artifact") or "").strip() or "(empty output)"
        severity = AlertSeverity.MEDIUM
        suggested_action = "A coding analysis job finished. Review the result when you can."
    else:
        detail = _PRINCIPAL_ERROR_DETAIL.get(status, _PRINCIPAL_ERROR_DETAIL["failed"])
        severity = AlertSeverity.HIGH
        suggested_action = "A coding analysis job did not finish successfully."
    if len(detail) > _ALERT_BODY_MAX:
        detail = detail[: _ALERT_BODY_MAX - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX

    body = (
        f"**Job:** `{job_id}`\n**Workspace:** `{workspace_id}`\n**Status:** `{status}`\n\n{detail}"
    )

    principal_id: int | None = None
    try:
        principal = find_principal_person()
        principal_id = principal.id if principal else None
    except Exception:
        logger.exception("coding job completion notify: principal lookup failed")

    insert_alert(
        source="artifact",
        external_id=job_id,
        severity=severity.value,
        headline=headline,
        body=body,
        suggested_action=suggested_action,
        topic_tags=["artifact"],
        routed_to_person_id=principal_id,
    )


async def _safe_abort(runner: CodingRuntime) -> None:
    try:
        await runner.abort()
    except Exception:
        logger.debug("coding runtime abort failed", exc_info=True)


async def get_job(job_id: str) -> dict[str, Any]:
    if not job_id:
        return _error("job_id is required.", "invalid_input")
    row = store.get_job(job_id)
    if row is None:
        return _error("Unknown job.", "unknown_job")
    return _truncate_for_tool(row)


async def list_jobs(
    *,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    return [_truncate_for_tool(row) for row in store.list_jobs(status=status, limit=limit)]


async def cancel_job(job_id: str) -> dict[str, Any]:
    """Cancel a queued or running job. Does not create a /today card.

    Compare-and-swap to ``cancelled`` is the claim; abort only after a
    successful write so a kill-as-failure cannot persist ``failed`` and
    notify. A lost CAS returns ``not_running`` without extra events/audit.
    """
    if not job_id:
        return _error("job_id is required.", "invalid_input")
    row = store.get_job(job_id)
    if row is None:
        return _error("Unknown job.", "unknown_job")
    claimed = store.update_job(
        job_id,
        status="cancelled",
        expected_statuses=("queued", "running"),
    )
    if not claimed:
        updated = store.get_job(job_id) or row
        return _error(
            f"Job is already {updated['status']}.",
            "not_running",
            job=_truncate_for_tool(updated),
        )
    spawned = _JOB_TASKS.get(job_id)
    if spawned is not None and not spawned.done():
        spawned.cancel()
    runner = _JOB_RUNTIMES.get(job_id)
    if runner is not None:
        await _safe_abort(runner)
    store.append_event(job_id, "cancelled")
    audit.job_cancelled(job_id, runtime=row.get("runtime"))
    return _truncate_for_tool(store.get_job(job_id) or row)
