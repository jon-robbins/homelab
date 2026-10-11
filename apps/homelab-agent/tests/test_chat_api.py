from __future__ import annotations

import json

import httpx
import respx
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

AUTH = {"Authorization": "Bearer test-token"}


def _reset_settings() -> None:
    get_settings.cache_clear()


def _configure(monkeypatch, tmp_path, **overrides: str) -> None:
    monkeypatch.setenv("HOMELAB_AGENT_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("HOMELAB_AGENT_MODEL_BASE_URL", "http://qwen.test/v1")
    monkeypatch.setenv("HOMELAB_AGENT_MCP_URL", "http://mcp.test")
    for key, value in overrides.items():
        monkeypatch.setenv(key, str(value))
    _reset_settings()


def _model_final_answer(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-final",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        },
    )


def _model_tool_call(
    name: str, arguments: dict | None = None, content: str | None = None
) -> httpx.Response:
    message: dict[str, object] = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments or {})},
            }
        ],
    }
    if content is not None:
        message["content"] = content
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-tool",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}],
        },
    )


def _mcp_ok(payload: dict) -> httpx.Response:
    return httpx.Response(200, headers={"mcp-session-id": "session-1"}, json=payload)


def _mcp_tool_result(text: str) -> dict:
    return {"jsonrpc": "2.0", "result": {"content": [{"type": "text", "text": text}]}}


def _read_audit_records(tmp_path) -> list[dict]:
    raw = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip()
    return [json.loads(line) for line in raw.splitlines() if line]


