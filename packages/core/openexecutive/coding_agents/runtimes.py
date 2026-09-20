"""Coding-runtime protocol, isolated subprocess env, and dispatch."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Protocol

from openexecutive.coding_agents.models import CodingJob, RuntimeName, WorkspaceSpec

# Never forwarded from the OE process. Tests assert Slack/Google/API secrets
# present in os.environ do not appear in the child env.
_BASE_ENV_KEYS = ("PATH", "HOME", "LANG", "TERM")
CURSOR_ENV_KEYS = (*_BASE_ENV_KEYS, "CURSOR_API_KEY")
OPENCODE_ENV_KEYS = (*_BASE_ENV_KEYS, "OPENCODE_SERVER_PASSWORD")

FORBIDDEN_ARGV_FLAGS = frozenset(
    {
        "--force",
        "--yolo",
        "--api-key",
        "--dangerously-skip-permissions",
        "--auto",
    }
)

STDOUT_KEEP_CHARS = 8000


class CodingRuntimeError(Exception):
    """Runtime failed. ``events`` are short status strings for the job row."""

    def __init__(self, message: str, events: list[str] | None = None) -> None:
        super().__init__(message)
        self.events = events or []


class CodingRuntime(Protocol):
    """Ask/plan backend. Implementations must not inherit the full os.environ."""

    async def run(self, job: CodingJob, workspace: WorkspaceSpec) -> tuple[str, list[str]]:
        """Execute the job. Returns ``(artifact, events)`` or raises."""

    async def abort(self) -> None:
        """Kill a running subprocess / close an HTTP client. Idempotent."""


RuntimeFactory = Callable[..., CodingRuntime]

# Filled by cursor_cli / opencode at import so this module never imports them
# (those modules import isolated_subprocess_env from here).
RUNTIME_BUILDERS: dict[str, RuntimeFactory] = {}


def isolated_subprocess_env(extra_keys: tuple[str, ...] = ()) -> dict[str, str]:
    """Copy a tight env allowlist. Do not inherit ``os.environ`` wholesale."""
    keys = (*_BASE_ENV_KEYS, *extra_keys)
    out: dict[str, str] = {}
    for key in keys:
        value = os.environ.get(key)
        if value is not None:
            out[key] = value
    return out


def assert_safe_argv(argv: list[str]) -> None:
    """Refuse to spawn if a forbidden flag slipped onto the command line."""
    forbidden = [item for item in argv if item in FORBIDDEN_ARGV_FLAGS]
    if forbidden:
        raise CodingRuntimeError(
            f"refusing to spawn coding runtime with forbidden flags: {forbidden}"
        )
    # Also catch `--api-key=...` / `--force=true` forms.
    for item in argv:
        name = item.split("=", 1)[0]
        if name in FORBIDDEN_ARGV_FLAGS:
            raise CodingRuntimeError(
                f"refusing to spawn coding runtime with forbidden flag: {item}"
            )


def summarize_runtime_output(stdout: str, *, max_chars: int = STDOUT_KEEP_CHARS) -> str:
    """Prefer a short JSON summary; otherwise the last N chars of stdout."""
    text = stdout.strip()
    if not text:
        return ""
    parsed: object | None = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{") or line.startswith("["):
                try:
                    parsed = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
    if parsed is None:
        return text[-max_chars:] if len(text) > max_chars else text
    summary = _extract_summary(parsed)
    return summary[:max_chars]


def _extract_summary(parsed: object) -> str:
    if isinstance(parsed, str):
        return parsed
    if isinstance(parsed, dict):
        for key in ("result", "response", "text", "message", "summary"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, dict):
                nested = _extract_summary(value)
                if nested:
                    return nested
        parts = parsed.get("parts")
        if isinstance(parts, list):
            texts = [
                part["text"]
                for part in parts
                if isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
            ]
            if texts:
                return "\n".join(texts)
        return json.dumps(parsed, ensure_ascii=False)
    return json.dumps(parsed, ensure_ascii=False)


def get_runtime(
    name: RuntimeName,
    *,
    binary: str,
    serve_url: str = "",
    timeout_s: float = 600.0,
) -> CodingRuntime:
    """Dispatch to a registered runtime implementation."""
    factory = RUNTIME_BUILDERS.get(name)
    if factory is None:
        raise ValueError(f"Unknown coding runtime {name!r}")
    return factory(binary=binary, serve_url=serve_url, timeout_s=timeout_s)
