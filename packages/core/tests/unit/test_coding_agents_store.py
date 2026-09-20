"""Round-trip tests for the coding_jobs SQLite store."""

from __future__ import annotations

from pathlib import Path

import pytest

from openexecutive.coding_agents import store


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db = tmp_path / "coding_jobs.db"
    monkeypatch.setattr("openexecutive.memory.episodic.DB_PATH", db)
    monkeypatch.setattr("openexecutive.coding_agents.store.DB_PATH", db)
    monkeypatch.setattr("openexecutive.audit.log_event", lambda *a, **k: None)
    monkeypatch.setattr("openexecutive.coding_agents.audit.log_event", lambda *a, **k: None)
    return db


def test_initialize_is_idempotent() -> None:
    store.initialize_coding_jobs_db()
    store.initialize_coding_jobs_db()
    assert store.list_jobs() == []


def test_insert_get_list_round_trip() -> None:
    row = store.insert_job("j1", "product", "cursor", "ask", "explain main.py")
    assert row["status"] == "queued"
    fetched = store.get_job("j1")
    assert fetched is not None
    assert fetched["workspace_id"] == "product"
    assert fetched["runtime"] == "cursor"
    assert fetched["mode"] == "ask"
    assert fetched["task"] == "explain main.py"
    assert fetched["events"] == []
    listed = store.list_jobs()
    assert len(listed) == 1
    assert listed[0]["job_id"] == "j1"


def test_unknown_job_is_none() -> None:
    store.initialize_coding_jobs_db()
    assert store.get_job("missing") is None


def test_count_active_and_status_filter() -> None:
    store.insert_job("a", "product", "cursor", "ask", "one")
    store.insert_job("b", "product", "opencode", "plan", "two")
    assert store.count_active_jobs() == 2
    store.update_job("a", status="succeeded", artifact="ok")
    assert store.count_active_jobs() == 1
    done = store.list_jobs(status="succeeded")
    assert [row["job_id"] for row in done] == ["a"]


def test_cas_update_refuses_stale_status() -> None:
    store.insert_job("j1", "product", "cursor", "ask", "task")
    store.update_job("j1", status="cancelled", expected_statuses=("queued", "running"))
    wrote = store.update_job(
        "j1",
        status="succeeded",
        artifact="too late",
        expected_statuses=("queued", "running"),
    )
    assert wrote is False
    row = store.get_job("j1")
    assert row is not None
    assert row["status"] == "cancelled"
    assert row["artifact"] is None


def test_append_event_caps_length() -> None:
    store.insert_job("j1", "product", "cursor", "ask", "task")
    store.append_event("j1", "started")
    store.append_event("j1", "x" * 500)
    row = store.get_job("j1")
    assert row is not None
    assert row["events"][0] == "started"
    assert len(row["events"][1]) <= 200