@respx.mock
def test_chat_completion_without_tools_returns_final_answer(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOMELAB_AGENT_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("HOMELAB_AGENT_MODEL_BASE_URL", "http://qwen.test/v1")
    monkeypatch.setenv("HOMELAB_AGENT_MCP_URL", "http://mcp.test")
    _reset_settings()

    respx.post("http://qwen.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello from Qwen."},
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    )
    respx.post("http://mcp.test/mcp").mock(
        return_value=httpx.Response(
            200,
            headers={"mcp-session-id": "session-1"},
            json={"jsonrpc": "2.0", "result": {"tools": []}},
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={
                "model": "Qwen3.8-27B",
                "messages": [{"role": "user", "content": "Say hi."}],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "Hello from Qwen."
    assert (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip()


@respx.mock
def test_chat_completion_runs_one_tool_then_finishes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOMELAB_AGENT_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("HOMELAB_AGENT_ALLOWED_TOOLS", "homelab_services_health")
    monkeypatch.setenv("HOMELAB_AGENT_MODEL_BASE_URL", "http://qwen.test/v1")
    monkeypatch.setenv("HOMELAB_AGENT_MCP_URL", "http://mcp.test")
    _reset_settings()

    respx.post("http://qwen.test/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "id": "chatcmpl-1",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "homelab_services_health",
                                            "arguments": json.dumps({"services": ["qbittorrent"]}),
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                },
            ),
            httpx.Response(
                200,
                json={
                    "id": "chatcmpl-2",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "qBittorrent is healthy.",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                },
            ),
        ]
    )
    respx.post("http://mcp.test/mcp").mock(
        return_value=httpx.Response(
            200,
            headers={"mcp-session-id": "session-1"},
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "content": [
                        {"type": "text", "text": '{"ok": true, "summary": "qbittorrent healthy"}'}
                    ]
                },
            },
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={
                "model": "Qwen3.8-27B",
                "messages": [{"role": "user", "content": "Check qBittorrent."}],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "qBittorrent is healthy."
    assert body["choices"][0]["finish_reason"] == "stop"
    assert "qbittorrent healthy" in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")


@respx.mock
def test_chat_completion_rejects_disallowed_tool_before_mcp_call(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOMELAB_AGENT_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("HOMELAB_AGENT_ALLOWED_TOOLS", "homelab_services_health")
    monkeypatch.setenv("HOMELAB_AGENT_MODEL_BASE_URL", "http://qwen.test/v1")
    monkeypatch.setenv("HOMELAB_AGENT_MCP_URL", "http://mcp.test")
    _reset_settings()

    respx.post("http://qwen.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "shell_exec",
                                        "arguments": json.dumps({"cmd": "whoami"}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
        )
    )
    respx.post("http://mcp.test/mcp").mock(
        return_value=httpx.Response(
            200,
            headers={"mcp-session-id": "session-1"},
            json={
                "jsonrpc": "2.0",
                "result": {
                    "tools": [{"name": "homelab_services_health", "description": "ok"}]
                },
            },
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={
                "model": "Qwen3.8-27B",
                "messages": [{"role": "user", "content": "Run a shell command."}],
            },
        )

    assert response.status_code == 403
    assert "shell_exec" in response.json()["detail"]
    # The handshake (initialize / notifications/initialized / tools/list) runs
    # before the model is called, so those posts are expected. A disallowed tool
    # must be blocked before the tools/call RPC reaches the MCP backend.
    posted_methods = [
        json.loads(call.request.content).get("method")
        for call in respx.calls
        if call.request.method == "POST"
        and str(call.request.url) == "http://mcp.test/mcp"
        and call.request.content
    ]
    assert "tools/call" not in posted_methods
    # Failures must leave an audit trail too, not just successes.
    records = _read_audit_records(tmp_path)
    assert records[-1]["status"] == "error"
    assert records[-1]["status_code"] == 403
    assert "shell_exec" in records[-1]["error"]


@respx.mock
def test_model_backend_failure_returns_502_and_audits(tmp_path, monkeypatch) -> None:
    _configure(monkeypatch, tmp_path)

    respx.post("http://qwen.test/v1/chat/completions").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.ConnectError("boom")]
    )
    respx.post("http://mcp.test/mcp").mock(
        return_value=_mcp_ok({"jsonrpc": "2.0", "result": {"tools": []}})
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "Qwen3.8-27B", "messages": [{"role": "user", "content": "Hi"}]},
        )

    assert response.status_code == 502
    assert "model" in response.json()["detail"].lower()
    records = _read_audit_records(tmp_path)
    assert records[-1]["status"] == "error"
    assert records[-1]["status_code"] == 502


@respx.mock
def test_model_backend_timeout_returns_504_and_audits(tmp_path, monkeypatch) -> None:
    _configure(monkeypatch, tmp_path)

    respx.post("http://qwen.test/v1/chat/completions").mock(
        side_effect=[httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow")]
    )
    respx.post("http://mcp.test/mcp").mock(
        return_value=_mcp_ok({"jsonrpc": "2.0", "result": {"tools": []}})
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "Qwen3.8-27B", "messages": [{"role": "user", "content": "Hi"}]},
        )

    assert response.status_code == 504
    assert "timed out" in response.json()["detail"].lower()
    records = _read_audit_records(tmp_path)
    assert records[-1]["status"] == "error"
    assert records[-1]["status_code"] == 504


@respx.mock
def test_mcp_backend_failure_returns_502_and_audits(tmp_path, monkeypatch) -> None:
    _configure(monkeypatch, tmp_path)

    respx.post("http://qwen.test/v1/chat/completions").mock(
        return_value=_model_final_answer("unused")
    )
    respx.post("http://mcp.test/mcp").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.ConnectError("boom")]
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "Qwen3.8-27B", "messages": [{"role": "user", "content": "Hi"}]},
        )

    assert response.status_code == 502
    assert "mcp" in response.json()["detail"].lower()
    records = _read_audit_records(tmp_path)
    assert records[-1]["status"] == "error"
    assert records[-1]["status_code"] == 502


@respx.mock
def test_loop_exhaustion_returns_partial_answer_with_metadata(tmp_path, monkeypatch) -> None:
    _configure(
        monkeypatch,
        tmp_path,
        HOMELAB_AGENT_MAX_TOOL_STEPS=2,
        HOMELAB_AGENT_ALLOWED_TOOLS="homelab_services_health",
    )

    respx.post("http://qwen.test/v1/chat/completions").mock(
        return_value=_model_tool_call("homelab_services_health", content="Working on it.")
    )
    respx.post("http://mcp.test/mcp").mock(
        return_value=_mcp_ok(_mcp_tool_result("service state"))
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "Qwen3.8-27B", "messages": [{"role": "user", "content": "Check."}]},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "Working on it."
    assert body["choices"][0]["finish_reason"] == "length"
    assert body["homelab_agent"]["status"] == "loop_exhausted"
    assert body["homelab_agent"]["max_tool_steps"] == 2
    records = _read_audit_records(tmp_path)
    assert records[-1]["status"] == "loop_exhausted"


