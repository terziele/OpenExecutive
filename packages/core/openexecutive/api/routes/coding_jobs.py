"""HTTP inspect/cancel surface for coding-agent jobs.

Start stays on the Executive chat tools (workspace_id allowlist + ask/plan
only). Operators list, fetch, and cancel jobs here. Auth is the shared-secret
middleware on the app — no extra gate.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from openexecutive.coding_agents.models import CodingJob, JobStatus
from openexecutive.coding_agents.service import cancel_job, get_job, list_jobs

router = APIRouter()


class CodingJobListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobs: list[CodingJob] = Field(default_factory=list)


def _unknown(payload: dict[str, Any]) -> bool:
    return payload.get("code") == "unknown_job"


@router.get("/coding-jobs", response_model=CodingJobListResponse)
async def list_coding_jobs(
    status: JobStatus | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> CodingJobListResponse:
    rows = await list_jobs(status=status, limit=limit)
    return CodingJobListResponse(jobs=[CodingJob.model_validate(row) for row in rows])


@router.get("/coding-jobs/{job_id}")
async def get_coding_job(job_id: str) -> dict[str, Any]:
    result = await get_job(job_id)
    if _unknown(result):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return result


@router.post("/coding-jobs/{job_id}/cancel")
async def cancel_coding_job(job_id: str) -> dict[str, Any]:
    result = await cancel_job(job_id)
    if _unknown(result):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return result
