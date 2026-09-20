"""Cursor/OpenCode argv, env isolation, and mocked subprocess/httpx."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from openexecutive.coding_agents.cursor_cli import CursorCliRuntime, build_cursor_argv
from openexecutive.coding_agents.models import CodingJob, WorkspaceSpec
from openexecutive.coding_agents.opencode import (
    OpenCodeRuntime,
    build_opencode_cli_argv,
    safe_url_for_log,
)
from openexecutive.coding_agents.runtimes import (
    FORBIDDEN_ARGV_FLAGS,
    CodingRuntimeError,
    assert_safe_argv,
    isolated_subprocess_env,
    summarize_runtime_output,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = tmp_path / "coding_jobs.db"
    monkeypatch.setattr("openexecutive.memory.episodic.DB_PATH", db)
    monkeypatch.setattr("openexecutive.coding_agents.store.DB_PATH", db)
    monkeypatch.setattr("openexecutive.audit.log_event", lambda *a, **k: None)
    monkeypatch.setattr("openexecutive.coding_agents.audit.log_event", lambda *a, **k: None)


def _job(mode: str = "ask") -> CodingJob:
    return CodingJob(
        job_id="abc123",
        workspace_id="product",
        runtime="cursor",
        mode=mode,  # type: ignore[arg-type]
        task="explain src/main.py",
        status="running",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def test_cursor_argv_has_sandbox_and_no_force(tmp_path: Path) -> None:
    argv = build_cursor_argv("agent", tmp_path, "ask", "what does this do?")
    assert argv[0] == "agent"
    assert "-p" in argv
    assert "--trust" in argv
    assert "--workspace" in argv
    assert "--sandbox" in argv
    assert "enabled" in argv
    assert "--mode" in argv
    assert "ask" in argv
    assert "--output-format" in argv
    assert "json" in argv
    assert argv[-1] == "what does this do?"
    for flag in FORBIDDEN_ARGV_FLAGS:
        assert flag not in argv
    assert "--force" not in argv
    assert "--yolo" not in argv
    assert "--api-key" not in argv


def test_opencode_cli_argv_uses_readonly_plan_agent(tmp_path: Path) -> None:
    argv = build_opencode_cli_argv("opencode", tmp_path, "ask", "review the types")
    assert argv[:2] == ["opencode", "run"]
    assert "--dir" in argv
    assert "--format" in argv
    assert "json" in argv
    assert "--agent" in argv
    assert "plan" in argv
    assert "--auto" not in argv
    assert "--force" not in argv


def test_assert_safe_argv_rejects_force() -> None:
    with pytest.raises(CodingRuntimeError):
        assert_safe_argv(["agent", "--force", "task"])


def test_isolated_env_drops_slack_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-secret")
    monkeypatch.setenv("BACKEND_SHARED_SECRET", "super-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("CURSOR_API_KEY", "cursor-key")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = isolated_subprocess_env(("CURSOR_API_KEY",))
    assert env["CURSOR_API_KEY"] == "cursor-key"
    assert "SLACK_BOT_TOKEN" not in env
    assert "BACKEND_SHARED_SECRET" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "xoxb-secret" not in env.values()


@pytest.mark.asyncio
async def test_cursor_run_passes_isolated_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-secret")
    monkeypatch.setenv("CURSOR_API_KEY", "cursor-key")
    captured: dict[str, Any] = {}

    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b'{"result":"looks fine"}', b""))

    async def fake_exec(*args: str, **kwargs: Any) -> MagicMock:
        captured["argv"] = args
        captured["env"] = kwargs.get("env")
        captured["cwd"] = kwargs.get("cwd")
        return proc

    monkeypatch.setattr(
        "openexecutive.coding_agents.cursor_cli.asyncio.create_subprocess_exec",
        fake_exec,
    )
    workspace = WorkspaceSpec(id="product", path=tmp_path, default_runtime="cursor")
    artifact, events = await CursorCliRuntime("agent").run(_job(), workspace)
    assert artifact == "looks fine"
    assert captured["cwd"] == str(tmp_path)
    assert "--force" not in captured["argv"]
    assert "SLACK_BOT_TOKEN" not in captured["env"]
    assert captured["env"]["CURSOR_API_KEY"] == "cursor-key"
    assert any("exit_code=0" in item for item in events)


@pytest.mark.asyncio
async def test_cursor_nonzero_exit_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proc = MagicMock()
    proc.returncode = 2
    proc.communicate = AsyncMock(return_value=(b"", b"boom"))

    async def fake_exec(*_a: str, **_k: Any) -> MagicMock:
        return proc

    monkeypatch.setattr(
        "openexecutive.coding_agents.cursor_cli.asyncio.create_subprocess_exec",
        fake_exec,
    )
    workspace = WorkspaceSpec(id="product", path=tmp_path, default_runtime="cursor")
    with pytest.raises(CodingRuntimeError, match="exited 2"):
        await CursorCliRuntime("agent").run(_job(), workspace)


@pytest.mark.asyncio
async def test_opencode_http_posts_session_then_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    posts: list[tuple[str, dict[str, Any] | None, Any, Any]] = []

    class _Resp:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._payload

    class _Client:
        def __init__(self, *a: object, **kwargs: object) -> None:
            captured["auth"] = kwargs.get("auth")
            captured["timeout"] = kwargs.get("timeout")

        async def post(self, url: str, json: dict[str, Any] | None = None, **kwargs: Any) -> _Resp:
            posts.append((url, json, kwargs.get("params"), kwargs.get("headers")))
            if url.endswith("/session"):
                return _Resp({"id": "ses_1"})
            return _Resp({"parts": [{"type": "text", "text": "the plan"}]})

        async def aclose(self) -> None:
            return None

    captured: dict[str, Any] = {}
    monkeypatch.setattr("openexecutive.coding_agents.opencode.httpx.AsyncClient", _Client)
    workspace = WorkspaceSpec(id="product", path=tmp_path, default_runtime="opencode")
    job = _job("plan")
    job.runtime = "opencode"
    artifact, events = await OpenCodeRuntime(
        "opencode", serve_url="http://127.0.0.1:4096", timeout_s=30
    ).run(job, workspace)
    assert artifact == "the plan"
    assert posts[0][0] == "http://127.0.0.1:4096/session"
    assert posts[0][1] is not None
    assert posts[0][1]["directory"] == str(tmp_path)
    assert posts[0][1]["title"] == "oe-abc123"
    assert posts[0][2] == {"directory": str(tmp_path)}
    assert posts[0][3] == {"x-opencode-directory": str(tmp_path)}
    assert posts[1][0] == "http://127.0.0.1:4096/session/ses_1/message"
    assert posts[1][1] is not None
    assert set(posts[0][1]) == {"title", "directory", "agent"}
    assert posts[0][1]["agent"] == "plan"
    assert posts[1][1]["parts"][0]["text"] == "explain src/main.py"
    assert posts[1][1]["agent"] == "plan"
    assert posts[1][2] == {"directory": str(tmp_path)}
    assert posts[1][3] == {"x-opencode-directory": str(tmp_path)}
    assert any("session=ses_1" in item for item in events)
    assert captured["auth"] is None


@pytest.mark.asyncio
async def test_opencode_http_message_timeout_raises_timeout_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _SessionResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"id": "ses_1"}

    class _Client:
        def __init__(self, *a: object, **kwargs: object) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any] | None = None, **_k: Any) -> Any:
            if url.endswith("/session"):
                return _SessionResp()
            raise httpx.TimeoutException("timed out")

        async def aclose(self) -> None:
            return None

    called_cli = {"n": 0}

    async def fake_exec(*args: str, **kwargs: Any) -> MagicMock:
        called_cli["n"] += 1
        raise AssertionError("CLI must not run after an HTTP timeout")

    monkeypatch.setattr("openexecutive.coding_agents.opencode.httpx.AsyncClient", _Client)
    monkeypatch.setattr(
        "openexecutive.coding_agents.opencode.asyncio.create_subprocess_exec",
        fake_exec,
    )
    workspace = WorkspaceSpec(id="product", path=tmp_path, default_runtime="opencode")
    with pytest.raises(TimeoutError, match="HTTP request timed out"):
        await OpenCodeRuntime("opencode", serve_url="http://127.0.0.1:4096").run(
            _job(), workspace
        )
    assert called_cli["n"] == 0


@pytest.mark.asyncio
async def test_opencode_http_session_timeout_raises_timeout_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Client:
        def __init__(self, *a: object, **kwargs: object) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any] | None = None, **_k: Any) -> Any:
            raise httpx.TimeoutException("timed out")

        async def aclose(self) -> None:
            return None

    called_cli = {"n": 0}

    async def fake_exec(*args: str, **kwargs: Any) -> MagicMock:
        called_cli["n"] += 1
        raise AssertionError("CLI must not run after a session timeout")

    monkeypatch.setattr("openexecutive.coding_agents.opencode.httpx.AsyncClient", _Client)
    monkeypatch.setattr(
        "openexecutive.coding_agents.opencode.asyncio.create_subprocess_exec",
        fake_exec,
    )
    workspace = WorkspaceSpec(id="product", path=tmp_path, default_runtime="opencode")
    with pytest.raises(TimeoutError, match="HTTP request timed out"):
        await OpenCodeRuntime("opencode", serve_url="http://127.0.0.1:4096").run(
            _job(), workspace
        )
    assert called_cli["n"] == 0


@pytest.mark.asyncio
async def test_opencode_http_session_status_error_falls_back_to_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Resp:
        def raise_for_status(self) -> None:
            request = httpx.Request("POST", "http://127.0.0.1:4096/session")
            response = httpx.Response(400, request=request)
            raise httpx.HTTPStatusError(
                "Client error '400 Bad Request' for url 'http://127.0.0.1:4096/session'",
                request=request,
                response=response,
            )

        def json(self) -> dict[str, Any]:
            return {}

    class _Client:
        def __init__(self, *a: object, **kwargs: object) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any] | None = None, **_k: Any) -> _Resp:
            return _Resp()

        async def aclose(self) -> None:
            return None

    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b'{"result":"from-cli"}', b""))
    captured: dict[str, Any] = {}

    async def fake_exec(*args: str, **kwargs: Any) -> MagicMock:
        captured["argv"] = args
        return proc

    monkeypatch.setattr("openexecutive.coding_agents.opencode.httpx.AsyncClient", _Client)
    monkeypatch.setattr(
        "openexecutive.coding_agents.opencode.asyncio.create_subprocess_exec",
        fake_exec,
    )
    workspace = WorkspaceSpec(id="product", path=tmp_path, default_runtime="opencode")
    artifact, _events = await OpenCodeRuntime("opencode", serve_url="http://127.0.0.1:4096").run(
        _job("plan"), workspace
    )
    assert artifact == "from-cli"
    assert "--agent" in captured["argv"]
    assert "plan" in captured["argv"]
    assert "--dir" in captured["argv"]


@pytest.mark.asyncio
async def test_opencode_http_session_connect_timeout_falls_back_to_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Client:
        def __init__(self, *a: object, **kwargs: object) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any] | None = None, **_k: Any) -> Any:
            raise httpx.ConnectTimeout(
                "connect timed out",
                request=httpx.Request("POST", url),
            )

        async def aclose(self) -> None:
            return None

    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b'{"result":"from-cli"}', b""))
    captured: dict[str, Any] = {}

    async def fake_exec(*args: str, **kwargs: Any) -> MagicMock:
        captured["argv"] = args
        return proc

    monkeypatch.setattr("openexecutive.coding_agents.opencode.httpx.AsyncClient", _Client)
    monkeypatch.setattr(
        "openexecutive.coding_agents.opencode.asyncio.create_subprocess_exec",
        fake_exec,
    )
    workspace = WorkspaceSpec(id="product", path=tmp_path, default_runtime="opencode")
    artifact, _events = await OpenCodeRuntime("opencode", serve_url="http://127.0.0.1:4096").run(
        _job("plan"), workspace
    )
    assert artifact == "from-cli"
    assert "--agent" in captured["argv"]
    assert "plan" in captured["argv"]


@pytest.mark.asyncio
async def test_opencode_http_uses_basic_auth_when_password_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "s3cret")
    captured: dict[str, Any] = {}

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"id": "ses_1"} if not captured.get("second") else {"text": "ok"}

    class _Client:
        def __init__(self, *a: object, **kwargs: object) -> None:
            captured["auth"] = kwargs.get("auth")

        async def post(self, url: str, json: dict[str, Any] | None = None, **_k: Any) -> _Resp:
            if "/message" in url:
                captured["second"] = True
            return _Resp()

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("openexecutive.coding_agents.opencode.httpx.AsyncClient", _Client)
    workspace = WorkspaceSpec(id="product", path=tmp_path, default_runtime="opencode")
    await OpenCodeRuntime("opencode", serve_url="http://127.0.0.1:4096").run(_job(), workspace)
    auth = captured["auth"]
    assert auth is not None
    # httpx.BasicAuth stores the password; it must not appear in our log helper.
    assert "s3cret" not in safe_url_for_log("http://user:s3cret@127.0.0.1:4096/session")


@pytest.mark.asyncio
async def test_opencode_http_bind_failure_falls_back_to_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Client:
        def __init__(self, *a: object, **kwargs: object) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any] | None = None, **_k: Any) -> Any:
            raise httpx.ConnectError(
                "connection refused",
                request=httpx.Request("POST", url),
            )

        async def aclose(self) -> None:
            return None

    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b'{"result":"from-cli"}', b""))
    captured: dict[str, Any] = {}

    async def fake_exec(*args: str, **kwargs: Any) -> MagicMock:
        captured["argv"] = args
        captured["cwd"] = kwargs.get("cwd")
        return proc

    monkeypatch.setattr("openexecutive.coding_agents.opencode.httpx.AsyncClient", _Client)
    monkeypatch.setattr(
        "openexecutive.coding_agents.opencode.asyncio.create_subprocess_exec",
        fake_exec,
    )
    workspace = WorkspaceSpec(id="product", path=tmp_path, default_runtime="opencode")
    job = _job("plan")
    job.runtime = "opencode"
    artifact, _events = await OpenCodeRuntime("opencode", serve_url="http://127.0.0.1:4096").run(
        job, workspace
    )
    assert artifact == "from-cli"
    assert "--dir" in captured["argv"]
    assert str(tmp_path) in captured["argv"]
    assert captured["cwd"] == str(tmp_path)


@pytest.mark.asyncio
async def test_opencode_http_directory_mismatch_falls_back_to_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"id": "ses_wrong", "directory": "/not/the/allowlist"}

    class _Client:
        def __init__(self, *a: object, **kwargs: object) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any] | None = None, **_k: Any) -> _Resp:
            return _Resp()

        async def aclose(self) -> None:
            return None

    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b'{"result":"cli-bound"}', b""))
    captured: dict[str, Any] = {}

    async def fake_exec(*args: str, **kwargs: Any) -> MagicMock:
        captured["argv"] = args
        return proc

    monkeypatch.setattr("openexecutive.coding_agents.opencode.httpx.AsyncClient", _Client)
    monkeypatch.setattr(
        "openexecutive.coding_agents.opencode.asyncio.create_subprocess_exec",
        fake_exec,
    )
    workspace = WorkspaceSpec(id="product", path=tmp_path, default_runtime="opencode")
    artifact, _events = await OpenCodeRuntime("opencode", serve_url="http://127.0.0.1:4096").run(
        _job(), workspace
    )
    assert artifact == "cli-bound"
    assert "--dir" in captured["argv"]
    assert str(tmp_path) in captured["argv"]


@pytest.mark.asyncio
async def test_opencode_http_message_error_does_not_fall_back_to_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _SessionResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"id": "ses_1"}

    class _Client:
        def __init__(self, *a: object, **kwargs: object) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any] | None = None, **_k: Any) -> Any:
            if url.endswith("/session"):
                return _SessionResp()
            raise httpx.ConnectError(
                "message failed",
                request=httpx.Request("POST", url),
            )

        async def aclose(self) -> None:
            return None

    called_cli = {"n": 0}

    async def fake_exec(*args: str, **kwargs: Any) -> MagicMock:
        called_cli["n"] += 1
        raise AssertionError("CLI must not run after a bound session")

    monkeypatch.setattr("openexecutive.coding_agents.opencode.httpx.AsyncClient", _Client)
    monkeypatch.setattr(
        "openexecutive.coding_agents.opencode.asyncio.create_subprocess_exec",
        fake_exec,
    )
    workspace = WorkspaceSpec(id="product", path=tmp_path, default_runtime="opencode")
    with pytest.raises(CodingRuntimeError, match="HTTP request failed"):
        await OpenCodeRuntime("opencode", serve_url="http://127.0.0.1:4096").run(_job(), workspace)
    assert called_cli["n"] == 0


def test_summarize_json_and_plaintext() -> None:
    assert summarize_runtime_output('{"result":"hello"}') == "hello"
    assert summarize_runtime_output("not json at all") == "not json at all"


def test_safe_url_strips_userinfo() -> None:
    assert "hunter2" not in safe_url_for_log("http://oe:hunter2@127.0.0.1:4096/x")