def test_request_with_too_many_messages_rejected(tmp_path, monkeypatch) -> None:
    _configure(monkeypatch, tmp_path)

    messages = [{"role": "user", "content": f"message {i}"} for i in range(101)]
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "Qwen3.8-27B", "messages": messages},
        )

    assert response.status_code == 422


def test_request_with_oversized_message_rejected(tmp_path, monkeypatch) -> None:
    _configure(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={
                "model": "Qwen3.8-27B",
                "messages": [{"role": "user", "content": "x" * 65537}],
            },
        )

    assert response.status_code == 422


@respx.mock
def test_tool_output_truncated_before_next_model_call(tmp_path, monkeypatch) -> None:
    _configure(
        monkeypatch,
        tmp_path,
        HOMELAB_AGENT_ALLOWED_TOOLS="homelab_services_health",
        HOMELAB_AGENT_MAX_TOOL_OUTPUT_CHARS=10,
    )

    respx.post("http://qwen.test/v1/chat/completions").mock(
        side_effect=[
            _model_tool_call("homelab_services_health"),
            _model_final_answer("all done"),
        ]
    )
    respx.post("http://mcp.test/mcp").mock(
        return_value=_mcp_ok(_mcp_tool_result("x" * 100))
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "Qwen3.8-27B", "messages": [{"role": "user", "content": "Check."}]},
        )

    assert response.status_code == 200
    model_calls = [
        call
        for call in respx.calls
        if str(call.request.url) == "http://qwen.test/v1/chat/completions"
    ]
    second_request = json.loads(model_calls[1].request.content)
    tool_message = second_request["messages"][-1]
    assert tool_message["role"] == "tool"
    assert len(tool_message["content"]) == 10
    records = _read_audit_records(tmp_path)
    assert records[-1]["status"] == "ok"
    assert records[-1]["tool_calls"][0]["truncated"] is True
    assert len(records[-1]["tool_calls"][0]["output"]) == 10


@respx.mock
def test_response_content_truncated(tmp_path, monkeypatch) -> None:
    _configure(monkeypatch, tmp_path, HOMELAB_AGENT_MAX_RESPONSE_CHARS=10)

    respx.post("http://qwen.test/v1/chat/completions").mock(
        return_value=_model_final_answer("x" * 50)
    )
    respx.post("http://mcp.test/mcp").mock(
        return_value=_mcp_ok({"jsonrpc": "2.0", "result": {"tools": []}})
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "Qwen3.8-27B", "messages": [{"role": "user", "content": "Hi"}]},
        )

    assert response.status_code == 200
    assert len(response.json()["choices"][0]["message"]["content"]) == 10


@respx.mock
def test_backend_auth_tokens_are_forwarded(tmp_path, monkeypatch) -> None:
    _configure(
        monkeypatch,
        tmp_path,
        HOMELAB_AGENT_ALLOWED_TOOLS="homelab_services_health",
        MCP_API_TOKEN="mcp-token",
        VLLM_API_KEY="vllm-key",
    )

    respx.post("http://qwen.test/v1/chat/completions").mock(
        side_effect=[
            _model_tool_call("homelab_services_health"),
            _model_final_answer("done"),
        ]
    )
    respx.post("http://mcp.test/mcp").mock(
        return_value=_mcp_ok(_mcp_tool_result("service state"))
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "Qwen3.8-27B", "messages": [{"role": "user", "content": "Check."}]},
        )

    assert response.status_code == 200
    model_calls = [
        call
        for call in respx.calls
        if str(call.request.url) == "http://qwen.test/v1/chat/completions"
    ]
    mcp_calls = [
        call for call in respx.calls if str(call.request.url) == "http://mcp.test/mcp"
    ]
    # The model backend must receive the vLLM API key, not the agent's own
    # token, and every model call must be authenticated.
    assert model_calls
    for call in model_calls:
        assert call.request.headers["Authorization"] == "Bearer vllm-key"
    # MCP must receive MCP_API_TOKEN, not the agent's client-facing token.
    assert mcp_calls
    for call in mcp_calls:
        assert call.request.headers["Authorization"] == "Bearer mcp-token"
