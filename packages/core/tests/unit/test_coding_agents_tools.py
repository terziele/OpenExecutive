"""JSON in/out tools: validation errors, cancel of unknown job, name order."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from openexecutive.coding_agents.tools import (
    CODING_AGENT_HANDLERS,
    CODING_AGENT_TOOLS,
    handle_cancel_coding_job,
    handle_get_coding_job,
    handle_start_coding_job,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = tmp_path / "coding_jobs.db"
    monkeypatch.setattr("openexecutive.memory.episodic.DB_PATH", db)
    monkeypatch.setattr("openexecutive.coding_agents.store.DB_PATH", db)
    monkeypatch.setattr("openexecutive.audit.log_event", lambda *a, **k: None)
    monkeypatch.setattr("openexecutive.coding_agents.audit.log_event", lambda *a, **k: None)


def _parse(raw: str) -> dict[str, Any]:
    payload = json.loads(raw)
    assert isinstance(payload, dict)
    return payload


def test_tools_are_sorted_by_name() -> None:
    names = [tool["name"] for tool in CODING_AGENT_TOOLS]
    assert names == ["cancel_coding_job", "get_coding_job", "start_coding_job"]
    assert set(CODING_AGENT_HANDLERS) == set(names)


def test_descriptions_forbid_vendor_names_in_user_text() -> None:
    for tool in CODING_AGENT_TOOLS:
        desc = tool["description"]
        assert "do not name cursor" in desc.lower()
    start_desc = next(
        tool["description"] for tool in CODING_AGENT_TOOLS if tool["name"] == "start_coding_job"
    )
    assert "workspace_id" in start_desc
    assert "filesystem path" in start_desc.lower() or "not a filesystem path" in start_desc.lower()


@pytest.mark.asyncio
async def test_start_rejects_missing_fields() -> None:
    payload = _parse(await handle_start_coding_job({}))
    assert payload["code"] == "invalid_input"


@pytest.mark.asyncio
async def test_start_rejects_path_as_workspace_id() -> None:
    payload = _parse(
        await handle_start_coding_job(
            {
                "workspace_id": "/repos/product",
                "task": "look around",
                "mode": "ask",
            }
        )
    )
    assert payload["code"] == "invalid_input"


@pytest.mark.asyncio
async def test_get_rejects_missing_job_id() -> None:
    payload = _parse(await handle_get_coding_job({}))
    assert payload["code"] == "invalid_input"


@pytest.mark.asyncio
async def test_get_unknown_job() -> None:
    payload = _parse(await handle_get_coding_job({"job_id": "nope"}))
    assert payload["code"] == "unknown_job"


@pytest.mark.asyncio
async def test_cancel_unknown_job() -> None:
    payload = _parse(await handle_cancel_coding_job({"job_id": "nope"}))
    assert payload["code"] == "unknown_job"
    assert "error" in payload


@pytest.mark.asyncio
async def test_cancel_rejects_non_string_job_id() -> None:
    payload = _parse(await handle_cancel_coding_job({"job_id": 12}))
    assert payload["code"] == "invalid_input"


@pytest.mark.asyncio
async def test_start_forwards_to_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_start(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"job_id": "jid", "status": "queued"}

    monkeypatch.setattr("openexecutive.coding_agents.service.start_job", fake_start)
    payload = _parse(
        await handle_start_coding_job({"workspace_id": "product", "task": "explain", "mode": "ask"})
    )
    assert payload["job_id"] == "jid"
