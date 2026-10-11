from __future__ import annotations

from collections.abc import Mapping

import httpx

from app.clients.http import post_with_retry


class ModelClient:
    def __init__(
        self,
        base_url: str,
        model_name: str,
        timeout_seconds: int = 120,
        api_key: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self._api_key = api_key
        self._client = httpx.Client(timeout=timeout_seconds)

    def chat(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        tool_choice: str | dict[str, object] | None = "auto",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model_name,
            "messages": messages,
            "stream": False,
        }
        # vLLM rejects `"tools": []` ("must not be an empty array"); only
        # advertise tools when at least one is available.
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else None
        response = post_with_retry(
            self._client, f"{self.base_url}/chat/completions", headers=headers, json=payload
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, Mapping):
            raise TypeError("Expected JSON object from model backend")
        return dict(data)
