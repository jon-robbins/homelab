from __future__ import annotations

import copy
from typing import Any

import httpx

from ..config import Settings

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"  # noqa: E501


def _convert_nullable_types(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert Ollama-style nullable types to Gemini-compatible format.

    Ollama accepts ``"type": ["integer", "null"]`` for nullable fields.
    Gemini's ``responseSchema`` does not; use ``nullable: true`` instead.
    Gemini also rejects ``additionalProperties``, so strip it.
    """
    schema = copy.deepcopy(schema)
    schema.pop("additionalProperties", None)
    for prop in schema.get("properties", {}).values():
        t = prop.get("type")
        if isinstance(t, list):
            non_null = [x for x in t if x != "null"]
            prop["type"] = non_null[0] if len(non_null) == 1 else "string"
            if "null" in t:
                prop["nullable"] = True
    return schema


def _to_gemini_messages(
    messages: list[dict[str, str]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Split messages into a Gemini systemInstruction and contents list.

    The first ``system`` role message becomes the ``systemInstruction``.
    Subsequent ``system`` messages (retry feedback) map to ``user`` role.
    ``assistant`` maps to ``model``.
    """
    system_instruction: dict[str, Any] | None = None
    contents: list[dict[str, Any]] = []
    seen_system = False

    for msg in messages:
        role = msg["role"]
        text = msg["content"]
        if role == "system" and not seen_system:
            system_instruction = {"parts": [{"text": text}]}
            seen_system = True
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": text}]})
        else:
            contents.append({"role": "user", "parts": [{"text": text}]})

    return system_instruction, contents


def chat(
    client: httpx.Client,
    s: Settings,
    messages: list[dict[str, str]],
    format_schema: dict[str, Any],
) -> httpx.Response:
    """POST generateContent on the Google Gemini API.

    Drop-in replacement for ``integrations.ollama.chat`` — same signature,
    different backend.  The caller in ``router/parser.py`` must extract
    content from the Gemini response format (``candidates[0]...text``)
    rather than the Ollama format (``message.content``).
    """
    system_instruction, contents = _to_gemini_messages(messages)
    gemini_schema = _convert_nullable_types(format_schema)

    body: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": gemini_schema,
        },
    }
    if system_instruction:
        body["systemInstruction"] = system_instruction

    return client.post(
        f"{_GEMINI_BASE}/{s.router_model}:generateContent?key={s.gemini_api_key}",
        json=body,
        timeout=max(10.0, s.prowlarr_search_timeout_s),
    )
