"""Service-level refusals, concurrency, cancel, and mocked runtimes."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

from openexecutive.alerts import store as alerts_store
from openexecutive.coding_agents import service
from openexecutive.coding_agents import store as coding_store
from openexecutive.coding_agents.models import WRITE_MODE_DEFERRED_CODE
from openexecutive.coding_agents.runtimes import CodingRuntimeError
from openexecutive.people import store as people_store


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = tmp_path / "coding_jobs.db"
    monkeypatch.setattr("openexecutive.memory.episodic.DB_PATH", db)
    monkeypatch.setattr("openexecutive.coding_agents.store.DB_PATH", db)
    monkeypatch.setattr(alerts_store, "DB_PATH", db)
    monkeypatch.setattr(people_store, "DB_PATH", db)
    alerts_store.initialize_db(db)
    people_store.initialize_db(db)
    coding_store.initialize_coding_jobs_db(db)
    monkeypatch.setattr("openexecutive.audit.log_event", lambda *a, **k: None)
    monkeypatch.setattr("openexecutive.coding_agents.audit.log_event", lambda *a, **k: None)
    service.clear_job_task()
    yield
    service.clear_job_task()


def _write_config(
    tmp_path: Path,
    workspace: Path,
    *,
    cursor_enabled: bool = True,
) -> Path:
    doc = {
        "runtimes": {
            "cursor": {"enabled": cursor_enabled, "binary": "agent"},
            "opencode": {"enabled": True, "binary": "opencode", "serve_url": ""},
        },
        "workspaces": [{"id": "product", "path": str(workspace), "default_runtime": "cursor"}],
    }
    path = tmp_path / "coding_agents.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


def _settings(
    tmp_path: Path,
    workspace: Path,
    *,
    cursor_enabled: bool = True,
    **overrides: Any,
) -> MagicMock:
    cfg = _write_config(tmp_path, workspace, cursor_enabled=cursor_enabled)
    settings = MagicMock()
    settings.coding_agents_enabled = True
    settings.coding_agents_config_path = cfg
    settings.coding_max_concurrent = 1
    settings.coding_job_timeout_s = 30
    settings.coding_cursor_binary = "agent"
    settings.coding_opencode_binary = "opencode"
    settings.coding_opencode_serve_url = ""
    settings.tool_result_max_chars = 50_000
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


@pytest.fixture
def settings(tmp_path: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    s = _settings(tmp_path, workspace)
    monkeypatch.setattr("openexecutive.coding_agents.service.get_settings", lambda: s)
    return s


async def _wait_terminal(job_id: str) -> dict[str, Any]:
    spawned = service.job_tasks().get(job_id)
    if spawned is not None:
        with contextlib.suppress(asyncio.CancelledError):
            await spawned
    for _ in range(100):
        row = await service.get_job(job_id)
        if row.get("status") not in {"queued", "running"}:
            return row
        await asyncio.sleep(0.01)
    return await service.get_job(job_id)


def _artifact_alerts() -> list[Any]:
    return [a for a in alerts_store.list_alerts() if a.source == "artifact"]


class _ImmediateRuntime:
    async def run(self, job: Any, workspace: Any) -> tuple[str, list[str]]:
        return "artifact-ok", ["ran"]

    async def abort(self) -> None:
        return None


class _SlowRuntime:
    async def run(self, job: Any, workspace: Any) -> tuple[str, list[str]]:
        await asyncio.sleep(2)
        return "nope", []

    async def abort(self) -> None:
        return None


class _BlockingRuntime:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, job: Any, workspace: Any) -> tuple[str, list[str]]:
        self.started.set()
        await self.release.wait()
        return "late", ["unblocked"]

    async def abort(self) -> None:
        self.release.set()


@pytest.mark.asyncio
async def test_agent_mode_refused(settings: MagicMock) -> None:
    result = await service.start_job("product", "edit the file", "agent")
    assert result["code"] == WRITE_MODE_DEFERRED_CODE
    assert "P2" in result["error"]
    assert "job_id" not in result


@pytest.mark.asyncio
async def test_write_mode_refused(settings: MagicMock) -> None:
    result = await service.start_job("product", "edit the file", "write", has_user_consent=True)
    assert result["code"] == WRITE_MODE_DEFERRED_CODE


@pytest.mark.asyncio
async def test_unknown_workspace(settings: MagicMock) -> None:
    result = await service.start_job("nope", "explain it", "ask")
    assert result["code"] == "unknown_workspace"


@pytest.mark.asyncio
async def test_path_must_be_a_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    not_dir = tmp_path / "file.txt"
    not_dir.write_text("x", encoding="utf-8")
    s = _settings(tmp_path, not_dir)
    monkeypatch.setattr("openexecutive.coding_agents.service.get_settings", lambda: s)
    result = await service.start_job("product", "explain it", "ask")
    assert result["code"] == "invalid_workspace_path"


@pytest.mark.asyncio
async def test_disabled_runtime(
    tmp_path: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = _settings(tmp_path, workspace, cursor_enabled=False)
    monkeypatch.setattr("openexecutive.coding_agents.service.get_settings", lambda: s)
    result = await service.start_job("product", "explain it", "ask", runtime="cursor")
    assert result["code"] == "runtime_disabled"


@pytest.mark.asyncio
async def test_disabled_feature_flag(
    settings: MagicMock,
) -> None:
    settings.coding_agents_enabled = False
    result = await service.start_job("product", "explain it", "ask")
    assert result["code"] == "disabled"


@pytest.mark.asyncio
async def test_start_returns_job_id_immediately(
    settings: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = _BlockingRuntime()
    monkeypatch.setattr(
        "openexecutive.coding_agents.service.get_runtime",
        lambda *a, **k: blocker,
    )
    result = await service.start_job("product", "explain main.py", "ask")
    assert "job_id" in result
    assert result["status"] == "queued"
    await blocker.started.wait()
    fetched = await service.get_job(result["job_id"])
    assert fetched["status"] == "running"
    blocker.release.set()
    spawned = service.job_tasks().get(result["job_id"])
    if spawned is not None:
        await spawned


@pytest.mark.asyncio
async def test_concurrency_cap(settings: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    blocker = _BlockingRuntime()
    monkeypatch.setattr(
        "openexecutive.coding_agents.service.get_runtime",
        lambda *a, **k: blocker,
    )
    first = await service.start_job("product", "first", "ask")
    await blocker.started.wait()
    second = await service.start_job("product", "second", "plan")
    assert second["code"] == "concurrency_cap"
    assert "job_id" not in second
    blocker.release.set()
    spawned = service.job_tasks().get(first["job_id"])
    if spawned is not None:
        await spawned


@pytest.mark.asyncio
async def test_cancel_unknown_job(settings: MagicMock) -> None:
    result = await service.cancel_job("does-not-exist")
    assert result["code"] == "unknown_job"


@pytest.mark.asyncio
async def test_cancel_running_job(settings: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    blocker = _BlockingRuntime()
    monkeypatch.setattr(
        "openexecutive.coding_agents.service.get_runtime",
        lambda *a, **k: blocker,
    )
    started = await service.start_job("product", "long job", "ask")
    await blocker.started.wait()
    cancelled = await service.cancel_job(started["job_id"])
    assert cancelled["status"] == "cancelled"
    spawned = service.job_tasks().get(started["job_id"])
    if spawned is not None:
        with pytest.raises(asyncio.CancelledError):
            await spawned


@pytest.mark.asyncio
async def test_timeout_marks_timed_out(
    settings: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.coding_job_timeout_s = 0.05

    monkeypatch.setattr(
        "openexecutive.coding_agents.service.get_runtime",
        lambda *a, **k: _SlowRuntime(),
    )
    started = await service.start_job("product", "slow", "ask")
    row = await _wait_terminal(started["job_id"])
    assert row["status"] == "timed_out"


@pytest.mark.asyncio
async def test_successful_run_stores_artifact(
    settings: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "openexecutive.coding_agents.service.get_runtime",
        lambda *a, **k: _ImmediateRuntime(),
    )
    started = await service.start_job("product", "summarize", "plan")
    row = await _wait_terminal(started["job_id"])
    assert row["status"] == "succeeded"
    assert row["artifact"] == "artifact-ok"


@pytest.mark.asyncio
async def test_success_creates_principal_artifact_alert(
    settings: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "openexecutive.coding_agents.service.get_runtime",
        lambda *a, **k: _ImmediateRuntime(),
    )
    started = await service.start_job("product", "summarize", "plan")
    row = await _wait_terminal(started["job_id"])
    cards = _artifact_alerts()
    assert len(cards) == 1
    card = cards[0]
    assert card.topic_tags == ["artifact"]
    assert card.source == "artifact"
    assert started["job_id"] in card.body
    assert "product" in card.body
    assert "succeeded" in card.body
    assert "artifact-ok" in card.body
    joined = f"{card.headline}\n{card.body}\n{card.suggested_action}".lower()
    assert "cursor" not in joined
    assert "opencode" not in joined
    assert row["status"] == "succeeded"


@pytest.mark.asyncio
async def test_failure_creates_artifact_alert(
    settings: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Boom:
        async def run(self, job: Any, workspace: Any) -> tuple[str, list[str]]:
            raise CodingRuntimeError("runtime exploded", events=["boom"])

        async def abort(self) -> None:
            return None

    monkeypatch.setattr(
        "openexecutive.coding_agents.service.get_runtime",
        lambda *a, **k: _Boom(),
    )
    started = await service.start_job("product", "summarize", "ask")
    row = await _wait_terminal(started["job_id"])
    assert row["status"] == "failed"
    assert "runtime exploded" in (row["error"] or "")
    cards = _artifact_alerts()
    assert len(cards) == 1
    assert started["job_id"] in cards[0].body
    assert "failed" in cards[0].body
    assert "runtime exploded" not in cards[0].body
    assert "The analysis job did not finish successfully." in cards[0].body
    joined = f"{cards[0].headline}\n{cards[0].body}\n{cards[0].suggested_action}".lower()
    assert "cursor" not in joined
    assert "opencode" not in joined
    assert cards[0].severity == "high"


@pytest.mark.asyncio
async def test_timeout_creates_artifact_alert(
    settings: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.coding_job_timeout_s = 0.05

    monkeypatch.setattr(
        "openexecutive.coding_agents.service.get_runtime",
        lambda *a, **k: _SlowRuntime(),
    )
    started = await service.start_job("product", "slow", "ask")
    row = await _wait_terminal(started["job_id"])
    assert row["status"] == "timed_out"
    cards = _artifact_alerts()
    assert len(cards) == 1
    assert "timed_out" in cards[0].body
    assert started["job_id"] in cards[0].body
    assert "The analysis job exceeded its time limit." in cards[0].body
    joined = f"{cards[0].headline}\n{cards[0].body}\n{cards[0].suggested_action}".lower()
    assert "cursor" not in joined
    assert "opencode" not in joined


@pytest.mark.asyncio
async def test_runtime_timeout_error_marks_timed_out(
    settings: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _HttpTimeout:
        async def run(self, job: Any, workspace: Any) -> tuple[str, list[str]]:
            raise TimeoutError("coding runtime HTTP request timed out")

        async def abort(self) -> None:
            return None

    monkeypatch.setattr(
        "openexecutive.coding_agents.service.get_runtime",
        lambda *a, **k: _HttpTimeout(),
    )
    started = await service.start_job("product", "slow", "ask")
    row = await _wait_terminal(started["job_id"])
    assert row["status"] == "timed_out"
    assert row["error"] == "coding runtime HTTP request timed out"
    cards = _artifact_alerts()
    assert len(cards) == 1
    assert "The analysis job exceeded its time limit." in cards[0].body
    assert "HTTP request timed out" not in cards[0].body
    joined = f"{cards[0].headline}\n{cards[0].body}\n{cards[0].suggested_action}".lower()
    assert "cursor" not in joined
    assert "opencode" not in joined


@pytest.mark.asyncio
async def test_cancel_does_not_create_artifact_alert(
    settings: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = _BlockingRuntime()
    monkeypatch.setattr(
        "openexecutive.coding_agents.service.get_runtime",
        lambda *a, **k: blocker,
    )
    started = await service.start_job("product", "long job", "ask")
    await blocker.started.wait()
    cancelled = await service.cancel_job(started["job_id"])
    assert cancelled["status"] == "cancelled"
    spawned = service.job_tasks().get(started["job_id"])
    if spawned is not None:
        with pytest.raises(asyncio.CancelledError):
            await spawned
    assert _artifact_alerts() == []


@pytest.mark.asyncio
async def test_cancel_wins_kill_as_failure_without_alert(
    settings: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Abort that surfaces as a runtime error must not overwrite cancelled."""

    class _KillAsFailure:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self._aborting = asyncio.Event()

        async def run(self, job: Any, workspace: Any) -> tuple[str, list[str]]:
            self.started.set()
            with contextlib.suppress(asyncio.CancelledError):
                await self._aborting.wait()
            await asyncio.sleep(0.05)
            raise CodingRuntimeError("coding runtime exited -9", events=["killed"])

        async def abort(self) -> None:
            self._aborting.set()
            await asyncio.sleep(0.05)

    killer = _KillAsFailure()
    monkeypatch.setattr(
        "openexecutive.coding_agents.service.get_runtime",
        lambda *a, **k: killer,
    )
    started = await service.start_job("product", "long job", "ask")
    await killer.started.wait()
    cancelled = await service.cancel_job(started["job_id"])
    assert cancelled["status"] == "cancelled"
    spawned = service.job_tasks().get(started["job_id"])
    if spawned is not None:
        with contextlib.suppress(asyncio.CancelledError, CodingRuntimeError):
            await spawned
    row = await service.get_job(started["job_id"])
    assert row["status"] == "cancelled"
    assert _artifact_alerts() == []


@pytest.mark.asyncio
async def test_notify_failure_does_not_fail_job(
    settings: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "openexecutive.coding_agents.service.get_runtime",
        lambda *a, **k: _ImmediateRuntime(),
    )

    def _boom(**_k: Any) -> int:
        raise RuntimeError("alerts down")

    monkeypatch.setattr("openexecutive.alerts.store.insert_alert", _boom)
    started = await service.start_job("product", "summarize", "plan")
    row = await _wait_terminal(started["job_id"])
    assert row["status"] == "succeeded"
    assert row["artifact"] == "artifact-ok"
