"""Pydantic models for coding-agent jobs and the operator YAML allowlist."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RuntimeName = Literal["cursor", "opencode"]
JobMode = Literal["ask", "plan", "agent", "write"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "timed_out"]

ALLOWED_MODES: frozenset[str] = frozenset({"ask", "plan"})
WRITE_MODES: frozenset[str] = frozenset({"agent", "write"})
KNOWN_RUNTIMES: frozenset[str] = frozenset({"cursor", "opencode"})
ACTIVE_STATUSES: frozenset[str] = frozenset({"queued", "running"})

WRITE_MODE_DEFERRED_CODE = "write_mode_deferred"
WRITE_MODE_DEFERRED_MESSAGE = (
    "Write/agent mode is deferred to P2. P1 coding-agent jobs are ask/plan "
    "only: they may analyze an allowlisted repository but must not edit "
    "files. P2 will add a human authority gate and an isolated git worktree "
    "before any runtime can write."
)


class RuntimeSpec(BaseModel):
    """One runtime entry under ``runtimes:`` in the operator YAML."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    binary: str = ""
    serve_url: str = ""


class WorkspaceSpec(BaseModel):
    """One allowlisted workspace. The model supplies ``id``, never a path."""

    model_config = ConfigDict(extra="ignore")

    id: str
    path: Path
    default_runtime: RuntimeName = "cursor"

    @field_validator("id")
    @classmethod
    def _workspace_id_is_not_a_path(cls, value: str) -> str:
        ident = value.strip()
        if not ident:
            raise ValueError("workspace id must be non-empty")
        if any(sep in ident for sep in ("/", "\\")) or ".." in ident:
            raise ValueError("workspace id must be a slug, not a filesystem path")
        return ident


class CodingAgentsFile(BaseModel):
    """Root document for ``company/coding_agents.yaml``."""

    model_config = ConfigDict(extra="ignore")

    runtimes: dict[str, RuntimeSpec] = Field(default_factory=dict)
    workspaces: list[WorkspaceSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_workspace_ids(self) -> CodingAgentsFile:
        ids = [workspace.id for workspace in self.workspaces]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate workspace id in coding_agents.yaml")
        return self

    def workspace_by_id(self, workspace_id: str) -> WorkspaceSpec | None:
        for workspace in self.workspaces:
            if workspace.id == workspace_id:
                return workspace
        return None

    def runtime_spec(self, name: str) -> RuntimeSpec | None:
        return self.runtimes.get(name)


class CodingJob(BaseModel):
    """In-memory / API shape of one ``coding_jobs`` row."""

    model_config = ConfigDict(extra="ignore")

    job_id: str
    workspace_id: str
    runtime: RuntimeName
    mode: JobMode
    task: str
    status: JobStatus
    artifact: str | None = None
    error: str | None = None
    events: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str

    def to_public_dict(self) -> dict[str, Any]:
        return self.model_dump()
