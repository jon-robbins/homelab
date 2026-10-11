"""Read-only adapters for validating Telegram functional cases against MCP."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Iterable


_ROOT = Path(__file__).resolve().parents[2]
_ROLE_ARTIFACT = _ROOT / "apps/qwen-code-telegram/config/mcp-role-tools.json"
_SERVER = _ROOT / "apps/homelab-mcp/src/homelab_mcp/server.py"
_ROLE_MAP = {"user": "trusted", "admin": "owner"}


def load_role_registry() -> dict[str, object]:
    """Load the committed role artifact without importing runtime settings."""
    return json.loads(_ROLE_ARTIFACT.read_text(encoding="utf-8"))


def registered_tool_names() -> frozenset[str]:
    """Return all functions decorated with the MCP ``@tool`` decorator."""
    tree = ast.parse(_SERVER.read_text(encoding="utf-8"), filename=str(_SERVER))
    return frozenset(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "tool"
            for decorator in node.decorator_list
        )
    )


def capability_for_tool(tool: str) -> str:
    capabilities = load_role_registry()["capabilities"]
    if not isinstance(capabilities, dict) or tool not in capabilities:
        raise ValueError(f"tool is not classified by the MCP role artifact: {tool}")
    capability = capabilities[tool]
    if not isinstance(capability, str):
        raise ValueError(f"tool has invalid capability in the MCP role artifact: {tool}")
    return capability


def catalog_validation_errors(cases: Iterable[object]) -> tuple[str, ...]:
    """Report catalog drift from the canonical role artifact and MCP server."""
    registry = load_role_registry()
    capabilities = registry["capabilities"]
    roles = registry["roles"]
    if not isinstance(capabilities, dict) or not isinstance(roles, dict):
        return ("invalid_role_registry",)
    registered = registered_tool_names()
    errors: list[str] = []
    for case in cases:
        tool = getattr(case, "tool", None)
        capability = getattr(case, "capability", None)
        role = getattr(case, "role", None)
        name = getattr(case, "name", "<unnamed>")
        if not isinstance(tool, str) or not tool:
            errors.append(f"{name}: missing_tool")
            continue
        if tool not in registered:
            errors.append(f"{name}: unregistered_tool:{tool}")
        expected_capability = capabilities.get(tool)
        if capability != expected_capability:
            errors.append(f"{name}: capability_drift:{tool}")
        required_roles = ("trusted", "owner") if role == "both" else (_ROLE_MAP.get(role),)
        for required_role in required_roles:
            role_tools = roles.get(required_role, ())
            if not required_role or not isinstance(role_tools, list) or tool not in role_tools:
                errors.append(f"{name}: role_denied:{role}:{tool}")
    return tuple(errors)
