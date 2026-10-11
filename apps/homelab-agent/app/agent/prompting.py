from __future__ import annotations

from collections.abc import Iterable


def build_system_prompt(tool_names: Iterable[str]) -> str:
    tools = ", ".join(sorted(tool_names))
    return (
        "You are Homelab Agent. Use the provided tools to inspect and operate the homelab. "
        "Call a tool only when it is needed, keep arguments minimal, and stop when you can "
        f"answer directly. Available tools: {tools}."
    )


def normalize_tool_spec(spec: dict[str, object]) -> dict[str, object]:
    function = {
        "name": str(spec["name"]),
        "description": str(spec.get("description", "")),
        "parameters": spec.get(
            "inputSchema",
            {"type": "object", "properties": {}, "additionalProperties": True},
        ),
    }
    return {"type": "function", "function": function}
