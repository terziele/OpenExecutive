"""Cursor CLI (``agent`` / ``cursor-agent``) ask/plan runner."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from openexecutive.coding_agents.models import CodingJob, WorkspaceSpec
from openexecutive.coding_agents.runtimes import (
    CURSOR_ENV_KEYS,
    RUNTIME_BUILDERS,
    CodingRuntimeError,
    assert_safe_argv,
    isolated_subprocess_env,
    summarize_runtime_output,
)


def build_cursor_argv(binary: str, workspace: Path, mode: str, task: str) -> list[str]:
    """Print-mode argv. Never includes ``--force`` / ``--yolo`` / ``--api-key``."""
    argv = [
        binary,
        "-p",
        "--trust",
        "--workspace",
        str(workspace),
        "--sandbox",
        "enabled",
        "--mode",
        mode,
        "--output-format",
        "json",
        task,
    ]
    assert_safe_argv(argv)
    return argv


class CursorCliRuntime:
    def __init__(self, binary: str, serve_url: str = "", timeout_s: float = 600.0) -> None:
        self.binary = binary
        self.timeout_s = timeout_s
        self._proc: asyncio.subprocess.Process | None = None

    async def abort(self) -> None:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(TimeoutError, ProcessLookupError):
            await asyncio.wait_for(proc.wait(), timeout=2)

    async def run(self, job: CodingJob, workspace: WorkspaceSpec) -> tuple[str, list[str]]:
        path = Path(workspace.path)
        argv = build_cursor_argv(self.binary, path, job.mode, job.task)
        env = isolated_subprocess_env(CURSOR_ENV_KEYS)
        events = [f"cursor_cli argv_len={len(argv)} mode={job.mode}"]
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(path),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise CodingRuntimeError(
                f"failed to start coding runtime: {exc}", events=events
            ) from exc

        try:
            stdout_b, stderr_b = await self._proc.communicate()
        except asyncio.CancelledError:
            await self.abort()
            raise
        finally:
            returncode = self._proc.returncode if self._proc is not None else None

        stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
        stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
        events.append(f"exit_code={returncode}")
        if returncode not in (0, None):
            snippet = (stderr or stdout)[-500:]
            events.append(f"stderr={snippet[:200]}")
            raise CodingRuntimeError(
                f"coding runtime exited {returncode}: {snippet[:300]}",
                events=events,
            )
        artifact = summarize_runtime_output(stdout)
        if not artifact:
            artifact = "(empty runtime output)"
        return artifact, events


def _build_cursor(
    *, binary: str, serve_url: str = "", timeout_s: float = 600.0
) -> CursorCliRuntime:
    return CursorCliRuntime(binary=binary, serve_url=serve_url, timeout_s=timeout_s)


RUNTIME_BUILDERS["cursor"] = _build_cursor
