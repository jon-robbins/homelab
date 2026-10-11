from __future__ import annotations

import json
import time
from collections.abc import Iterable

import httpx
from fastapi import HTTPException, status

from app.agent.prompting import build_system_prompt, normalize_tool_spec
from app.agent.tool_policy import filter_tool_specs, tool_is_allowed
from app.api.auth import new_request_id
from app.audit import append_audit_record
from app.clients.mcp import McpClient
from app.clients.model import ModelClient
from app.clients.schemas import ChatCompletionRequest
from app.config import Settings


def _message_to_dict(message) -> dict[str, object]:
    payload = message.model_dump(exclude_none=True)
    if "tool_calls" in payload:
        payload["tool_calls"] = list(payload["tool_calls"])
    return payload


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if limit <= 0 or len(text) <= limit:
        return text, False
    return text[:limit], True


def _backend_http_exception(backend: str, exc: httpx.HTTPError) -> HTTPException:
    if isinstance(exc, httpx.TimeoutException):
        return HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"{backend} backend timed out",
        )
    if isinstance(exc, httpx.HTTPStatusError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"{backend} backend returned HTTP {exc.response.status_code}",
        )
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"{backend} backend connection failed",
    )


class AgentLoop:
    def __init__(
        self,
        settings: Settings,
        model_client: ModelClient,
        mcp_client: McpClient,
    ) -> None:
        self._settings = settings
        self._model_client = model_client
        self._mcp_client = mcp_client

    def _build_tool_specs(self) -> list[dict[str, object]]:
        tools = self._mcp_client.list_tools()
        return filter_tool_specs(tools, self._settings.allowlist())

    def _build_openai_tools(self, tool_specs: Iterable[dict[str, object]]) -> list[dict[str, object]]:
        return [normalize_tool_spec(spec) for spec in tool_specs]

    def run_chat_completion(self, request: ChatCompletionRequest) -> dict[str, object]:
        request_id = f"chatcmpl-{new_request_id()}"
        started_at = time.time()
        messages = [_message_to_dict(message) for message in request.messages]
        audit_tools: list[dict[str, object]] = []
        last_assistant_content = ""

        def _audit(
            record_status: str,
            response: dict[str, object] | None = None,
            error: str | None = None,
            status_code: int | None = None,
        ) -> None:
            record: dict[str, object] = {
                "request_id": request_id,
                "model": request.model,
                "messages": messages,
                "tool_calls": audit_tools,
                "status": record_status,
                "elapsed_ms": round((time.time() - started_at) * 1000, 2),
            }
            if response is not None:
                record["response"] = response
            if error is not None:
                record["error"] = error
            if status_code is not None:
                record["status_code"] = status_code
            append_audit_record(self._settings.audit_path, record)

        try:
            try:
                tool_specs = self._build_tool_specs()
            except httpx.HTTPError as exc:
                raise _backend_http_exception("MCP", exc) from exc
            openai_tools = self._build_openai_tools(tool_specs)
            system_prompt = build_system_prompt(spec["name"] for spec in tool_specs)
            if not messages or messages[0].get("role") != "system":
                messages.insert(0, {"role": "system", "content": system_prompt})
            else:
                current = str(messages[0].get("content") or "").strip()
                messages[0]["content"] = f"{current}\n\n{system_prompt}".strip()

            for _ in range(self._settings.max_tool_steps):
                try:
                    response = self._model_client.chat(
                        messages=messages,
                        tools=openai_tools,
                        tool_choice=request.tool_choice or "auto",
                        temperature=request.temperature,
                        max_tokens=request.max_tokens,
                    )
                except httpx.HTTPError as exc:
                    raise _backend_http_exception("Model", exc) from exc
                choices = response.get("choices", [])
                if not choices:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="Model backend returned no choices",
                    )
                choice = choices[0]
                message = choice.get("message", {})
                tool_calls = message.get("tool_calls") or []
                assistant_content = str(message.get("content") or "")
                if assistant_content:
                    last_assistant_content = assistant_content
                messages.append({k: v for k, v in message.items() if v is not None})
                if tool_calls:
                    for tool_call in tool_calls:
                        function = tool_call.get("function", {})
                        tool_name = str(function.get("name", ""))
                        if not tool_is_allowed(tool_name, self._settings.allowlist()):
                            raise HTTPException(
                                status_code=status.HTTP_403_FORBIDDEN,
                                detail=f"Tool not allowed: {tool_name}",
                            )
                        arguments_raw = str(function.get("arguments") or "{}")
                        try:
                            arguments = json.loads(arguments_raw)
                        except json.JSONDecodeError as exc:
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Invalid tool arguments for {tool_name}",
                            ) from exc
                        if not isinstance(arguments, dict):
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Tool arguments for {tool_name} must be an object",
                            )
                        try:
                            output = self._mcp_client.call_tool(tool_name, arguments)
                        except httpx.HTTPError as exc:
                            raise _backend_http_exception("MCP", exc) from exc
                        output, truncated = _truncate(
                            output, self._settings.max_tool_output_chars
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": str(tool_call.get("id", "")),
                                "content": output,
                            }
                        )
                        audit_tools.append(
                            {
                                "name": tool_name,
                                "arguments": arguments,
                                "output": output,
                                "truncated": truncated,
                            }
                        )
                    continue

                final_content, _ = _truncate(
                    assistant_content, self._settings.max_response_chars
                )
                result = {
                    "id": request_id,
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": request.model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": final_content,
                            },
                            "finish_reason": choice.get("finish_reason") or "stop",
                        }
                    ],
                    "usage": response.get("usage"),
                }
                _audit("ok", response=result)
                return result

            # Loop exhausted: return the best partial answer plus a clear
            # completion error in the response metadata.
            result = {
                "id": request_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": request.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": last_assistant_content,
                        },
                        "finish_reason": "length",
                    }
                ],
                "usage": None,
                "homelab_agent": {
                    "status": "loop_exhausted",
                    "error": "Agent loop exhausted before reaching a final answer",
                    "max_tool_steps": self._settings.max_tool_steps,
                },
            }
            _audit("loop_exhausted", response=result)
            return result
        except HTTPException as exc:
            _audit("error", error=str(exc.detail), status_code=exc.status_code)
            raise
