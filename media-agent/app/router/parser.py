from __future__ import annotations

import json
import re
from typing import Any

import httpx
from pydantic import ValidationError

from ..core.action_catalog import ROUTER_ACTION_NAMES
from ..core.models import ACTION_CALL_ADAPTER, RouterIntentDecision
from .router_selection import parse_selection_choice

ROUTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "action": {"type": "string", "enum": list(ROUTER_ACTION_NAMES)},
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
    },
    "required": ["action"],
}


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
    allowed_by_action: dict[str, set[str]] = {
        "search": {"action", "type", "query"},
        "download_options_tv": {
            "action",
            "query",
            "season",
            "series_id",
            "include_full_series_packs",
        },
        "download_options_movie": {"action", "query", "movie_id"},
        "download_grab_tv": {"action", "guid", "episode_id"},
        "download_grab_movie": {"action", "guid", "movie_id"},
        "indexer_search": {"action", "query", "limit", "search_type"},
        "indexer_grab": {"action", "release"},
    }
    allowed = allowed_by_action.get(action)
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
            raise ValueError("parser returned no JSON object")
        obj = json.loads(t[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("parser output must be JSON object")
    return obj


def classify_intent(user_message: str, has_session_state: bool) -> RouterIntentDecision:
    selection = parse_selection_choice(user_message)
    if has_session_state and selection is not None:
        return RouterIntentDecision(
            intent="selection", reason="pending session has deterministic selection"
        )
    lower = (user_message or "").lower()
    media_words = (
        "download",
        "get ",
        "grab ",
        "movie",
        "show",
        "season",
        "episode",
        "indexer",
        "torrent",
    )
    if any(w in lower for w in media_words):
        return RouterIntentDecision(intent="download", reason="matched media keywords")
    return RouterIntentDecision(intent="non_media", reason="no media keywords matched")


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
                "You are a strict API action parser. Return exactly one JSON object with action and args. "
                "Allowed actions: search, download_options_tv, download_options_movie, "
                "download_grab_tv, download_grab_movie, indexer_search, indexer_grab. "
                "Never return prose, markdown, placeholders, or URLs."
            ),
        },
        {"role": "user", "content": user_message},
    ]
    last_error = "unknown"
    last_content = ""
    for _ in range(max(1, int(s.router_max_retries))):
        r = http.post(
            f"{s.ollama_base}/api/chat",
            json={
                "model": s.router_model,
                "stream": False,
                "format": ROUTER_SCHEMA,
                "messages": messages,
                "options": {"temperature": 0},
            },
            timeout=max(10.0, s.prowlarr_search_timeout_s),
        )
        r.raise_for_status()
        j = r.json()
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
            messages.append(
                {
                    "role": "system",
                    "content": f"INVALID FORMAT. Output one valid action object only. Last error: {last_error}",
                }
            )
    fallback = heuristic_action_from_message(user_message)
    if fallback is not None:
        action_obj = ACTION_CALL_ADAPTER.validate_python(fallback)
        return action_obj.model_dump()
    preview = (last_content or "")[:180].replace("\n", " ")
    raise ValueError(f"router parser failed: {last_error}; raw={preview!r}")
