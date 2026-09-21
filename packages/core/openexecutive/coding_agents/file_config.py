"""Load and validate the operator-controlled coding-agents YAML allowlist."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from openexecutive.coding_agents.models import (
    CodingAgentsFile,
    RuntimeName,
    RuntimeSpec,
    WorkspaceSpec,
)

logger = logging.getLogger(__name__)

_MAX_YAML_BYTES = 256 * 1024


class CodingAgentsConfigError(ValueError):
    """Allowlist lookup, path, or runtime choice failed.

    Raised by ``get_workspace``, ``validate_workspace_path``, and
    ``resolve_runtime_choice``. ``load_coding_agents_file`` never raises:
    missing or invalid YAML returns ``None`` and logs a warning.
    """

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def load_coding_agents_file(path: Path) -> CodingAgentsFile | None:
    """Parse ``path``. Missing or invalid YAML → ``None`` + warning, never raise."""
    try:
        if not path.is_file():
            logger.warning("coding_agents: config is not a file: %s", path)
            return None
        size = path.stat().st_size
        if size <= 0:
            logger.warning("coding_agents: config file is empty: %s", path)
            return None
        if size > _MAX_YAML_BYTES:
            logger.warning("coding_agents: config file is too large: %s", path)
            return None
        raw = path.read_text(encoding="utf-8")
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        logger.warning("coding_agents: could not read %s: %s", path, exc)
        return None

    if not raw.strip():
        logger.warning("coding_agents: config file is empty: %s", path)
        return None

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        logger.warning("coding_agents: invalid YAML at %s: %s", path, exc)
        return None
    except Exception as exc:
        logger.warning("coding_agents: failed to parse %s: %s", path, exc)
        return None

    if data is None:
        logger.warning("coding_agents: YAML at %s is empty; treating as missing", path)
        return None
    if not isinstance(data, dict):
        logger.warning("coding_agents: YAML at %s is not a mapping", path)
        return None

    try:
        return CodingAgentsFile.model_validate(data)
    except ValidationError as exc:
        logger.warning("coding_agents: invalid config at %s: %s", path, exc)
        return None


def get_workspace(config: CodingAgentsFile, workspace_id: str) -> WorkspaceSpec:
    """Look up an allowlisted workspace. Unknown id → error (never a path guess)."""
    if not workspace_id or any(sep in workspace_id for sep in ("/", "\\")):
        raise CodingAgentsConfigError(
            "workspace_id must be an allowlisted slug, not a filesystem path",
            code="unknown_workspace",
        )
    workspace = config.workspace_by_id(workspace_id)
    if workspace is None:
        raise CodingAgentsConfigError(
            f"Unknown workspace_id {workspace_id!r}",
            code="unknown_workspace",
        )
    return workspace


def validate_workspace_path(configured: Path) -> Path:
    """Resolve the allowlisted path and require it to be an existing directory.

    Rejects ``..`` components so a YAML path cannot walk out via relative
    segments. ``expanduser().resolve()`` follows a symlink *at* the
    configured path (the operator chose that entry). This does not jail
    in-workspace symlinks; the model never supplies extra path segments.
    """
    raw = Path(configured)
    if any(part == ".." for part in raw.parts):
        raise CodingAgentsConfigError(
            "workspace path must not contain '..'",
            code="invalid_workspace_path",
        )
    try:
        resolved = raw.expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise CodingAgentsConfigError(
            f"workspace path could not be resolved: {exc}",
            code="invalid_workspace_path",
        ) from exc
    if not resolved.is_dir():
        raise CodingAgentsConfigError(
            "workspace path must exist and be a directory",
            code="invalid_workspace_path",
        )
    return resolved


def resolve_runtime_choice(
    config: CodingAgentsFile,
    workspace: WorkspaceSpec,
    runtime: str | None,
) -> RuntimeName:
    """Pick cursor/opencode from the request or the workspace default."""
    chosen = runtime or workspace.default_runtime
    if chosen not in {"cursor", "opencode"}:
        raise CodingAgentsConfigError(
            f"Unknown runtime {chosen!r}",
            code="unknown_runtime",
        )
    spec = config.runtime_spec(chosen)
    if spec is None or not spec.enabled:
        raise CodingAgentsConfigError(
            f"Runtime {chosen!r} is disabled",
            code="runtime_disabled",
        )
    return chosen  # type: ignore[return-value]


def resolved_runtime_spec(
    config: CodingAgentsFile,
    name: RuntimeName,
    *,
    default_binary: str,
    default_serve_url: str = "",
) -> RuntimeSpec:
    """YAML values win when set; empty strings fall through to Settings defaults."""
    spec = config.runtime_spec(name) or RuntimeSpec()
    binary = spec.binary.strip() or default_binary
    serve_url = spec.serve_url.strip() or default_serve_url
    return RuntimeSpec(enabled=spec.enabled, binary=binary, serve_url=serve_url)
