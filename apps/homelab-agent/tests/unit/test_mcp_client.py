from __future__ import annotations

import httpx
import respx

from app.clients.mcp import McpClient


@respx.mock
def test_mcp_client_lists_tools() -> None:
    respx.post("http://mcp.test/mcp").mock(
        side_effect=[
            httpx.Response(
                200,
                headers={"mcp-session-id": "session-1"},
                json={"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}},
            ),
            httpx.Response(
                200,
                headers={"mcp-session-id": "session-1"},
                json={"jsonrpc": "2.0", "result": {}},
            ),
            httpx.Response(
                200,
                headers={"mcp-session-id": "session-1"},
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"tools": [{"name": "homelab_services_health", "description": "ok"}]},
                },
            ),
        ]
    )

    client = McpClient(base_url="http://mcp.test", api_token="token")
    tools = client.list_tools()

    assert tools[0]["name"] == "homelab_services_health"


@respx.mock
def test_mcp_client_retries_transient_transport_error_once() -> None:
    route = respx.post("http://mcp.test/mcp").mock(
        side_effect=[
            httpx.ConnectError("transient"),
            httpx.Response(
                200,
                headers={"mcp-session-id": "session-1"},
                json={"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}},
            ),
            httpx.Response(
                200,
                headers={"mcp-session-id": "session-1"},
                json={"jsonrpc": "2.0", "result": {}},
            ),
            httpx.Response(
                200,
                headers={"mcp-session-id": "session-1"},
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"tools": [{"name": "homelab_services_health", "description": "ok"}]},
                },
            ),
        ]
    )

    client = McpClient(base_url="http://mcp.test", api_token="token")
    tools = client.list_tools()

    assert tools[0]["name"] == "homelab_services_health"
    # One transient failure retried once, then the normal three-post handshake.
    assert route.call_count == 4
