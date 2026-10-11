from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.clients.model import ModelClient


@respx.mock
def test_model_client_posts_chat_completions() -> None:
    respx.post("http://qwen.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    )

    client = ModelClient(base_url="http://qwen.test/v1", model_name="Qwen3.8-27B")
    response = client.chat(
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
    )

    assert response["choices"][0]["message"]["content"] == "ok"


@respx.mock
def test_model_client_retries_transient_transport_error_once() -> None:
    route = respx.post("http://qwen.test/v1/chat/completions").mock(
        side_effect=[
            httpx.ConnectError("transient"),
            httpx.Response(
                200,
                json={
                    "id": "chatcmpl-1",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            ),
        ]
    )

    client = ModelClient(base_url="http://qwen.test/v1", model_name="Qwen3.8-27B")
    response = client.chat(messages=[{"role": "user", "content": "hello"}])

    assert response["choices"][0]["message"]["content"] == "ok"
    assert route.call_count == 2


@respx.mock
def test_model_client_does_not_retry_http_status_errors() -> None:
    route = respx.post("http://qwen.test/v1/chat/completions").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )

    client = ModelClient(base_url="http://qwen.test/v1", model_name="Qwen3.8-27B")
    with pytest.raises(httpx.HTTPStatusError):
        client.chat(messages=[{"role": "user", "content": "hello"}])

    assert route.call_count == 1


@respx.mock
def test_model_client_sends_bearer_auth_when_key_set() -> None:
    route = respx.post("http://qwen.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    )

    client = ModelClient(
        base_url="http://qwen.test/v1", model_name="Qwen3.8-27B", api_key="vllm-key"
    )
    client.chat(messages=[{"role": "user", "content": "hello"}])

    assert route.calls.last.request.headers["Authorization"] == "Bearer vllm-key"


@respx.mock
def test_model_client_omits_auth_header_when_no_key() -> None:
    route = respx.post("http://qwen.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    )

    client = ModelClient(base_url="http://qwen.test/v1", model_name="Qwen3.8-27B")
    client.chat(messages=[{"role": "user", "content": "hello"}])

    assert "Authorization" not in route.calls.last.request.headers


@respx.mock
def test_model_client_omits_tools_when_list_is_empty() -> None:
    # vLLM rejects `"tools": []` with a 400: "must not be an empty array".
    route = respx.post("http://qwen.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    )

    client = ModelClient(base_url="http://qwen.test/v1", model_name="Qwen3.8-27B")
    client.chat(messages=[{"role": "user", "content": "hello"}], tools=[])

    body = json.loads(route.calls.last.request.content)
    assert "tools" not in body
    assert "tool_choice" not in body
