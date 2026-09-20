"""Thin audit wrappers. Never raise — a failed audit must not fail a job."""

from __future__ import annotations

import logging
from typing import Any

from openexecutive.audit import log_event

logger = logging.getLogger(__name__)


def _emit(event_type: str, summary: str, details: dict[str, Any] | None = None) -> None:
    try:
        log_event(
            event_type,
            summary,
            actor="coding_agents",
            details=details,
        )
    except Exception:
        logger.warning("coding_agents audit failed event_type=%s", event_type, exc_info=True)


def job_started(
    job_id: str,
    *,
    workspace_id: str,
    runtime: str,
    mode: str,
    has_user_consent: bool,
) -> None:
    _emit(
        "coding_job_started",
        f"coding job {job_id} started",
        {
            "job_id": job_id,
            "workspace_id": workspace_id,
            "runtime": runtime,
            "mode": mode,
            "has_user_consent": has_user_consent,
        },
    )


def job_completed(job_id: str, *, runtime: str, workspace_id: str) -> None:
    _emit(
        "coding_job_completed",
        f"coding job {job_id} completed",
        {"job_id": job_id, "runtime": runtime, "workspace_id": workspace_id},
    )


def job_cancelled(job_id: str, *, runtime: str | None = None) -> None:
    _emit(
        "coding_job_cancelled",
        f"coding job {job_id} cancelled",
        {"job_id": job_id, "runtime": runtime},
    )


def job_failed(job_id: str, *, error: str, runtime: str | None = None) -> None:
    _emit(
        "coding_job_failed",
        f"coding job {job_id} failed",
        {"job_id": job_id, "runtime": runtime, "error": error[:500]},
    )


def job_timed_out(job_id: str, *, runtime: str | None = None) -> None:
    _emit(
        "coding_job_timed_out",
        f"coding job {job_id} timed out",
        {"job_id": job_id, "runtime": runtime},
    )
