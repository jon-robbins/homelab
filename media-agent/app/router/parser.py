"""Strict LLM-driven parser that turns a user message into an ``ActionCall``.

The schema and the per-action allowed-fields map are both derived from the
registered handlers, so adding a new action under ``app/actions/`` is enough
to make it parseable here.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from pydantic import ValidationError

from ..integrations.gemini import chat as _gemini_chat
from ..integrations.ollama import chat as _ollama_chat
from ..models.actions import ACTION_CALL_ADAPTER
from ..models.router import RouterIntentDecision  # noqa: F401  re-export-friendly
from .intent import (
    classify_intent,  # noqa: F401  re-export-friendly
    parse_selection_choice,  # noqa: F401  re-export-friendly
)


def _build_router_schema() -> dict[str, Any]:
    from ..actions import registry

    properties: dict[str, Any] = {
        "action": {"type": "string", "enum": list(registry.router_emittable_names())},
        "type": {"type": "string", "enum": ["tv", "movie"]},
        "query": {"type": "string"},
        "season": {"type": "integer"},
        "series_id": {"type": ["integer", "null"]},
        "movie_id": {"type": ["integer", "null"]},
        "guid": {"type": "string"},
        "release": {"type": "object"},
        "limit": {"type": "integer"},
        "search_type": {"type": "string"},
        "include_full_series_packs": {"type": "boolean"},
        "episode_id": {"type": "integer"},
    }
    return {
        "type": "object",
        "additionalProperties": True,
        "properties": properties,
        "required": ["action"],
    }


def _allowed_fields_by_action() -> dict[str, set[str]]:
    from ..actions import registry

    return {
        h.name: set(h.args_model.model_fields.keys()) | {"action"}
        for h in registry.all_handlers()
    }


# Cached views; rebuilt lazily so adding a handler at import time picks up.
_ROUTER_SCHEMA: dict[str, Any] | None = None
_ALLOWED_FIELDS: dict[str, set[str]] | None = None


def _router_schema() -> dict[str, Any]:
    global _ROUTER_SCHEMA
    if _ROUTER_SCHEMA is None:
        _ROUTER_SCHEMA = _build_router_schema()
    return _ROUTER_SCHEMA


def _allowed_fields() -> dict[str, set[str]]:
    global _ALLOWED_FIELDS
    if _ALLOWED_FIELDS is None:
        _ALLOWED_FIELDS = _allowed_fields_by_action()
    return _ALLOWED_FIELDS


def normalize_router_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    out = dict(candidate)
    if "action" not in out:
        for key in ("payload", "call", "arguments", "tool_call", "action_payload"):
            inner = out.get(key)
            if isinstance(inner, dict) and "action" in inner:
                out = dict(inner)
                break
    alias_map = {
        "series_name": "query",
        "movie_name": "query",
        "title": "query",
        "season_number": "season",
        "seriesId": "series_id",
        "movieId": "movie_id",
        "episodeId": "episode_id",
    }
    for src, dst in alias_map.items():
        if src in out and dst not in out:
            out[dst] = out[src]
    for src in alias_map:
        out.pop(src, None)
    action = str(out.get("action") or "")
    allowed = _allowed_fields().get(action)
    if allowed:
        out = {k: v for k, v in out.items() if k in allowed}
    return out


def parse_json_object(text: str) -> dict[str, Any]:
    t = (text or "").strip()
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        start = t.find("{")
        end = t.rfind("}")
        if start < 0 or end < start:
            raise ValueError("parser returned no JSON object") from None
        obj = json.loads(t[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("parser output must be JSON object")
    return obj


def heuristic_action_from_message(user_message: str) -> dict[str, Any] | None:
    text = (user_message or "").strip()
    lower = text.lower()
    m = re.search(r"\bseason\s+(\d{1,2})\b", lower)
    if m:
        season = int(m.group(1))
        cleaned = re.sub(r"\bseason\s+\d{1,2}\b", "", text, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"^\s*(get|download|grab|find|search)\s+", "", cleaned, flags=re.IGNORECASE
        )
        cleaned = re.sub(r"[.!?]+$", "", cleaned).strip(" -:")
        cleaned = " ".join(cleaned.split())
        if len(cleaned) >= 2:
            return {
                "action": "indexer_search",
                "query": f"{cleaned} season {season}",
                "limit": 10,
                "search_type": "search",
            }
    return None


def parse_router_action(http: httpx.Client, s: Any, user_message: str) -> dict[str, Any]:
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You are a strict API action parser. Return exactly one JSON object "
                "with action and args. "
                "Allowed actions: search, download_options_tv, download_options_movie, "
                "download_grab_tv, download_grab_movie, indexer_search, indexer_grab. "
                "Never return prose, markdown, placeholders, or URLs."
            ),
        },
        {"role": "user", "content": user_message},
    ]
    last_error = "unknown"
    last_content = ""
    _chat = _gemini_chat if getattr(s, "llm_provider", "ollama") == "gemini" else _ollama_chat
    _is_gemini = _chat is _gemini_chat
    for _ in range(max(1, int(s.router_max_retries))):
        r = _chat(http, s, messages, _router_schema())
        r.raise_for_status()
        j = r.json()
        if _is_gemini:
            parts = (j.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
            content = str(parts[0].get("text") or "").strip() if parts else ""
        else:
            content = str((j.get("message") or {}).get("content") or "").strip()
        last_content = content
        if not content:
            last_error = "empty parser output"
            messages.append(
                {
                    "role": "system",
                    "content": "INVALID FORMAT. Output one JSON object only.",
                }
            )
            continue
        try:
            candidate = normalize_router_candidate(parse_json_object(content))
            action_obj = ACTION_CALL_ADAPTER.validate_python(candidate)
            return action_obj.model_dump()
        except (ValueError, ValidationError) as e:
            last_error = str(e)[:300]
            err_msg = (
                f"INVALID FORMAT. Output one valid action object only. "
                f"Last error: {last_error}"
            )
            messages.append({"role": "system", "content": err_msg})
    fallback = heuristic_action_from_message(user_message)
    if fallback is not None:
        action_obj = ACTION_CALL_ADAPTER.validate_python(fallback)
        return action_obj.model_dump()
    preview = (last_content or "")[:180].replace("\n", " ")
    raise ValueError(f"router parser failed: {last_error}; raw={preview!r}")


# Keep the legacy module-level name available for callers that still
# reference it (e.g. existing tests asserting the schema enum).
ROUTER_SCHEMA = _router_schema()


__all__ = [
    "ROUTER_SCHEMA",
    "normalize_router_candidate",
    "parse_json_object",
    "heuristic_action_from_message",
    "parse_router_action",
    "classify_intent",
    "parse_selection_choice",
]
