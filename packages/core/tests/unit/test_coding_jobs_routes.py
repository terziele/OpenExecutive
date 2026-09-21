"""HTTP inspect/cancel routes for coding-agent jobs.

Uses a bare FastAPI app (no lifespan, no shared-secret middleware) and
monkeypatches the service so tests never touch the default episodic DB.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openexecutive.api.routes import coding_jobs as coding_jobs_route


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr("openexecutive.audit.log_event", lambda *a, **k: None)
    monkeypatch.setattr("openexecutive.coding_agents.audit.log_event", lambda *a, **k: None)
    app = FastAPI()
    app.include_router(coding_jobs_route.router)
    return TestClient(app)


def test_list_empty(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_list(*, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(coding_jobs_route, "list_jobs", fake_list)
    res = client.get("/coding-jobs")
    assert res.status_code == 200
    assert res.json() == {"jobs": []}


def test_get_missing_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(job_id: str) -> dict[str, Any]:
        return {"error": "Unknown job.", "code": "unknown_job"}

    monkeypatch.setattr(coding_jobs_route, "get_job", fake_get)
    res = client.get("/coding-jobs/missing")
    assert res.status_code == 404


def test_cancel_missing_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_cancel(job_id: str) -> dict[str, Any]:
        return {"error": "Unknown job.", "code": "unknown_job"}

    monkeypatch.setattr(coding_jobs_route, "cancel_job", fake_cancel)
    res = client.post("/coding-jobs/missing/cancel")
    assert res.status_code == 404


def test_get_returns_job(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(job_id: str) -> dict[str, Any]:
        return {"job_id": job_id, "status": "succeeded", "workspace_id": "product"}

    monkeypatch.setattr(coding_jobs_route, "get_job", fake_get)
    res = client.get("/coding-jobs/abc123")
    assert res.status_code == 200
    assert res.json()["job_id"] == "abc123"


def test_cancel_returns_job(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_cancel(job_id: str) -> dict[str, Any]:
        return {"job_id": job_id, "status": "cancelled"}

    monkeypatch.setattr(coding_jobs_route, "cancel_job", fake_cancel)
    res = client.post("/coding-jobs/abc123/cancel")
    assert res.status_code == 200
    assert res.json()["status"] == "cancelled"


def test_cancel_already_terminal_409(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_cancel(job_id: str) -> dict[str, Any]:
        return {
            "error": "Job is already succeeded.",
            "code": "not_running",
            "job": {"job_id": job_id, "status": "succeeded"},
        }

    monkeypatch.setattr(coding_jobs_route, "cancel_job", fake_cancel)
    res = client.post("/coding-jobs/abc123/cancel")
    assert res.status_code == 409
    body = res.json()
    assert body["detail"]["code"] == "not_running"
    assert body["detail"]["job"]["status"] == "succeeded"


def test_post_start_is_not_a_route(client: TestClient) -> None:
    res = client.post("/coding-jobs")
    assert res.status_code == 405
