#!/usr/bin/env python3
"""Strict two-phase media router: parse -> execute -> format."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
MEDIA_AGENT_APP_PATH = REPO_ROOT / "media-agent"
if str(MEDIA_AGENT_APP_PATH) not in sys.path:
    sys.path.insert(0, str(MEDIA_AGENT_APP_PATH))

from app.core.models import ACTION_CALL_ADAPTER  # noqa: E402

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b-instruct-q8_0")
DEFAULT_LLM_PROVIDER = os.environ.get("MEDIA_AGENT_LLM_PROVIDER", "gemini")
DEFAULT_GEMINI_MODEL = os.environ.get("MEDIA_AGENT_ROUTER_MODEL", "gemini-2.5-flash")
_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

PARSER_SYSTEM = (
    "You are a strict API action parser. "
    "Return exactly one JSON object with an 'action' key and its arguments. "
    "Allowed actions: search, download_options_tv, download_options_movie, "
    "download_grab_tv, download_grab_movie, indexer_search, indexer_grab. "
    "Never return prose, markdown, placeholders, or URLs."
)

ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "search",
                "download_options_tv",
                "download_options_movie",
                "download_grab_tv",
                "download_grab_movie",
                "indexer_search",
                "indexer_grab",
            ],
        },
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


def _http_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    body: bytes | None = None
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url=url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        text = (e.read() or b"").decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} for {url}: {text[:400]}") from e
    except TimeoutError as e:
        raise RuntimeError(f"request timed out for {url}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"request failed for {url}: {e.reason}") from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"non-JSON response from {url}: {raw[:400]}") from e


def _extract_json_object(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end < start:
            raise RuntimeError("parser returned no JSON object")
        obj = json.loads(raw[start : end + 1])
    if not isinstance(obj, dict):
        raise RuntimeError("parser output must be a JSON object")
    return obj


def _normalize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    if "action" in candidate:
        out = dict(candidate)
    else:
        out = candidate
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
        if src in out:
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


def _convert_nullable_types(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert Ollama-style nullable types to Gemini format."""
    import copy

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


