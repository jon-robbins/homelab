from __future__ import annotations

import json
import re
from collections.abc import Mapping

import httpx

from app.clients.http import post_with_retry


def _extract_json_payload(response: httpx.Response) -> dict[str, object]:
    content_type = response.headers.get("content-type", "")
    text = response.text
    if "text/event-stream" in content_type or text.lstrip().startswith("event:"):
        match = re.search(r"data:\s*(\{.*\})", text, re.DOTALL)
        if not match:
            raise ValueError("MCP SSE response did not contain JSON data")
        data = json.loads(match.group(1))
        if not isinstance(data, Mapping):
            raise TypeError("Expected JSON object in MCP SSE data")
        return dict(data)
    data = response.json()
    if not isinstance(data, Mapping):
        raise TypeError("Expected JSON object from MCP backend")
    return dict(data)


class McpClient:
    def __init__(self, base_url: str, api_token: str, timeout_seconds: int = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self._client = httpx.Client(timeout=timeout_seconds)
        self._session_id: str | None = None
        self._initialized = False

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        return headers

    def _post(self, payload: dict[str, object]) -> httpx.Response:
        response = post_with_retry(
            self._client, f"{self.base_url}/mcp", headers=self._headers(), json=payload
        )
        response.raise_for_status()
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        return response

    def _initialize(self) -> None:
        if self._initialized:
            return
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "homelab-agent", "version": "0.1.0"},
                },
            }
        )
        _extract_json_payload(response)
        self._post(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
        )
        self._initialized = True

    def list_tools(self) -> list[dict[str, object]]:
        self._initialize()
        response = self._post(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        data = _extract_json_payload(response)
        tools = data.get("result", {}).get("tools", [])
        if not isinstance(tools, list):
            raise TypeError("MCP tools/list returned a non-list tools field")
        return [dict(item) for item in tools if isinstance(item, Mapping)]

    def call_tool(self, name: str, arguments: dict[str, object]) -> str:
        self._initialize()
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        data = _extract_json_payload(response)
        result = data.get("result", {})
        if not isinstance(result, Mapping):
            raise TypeError("MCP tools/call returned a non-object result")
        content = result.get("content", [])
        if not isinstance(content, list):
            raise TypeError("MCP tools/call returned a non-list content field")
        text_parts: list[str] = []
        for item in content:
            if not isinstance(item, Mapping):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                text_parts.append(item["text"])
        return "\n".join(text_parts)
