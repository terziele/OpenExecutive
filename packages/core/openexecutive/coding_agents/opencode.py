"""OpenCode runner: HTTP ``serve`` when configured, otherwise ``opencode run``."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from openexecutive.coding_agents.models import CodingJob, WorkspaceSpec
from openexecutive.coding_agents.runtimes import (
    OPENCODE_ENV_KEYS,
    RUNTIME_BUILDERS,
    CodingRuntimeError,
    assert_safe_argv,
    isolated_subprocess_env,
    summarize_runtime_output,
)

logger = logging.getLogger(__name__)


class _HttpWorkspaceBindError(Exception):
    """Serve could not bind the allowlisted workspace; caller should use CLI."""


def _directory_http_kwargs(directory: str) -> dict[str, Any]:
    """Serve routes workspaces via query and header, not only JSON body."""
    return {
        "params": {"directory": directory},
        "headers": {"x-opencode-directory": directory},
    }


def _workspace_directory(workspace: WorkspaceSpec) -> str:
    return str(Path(workspace.path))


def _same_directory(left: str, right: str) -> bool:
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return str(Path(left)) == str(Path(right))


def _reported_session_directory(payload: dict[str, Any]) -> str | None:
    raw = payload.get("directory")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    location = payload.get("location")
    if isinstance(location, dict):
        nested = location.get("directory")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return None


# Ask and plan both use the read-only plan agent. The OpenCode default is a
# write-capable build agent; we must not fall through to it on the P1 path.
_READONLY_AGENT = "plan"


def build_opencode_cli_argv(binary: str, workspace: Path, mode: str, task: str) -> list[str]:
    """CLI fallback. Never includes ``--auto`` / ``--force`` / ``--yolo``."""
    argv = [binary, "run", "--dir", str(workspace), "--format", "json"]
    if mode in {"ask", "plan"}:
        argv.extend(["--agent", _READONLY_AGENT])
    argv.append(task)
    assert_safe_argv(argv)
    return argv


def safe_url_for_log(url: str) -> str:
    """Strip userinfo so a basic-auth password never appears in logs."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


class OpenCodeRuntime:
    def __init__(self, binary: str, serve_url: str = "", timeout_s: float = 600.0) -> None:
        self.binary = binary
        self.serve_url = serve_url.rstrip("/")
        self.timeout_s = timeout_s
        self._proc: asyncio.subprocess.Process | None = None
        self._client: httpx.AsyncClient | None = None

    async def abort(self) -> None:
        proc = self._proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(TimeoutError, ProcessLookupError):
                await asyncio.wait_for(proc.wait(), timeout=2)
        client = self._client
        self._client = None
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                logger.debug("opencode http client close failed", exc_info=True)

    async def run(self, job: CodingJob, workspace: WorkspaceSpec) -> tuple[str, list[str]]:
        if self.serve_url:
            try:
                return await self._run_http(job, workspace)
            except _HttpWorkspaceBindError:
                logger.warning(
                    "OpenCode serve could not bind workspace %s; falling back to CLI",
                    workspace.id,
                )
                return await self._run_cli(job, workspace)
        return await self._run_cli(job, workspace)

    async def _run_cli(self, job: CodingJob, workspace: WorkspaceSpec) -> tuple[str, list[str]]:
        path = Path(workspace.path)
        argv = build_opencode_cli_argv(self.binary, path, job.mode, job.task)
        env = isolated_subprocess_env(OPENCODE_ENV_KEYS)
        events = [f"opencode_cli argv_len={len(argv)} mode={job.mode}"]
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
        artifact = summarize_runtime_output(stdout) or "(empty runtime output)"
        return artifact, events

    async def _run_http(self, job: CodingJob, workspace: WorkspaceSpec) -> tuple[str, list[str]]:
        events = [f"opencode_http url={safe_url_for_log(self.serve_url)}"]
        password = isolated_subprocess_env(OPENCODE_ENV_KEYS).get("OPENCODE_SERVER_PASSWORD")
        auth = httpx.BasicAuth(username="", password=password) if password else None
        timeout = httpx.Timeout(self.timeout_s)
        try:
            self._client = httpx.AsyncClient(timeout=timeout, auth=auth)
            try:
                session_id = await self._create_session(job, workspace)
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, _HttpWorkspaceBindError, CodingRuntimeError) as exc:
                raise _HttpWorkspaceBindError(
                    f"OpenCode serve could not bind workspace: {exc.__class__.__name__}"
                ) from exc
            events.append(f"session={session_id}")
            artifact = await self._post_message(session_id, job, workspace)
        except asyncio.CancelledError:
            await self.abort()
            raise
        except _HttpWorkspaceBindError:
            raise
        except httpx.HTTPError as exc:
            # httpx may stringify the URL; never include auth material.
            raise CodingRuntimeError(
                f"OpenCode serve request failed: {exc.__class__.__name__}",
                events=events,
            ) from exc
        finally:
            await self.abort()
        return artifact or "(empty runtime output)", events

    async def _create_session(self, job: CodingJob, workspace: WorkspaceSpec) -> str:
        assert self._client is not None
        directory = _workspace_directory(workspace)
        response = await self._client.post(
            f"{self.serve_url}/session",
            json={"title": f"oe-{job.job_id}", "directory": directory},
            **_directory_http_kwargs(directory),
        )
        response.raise_for_status()
        payload = _json_object(response)
        session_id = payload.get("id") or payload.get("sessionID") or payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise _HttpWorkspaceBindError("OpenCode serve did not return a session id")
        reported = _reported_session_directory(payload)
        if reported is not None and not _same_directory(reported, directory):
            raise _HttpWorkspaceBindError(
                "OpenCode serve bound a different directory than the allowlist"
            )
        return session_id

    async def _post_message(self, session_id: str, job: CodingJob, workspace: WorkspaceSpec) -> str:
        assert self._client is not None
        directory = _workspace_directory(workspace)
        body: dict[str, Any] = {
            "parts": [{"type": "text", "text": job.task}],
            "agent": _READONLY_AGENT,
        }
        response = await self._client.post(
            f"{self.serve_url}/session/{session_id}/message",
            json=body,
            **_directory_http_kwargs(directory),
        )
        response.raise_for_status()
        payload = _json_object(response)
        return summarize_runtime_output(json.dumps(payload, ensure_ascii=False))


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise CodingRuntimeError("OpenCode serve returned non-JSON") from exc
    if not isinstance(payload, dict):
        raise CodingRuntimeError("OpenCode serve returned a non-object JSON body")
    return payload


def _build_opencode(
    *, binary: str, serve_url: str = "", timeout_s: float = 600.0
) -> OpenCodeRuntime:
    return OpenCodeRuntime(binary=binary, serve_url=serve_url, timeout_s=timeout_s)


RUNTIME_BUILDERS["opencode"] = _build_opencode