def _parse_action_gemini(
    user_message: str,
    *,
    model: str = DEFAULT_GEMINI_MODEL,
    gemini_api_key: str,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Parse using Google Gemini REST API."""
    gemini_schema = _convert_nullable_types(ACTION_SCHEMA)
    contents = [{"role": "user", "parts": [{"text": user_message}]}]
    last_error = "unknown parser failure"
    last_content = ""
    for _ in range(max_retries):
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": PARSER_SYSTEM}]},
            "contents": contents,
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": gemini_schema,
            },
        }
        resp = _http_json(
            "POST",
            f"{_GEMINI_BASE}/{model}:generateContent?key={gemini_api_key}",
            body,
            timeout=60,
        )
        parts = (resp.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        content = str(parts[0].get("text") or "").strip() if parts else ""
        last_content = content
        if not content:
            last_error = "empty parser content"
            contents.append({"role": "model", "parts": [{"text": ""}]})
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": "Previous output was empty. Output one valid JSON object with action + args only."
                        }
                    ],
                }
            )
            continue
        try:
            candidate = _normalize_candidate(_extract_json_object(content))
            action_obj = ACTION_CALL_ADAPTER.validate_python(candidate)
            return action_obj.model_dump()
        except (RuntimeError, ValidationError) as e:
            if isinstance(e, ValidationError):
                short = "; ".join(x["msg"] for x in e.errors()[:3])
                last_error = f"schema validation failed: {short}"
            else:
                last_error = str(e)
            contents.append({"role": "model", "parts": [{"text": content}]})
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "INVALID FORMAT. You must output exactly one JSON object matching the action schema. "
                                f"Previous error: {last_error}"
                            )
                        }
                    ],
                }
            )
    preview = (last_content or "")[:300].replace("\n", " ")
    raise RuntimeError(
        f"parser produced invalid action payload: {last_error}; raw={preview!r}"
    )


def parse_action(
    user_message: str,
    *,
    model: str = DEFAULT_OLLAMA_MODEL,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    max_retries: int = 3,
) -> dict[str, Any]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": PARSER_SYSTEM},
        {"role": "user", "content": user_message},
    ]
    last_error = "unknown parser failure"
    last_content = ""
    for _ in range(max_retries):
        payload = {
            "model": model,
            "stream": False,
            "format": ACTION_SCHEMA,
            "messages": messages,
            "options": {"temperature": 0},
        }
        resp = _http_json("POST", f"{ollama_url.rstrip('/')}/api/chat", payload, timeout=60)
        content = ((resp.get("message") or {}).get("content") or "").strip()
        last_content = content
        if not content:
            last_error = "empty parser content"
            messages.append(
                {
                    "role": "system",
                    "content": "Previous output was empty. Output one valid JSON object with action + args only.",
                }
            )
            continue
        try:
            candidate = _normalize_candidate(_extract_json_object(content))
            action_obj = ACTION_CALL_ADAPTER.validate_python(candidate)
            return action_obj.model_dump()
        except (RuntimeError, ValidationError) as e:
            if isinstance(e, ValidationError):
                short = "; ".join(x["msg"] for x in e.errors()[:3])
                last_error = f"schema validation failed: {short}"
            else:
                last_error = str(e)
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "INVALID FORMAT. You must output exactly one JSON object matching the action schema. "
                        f"Previous error: {last_error}"
                    ),
                }
            )
    preview = (last_content or "")[:300].replace("\n", " ")
    raise RuntimeError(f"parser produced invalid action payload: {last_error}; raw={preview!r}")


def execute_action(
    action_payload: dict[str, Any],
    *,
    media_agent_url: str,
    media_agent_token: str,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {media_agent_token}"}
    return _http_json(
        "POST",
        f"{media_agent_url.rstrip('/')}/internal/media-agent/v1/action",
        action_payload,
        headers=headers,
        timeout=90,
    )


def _format_options(options: list[dict[str, Any]], max_rows: int = 10) -> str:
    rows: list[str] = []
    for i, op in enumerate(options[:max_rows], start=1):
        title = str(op.get("title") or "(untitled)")
        seeders = op.get("seeders", "?")
        leechers = op.get("leechers", "?")
        size = str(op.get("size_human") or op.get("size") or "?")
        indexer = str(op.get("indexer") or "?")
        rows.append(f"{i}. {title} — seeders {seeders}, leechers {leechers}, size {size}, {indexer}")
    return "\n".join(rows)


def format_response(action_payload: dict[str, Any], tool_result: dict[str, Any]) -> str:
    if tool_result.get("ok") is not True:
        err = tool_result.get("error") or {}
        code = err.get("code", "ACTION_FAILED")
        msg = err.get("message", "request failed")
        return f"I couldn't complete that action yet ({code}): {msg}"

    action = str(action_payload.get("action") or "")
    if action in {"download_options_tv", "download_options_movie", "indexer_search"}:
        options = tool_result.get("options") or []
        if not isinstance(options, list) or not options:
            return "I checked, but there are no release options right now."
        return "Got it! Here are some options\n\n" + _format_options(options)

    if action in {"download_grab_tv", "download_grab_movie", "indexer_grab"}:
        return "OK! It's downloading."

    if action == "search":
        results = tool_result.get("results") or []
        if not isinstance(results, list) or not results:
            return "I couldn't find a matching title in your library tools."
        top = results[:5]
        lines = []
        for i, r in enumerate(top, start=1):
            title = str(r.get("title") or "Unknown")
            year = r.get("year")
            suffix = f" ({year})" if year else ""
            lines.append(f"{i}. {title}{suffix}")
        return "I found these matches:\n\n" + "\n".join(lines)

    return "Action completed."


def run_router(
    user_message: str,
    *,
    provider: str = DEFAULT_LLM_PROVIDER,
    model: str | None = None,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    gemini_api_key: str = "",
    media_agent_url: str,
    media_agent_token: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    if provider == "gemini":
        action_payload = _parse_action_gemini(
            user_message, model=model or DEFAULT_GEMINI_MODEL, gemini_api_key=gemini_api_key
        )
    else:
        action_payload = parse_action(
            user_message, model=model or DEFAULT_OLLAMA_MODEL, ollama_url=ollama_url
        )
    tool_result = execute_action(
        action_payload,
        media_agent_url=media_agent_url,
        media_agent_token=media_agent_token,
    )
    final_text = format_response(action_payload, tool_result)
    return action_payload, tool_result, final_text


def main() -> int:
    ap = argparse.ArgumentParser(description="Strict media action router")
    ap.add_argument("message", help="User media request text")
    ap.add_argument("--provider", default=DEFAULT_LLM_PROVIDER, choices=["gemini", "ollama"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL))
    ap.add_argument("--gemini-api-key", default=os.environ.get("GEMINI_API_KEY", ""))
    ap.add_argument("--media-agent-url", default=os.environ.get("MEDIA_AGENT_URL", ""))
    ap.add_argument("--media-agent-token", default=os.environ.get("MEDIA_AGENT_TOKEN", ""))
    ap.add_argument("--debug-json", action="store_true", help="Print parser/action/result JSON")
    args = ap.parse_args()

    if not args.media_agent_url or not args.media_agent_token:
        print("MEDIA_AGENT_URL and MEDIA_AGENT_TOKEN are required (flag or env).", file=sys.stderr)
        return 2
    if args.provider == "gemini" and not args.gemini_api_key:
        print("GEMINI_API_KEY is required when using gemini provider (flag or env).", file=sys.stderr)
        return 2
    try:
        action_payload, tool_result, final_text = run_router(
            args.message,
            provider=args.provider,
            model=args.model,
            ollama_url=args.ollama_url,
            gemini_api_key=args.gemini_api_key,
            media_agent_url=args.media_agent_url,
            media_agent_token=args.media_agent_token,
        )
    except RuntimeError as e:
        print(f"router error: {e}", file=sys.stderr)
        return 1
    if args.debug_json:
        print(
            json.dumps(
                {
                    "action_payload": action_payload,
                    "tool_result": tool_result,
                    "final_text": final_text,
                },
                indent=2,
            )
        )
    else:
        print(final_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
