from __future__ import annotations

from typing import Any


def format_router_response(action_payload: dict[str, Any], tool_result: dict[str, Any]) -> str:
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
        rows: list[str] = []
        for i, op in enumerate(options[:10], start=1):
            title = str(op.get("title") or "(untitled)")
            seeders = op.get("seeders", "?")
            leechers = op.get("leechers", "?")
            size = str(op.get("size_human") or op.get("size") or "?")
            indexer = str(op.get("indexer") or "?")
            rows.append(
                f"{i}. {title} - seeders {seeders}, leechers {leechers}, size {size}, {indexer}"
            )
        return "Got it! Here are some options\n\n" + "\n".join(rows)
    if action in {"download_grab_tv", "download_grab_movie", "indexer_grab"}:
        return "OK! It's downloading."
    if action == "search":
        results = tool_result.get("results") or []
        if not isinstance(results, list) or not results:
            return "I couldn't find a matching title in your library tools."
        rows = []
        for i, r in enumerate(results[:5], start=1):
            title = str(r.get("title") or "Unknown")
            year = r.get("year")
            rows.append(f"{i}. {title}{f' ({year})' if year else ''}")
        return "I found these matches:\n\n" + "\n".join(rows)
    return "Action completed."
