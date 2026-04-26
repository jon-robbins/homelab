from __future__ import annotations

from typing import Any

import httpx

from ..config import Settings


def chat(
    client: httpx.Client,
    s: Settings,
    messages: list[dict[str, str]],
    format_schema: dict[str, Any],
) -> httpx.Response:
    """POST /api/chat on the configured Ollama endpoint.

    The timeout mirrors the prior caller-side max(10, prowlarr_timeout) so the
    router parser keeps its long-poll behavior for slow local models.
    """
    return client.post(
        f"{s.ollama_base}/api/chat",
        json={
            "model": s.router_model,
            "stream": False,
            "format": format_schema,
            "messages": messages,
            "options": {"temperature": 0},
        },
        timeout=max(10.0, s.prowlarr_search_timeout_s),
    )
