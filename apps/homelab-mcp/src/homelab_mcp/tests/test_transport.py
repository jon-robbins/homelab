from __future__ import annotations

import json
import re

import pytest
from starlette.testclient import TestClient

from homelab_mcp.__main__ import build_app
from homelab_mcp.server import mcp

MCP_HEADERS = {
    "Authorization": "Bearer token",
    "Accept": "application/json, text/event-stream",
}


def _parse_mcp_response(response) -> dict:
    text = response.text
    if text.startswith("event:"):
        match = re.search(r"data: (\{.*\})", text, re.DOTALL)
        assert match, f"expected SSE data payload, got: {text[:200]}"
        return json.loads(match.group(1))
    return response.json()


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("MCP_API_TOKEN", "token")
    mcp._session_manager = None
    with TestClient(build_app()) as test_client:
        yield test_client


def _session_headers(response) -> dict[str, str]:
    session_id = response.headers.get("mcp-session-id")
    if not session_id:
        return dict(MCP_HEADERS)
    return {**MCP_HEADERS, "mcp-session-id": session_id}


def test_streamable_http_initialize(client: TestClient) -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1.0"},
        },
    }
    response = client.post("/mcp", json=payload, headers=MCP_HEADERS)
    assert response.status_code == 200
    body = _parse_mcp_response(response)
    assert body["result"]["serverInfo"]["name"] == "homelab"


def test_streamable_http_tools_list(client: TestClient) -> None:
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1.0"},
        },
    }
    init_response = client.post("/mcp", json=init, headers=MCP_HEADERS)
    headers = _session_headers(init_response)
    client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        headers=headers,
    )
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers=headers,
    )
    assert response.status_code == 200
    body = _parse_mcp_response(response)
    assert len(body["result"]["tools"]) >= 19


def test_health_unauthenticated(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.text == "ok"


def test_legacy_sse_message_endpoint_exists(client: TestClient) -> None:
    response = client.post(
        "/messages/?session_id=test",
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 1, "params": {}},
        headers=MCP_HEADERS,
    )
    assert response.status_code != 404


def _initialize_payload() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1.0"},
        },
    }


def test_wrong_token_rejected_with_401(client: TestClient) -> None:
    headers = {**MCP_HEADERS, "Authorization": "Bearer wrong-token"}
    response = client.post("/mcp", json=_initialize_payload(), headers=headers)
    assert response.status_code == 401
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "UNAUTHORIZED"


def test_missing_authorization_rejected_with_401(client: TestClient) -> None:
    headers = {"Accept": "application/json, text/event-stream"}
    response = client.post("/mcp", json=_initialize_payload(), headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_health_exempt_from_auth_even_with_wrong_token(client: TestClient) -> None:
    response = client.get("/health", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 200
    assert response.text == "ok"
