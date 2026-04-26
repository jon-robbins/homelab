"""Conversational formatter for the /router endpoint.

Delegates to per-action handler ``format_response`` methods registered in
:mod:`app.actions.registry`. Falls back to the default formatter when an
unknown action name is encountered, so misrouted payloads still render
something sensible.
"""

from __future__ import annotations

from typing import Any

from ..actions import registry
from ..actions.formatting import default_format_response


def format_router_response(
    action_payload: dict[str, Any], tool_result: dict[str, Any]
) -> str:
    name = str(action_payload.get("action") or "")
    if not registry.has(name):
        return default_format_response(name, tool_result)
    handler = registry.get(name)
    try:
        args = handler.args_model.model_validate(action_payload)
    except Exception:  # noqa: BLE001 — fall back if payload doesn't validate
        return default_format_response(name, tool_result)
    return handler.format_response(args, tool_result)


__all__ = ["format_router_response"]
