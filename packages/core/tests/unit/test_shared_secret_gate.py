"""Shared-secret gate: ``x-api-key`` and ``Authorization: Bearer`` aliases.

Hits ``/mcp`` so MCP/CLI clients are proven to authenticate the way their
docs show (``headers.Authorization``). Routing only — lifespan is not run.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

_SECRET = "testsecret"
_MCP_BODY = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
_MCP_ACCEPT = {"accept": "application/json, text/event-stream"}


def _gated_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from openexecutive.api.main import create_app

    monkeypatch.setenv("BACKEND_SHARED_SECRET", _SECRET)
    monkeypatch.delenv("OE_PUBLIC_DEPLOYMENT", raising=False)
    return TestClient(create_app())


def _post_mcp(client: TestClient, extra_headers: dict[str, str] | None = None):
    headers = {**_MCP_ACCEPT, **(extra_headers or {})}
    return client.post("/mcp", json=_MCP_BODY, headers=headers, follow_redirects=False)


def test_no_header_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _gated_client(monkeypatch)
    assert _post_mcp(client).status_code == 401


def test_x_api_key_correct_passes_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _gated_client(monkeypatch)
    assert _post_mcp(client, {"x-api-key": _SECRET}).status_code != 401


def test_authorization_bearer_correct_passes_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _gated_client(monkeypatch)
    assert _post_mcp(client, {"Authorization": f"Bearer {_SECRET}"}).status_code != 401


def test_authorization_lowercase_scheme_passes_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _gated_client(monkeypatch)
    assert _post_mcp(client, {"Authorization": f"bearer {_SECRET}"}).status_code != 401


def test_wrong_bearer_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _gated_client(monkeypatch)
    resp = _post_mcp(client, {"Authorization": "Bearer wrongsecret"})
    assert resp.status_code == 401
    assert "wrongsecret" not in resp.text
    assert _SECRET not in resp.text


def test_mismatched_headers_are_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both set but different — do not silently prefer one."""
    client = _gated_client(monkeypatch)
    resp = _post_mcp(
        client,
        {"x-api-key": _SECRET, "Authorization": "Bearer other-secret"},
    )
    assert resp.status_code == 401


def test_matching_headers_pass_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _gated_client(monkeypatch)
    assert (
        _post_mcp(
            client,
            {"x-api-key": _SECRET, "Authorization": f"Bearer {_SECRET}"},
        ).status_code
        != 401
    )


def test_unauthenticated_paths_work_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _gated_client(monkeypatch)
    health = client.get("/health", follow_redirects=False)
    assert health.status_code != 401

    options = client.options("/mcp", follow_redirects=False)
    assert options.status_code != 401


def test_query_string_secret_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate reads headers only — never a URL query."""
    client = _gated_client(monkeypatch)
    resp = client.post(
        "/mcp",
        json=_MCP_BODY,
        headers=_MCP_ACCEPT,
        params={"api_key": _SECRET, "x-api-key": _SECRET},
        follow_redirects=False,
    )
    assert resp.status_code == 401
