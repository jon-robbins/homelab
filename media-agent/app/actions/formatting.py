"""Default conversational formatter for action results.

Handlers may call :func:`default_format_response` from their
``format_response`` method or override entirely. The strings here mirror the
legacy logic in ``app.router.formatting.format_router_response`` and must
stay byte-stable to keep the existing tests green.
"""

from __future__ import annotations

from typing import Any

_OPTIONS_ACTIONS = frozenset(
    {"download_options_tv", "download_options_movie", "indexer_search"}
)
_GRAB_ACTIONS = frozenset({"download_grab_tv", "download_grab_movie", "indexer_grab"})


def format_options_table(tool_result: dict[str, Any]) -> str:
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


def format_search_results(tool_result: dict[str, Any]) -> str:
    results = tool_result.get("results") or []
    if not isinstance(results, list) or not results:
        return "I couldn't find a matching title in your library tools."
    rows: list[str] = []
    for i, r in enumerate(results[:5], start=1):
        title = str(r.get("title") or "Unknown")
        year = r.get("year")
        rows.append(f"{i}. {title}{f' ({year})' if year else ''}")
    return "I found these matches:\n\n" + "\n".join(rows)


def format_grab_ack() -> str:
    return "OK! It's downloading."


def format_action_error(tool_result: dict[str, Any]) -> str:
    err = tool_result.get("error") or {}
    code = err.get("code", "ACTION_FAILED")
    msg = err.get("message", "request failed")
    return f"I couldn't complete that action yet ({code}): {msg}"


def default_format_response(action_name: str, tool_result: dict[str, Any]) -> str:
    """Common conversational formatter, lifted from router/formatting.py."""
    if tool_result.get("ok") is not True:
        return format_action_error(tool_result)
    if action_name in _OPTIONS_ACTIONS:
        return format_options_table(tool_result)
    if action_name in _GRAB_ACTIONS:
        return format_grab_ack()
    if action_name == "search":
        return format_search_results(tool_result)
    return "Action completed."


__all__ = [
    "default_format_response",
    "format_options_table",
    "format_search_results",
    "format_grab_ack",
    "format_action_error",
]
