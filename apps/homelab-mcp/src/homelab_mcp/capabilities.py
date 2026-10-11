"""Server-side capability and role policy loaded from the shared role artifact."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PERMITTED_CAPABILITIES = frozenset(
    {
        "read",
        "trusted-acquisition",
        "trusted-single-control",
        "owner-mutation",
        "owner-admin",
    }
)
TRUSTED_ALLOWED_CAPABILITIES = frozenset(
    {"read", "trusted-acquisition", "trusted-single-control"}
)


def role_tool_artifact_path() -> Path:
    """Locate the public package's base role artifact."""
    return Path(__file__).with_name("mcp-role-tools.json")


def _load_role_tool_artifact() -> dict[str, Any]:
    artifact_path = role_tool_artifact_path()
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid MCP role tool artifact at {artifact_path}") from exc
    if not isinstance(artifact, dict) or set(artifact) != {"capabilities", "roles"}:
        raise RuntimeError("MCP role tool artifact must contain capabilities and roles")

    capabilities = artifact["capabilities"]
    roles = artifact["roles"]
    if not isinstance(capabilities, dict) or not isinstance(roles, dict):
        raise RuntimeError("MCP role tool artifact has invalid sections")
    if set(roles) != {"trusted", "owner"}:
        raise RuntimeError("MCP role tool artifact must define trusted and owner roles")
    if not all(isinstance(name, str) and isinstance(capability, str) for name, capability in capabilities.items()):
        raise RuntimeError("MCP role tool artifact has invalid capabilities")
    invalid_capabilities = set(capabilities.values()) - PERMITTED_CAPABILITIES
    if invalid_capabilities:
        raise RuntimeError(
            "MCP role tool artifact has invalid capability values: "
            + ", ".join(sorted(invalid_capabilities))
        )
    if not all(
        isinstance(tools, list)
        and all(isinstance(name, str) for name in tools)
        and len(tools) == len(set(tools))
        and tools == sorted(tools)
        for tools in roles.values()
    ):
        raise RuntimeError("MCP role tool artifact has invalid role tools")
    if not set(roles["trusted"]) < set(roles["owner"]):
        raise RuntimeError("trusted role tools must be a strict owner subset")
    if set(roles["owner"]) != set(capabilities):
        raise RuntimeError("owner role tools must exactly match classified tools")
    trusted_capabilities = {capabilities[name] for name in roles["trusted"]}
    disallowed_trusted = trusted_capabilities - TRUSTED_ALLOWED_CAPABILITIES
    if disallowed_trusted:
        raise RuntimeError(
            "trusted role has disallowed capabilities: "
            + ", ".join(sorted(disallowed_trusted))
        )
    return artifact


_ROLE_TOOL_ARTIFACT = _load_role_tool_artifact()
TOOL_CAPABILITIES: dict[str, str] = dict(_ROLE_TOOL_ARTIFACT["capabilities"])
CAPABILITIES = frozenset(TOOL_CAPABILITIES.values())
TRUSTED_CAPABILITIES = frozenset(
    TOOL_CAPABILITIES[name] for name in _ROLE_TOOL_ARTIFACT["roles"]["trusted"]
)
OWNER_CAPABILITIES = frozenset(
    TOOL_CAPABILITIES[name] for name in _ROLE_TOOL_ARTIFACT["roles"]["owner"]
)


def capability_for(
    tool_name: str, extension_roles: dict[str, tuple[str, str]] | None = None
) -> str:
    """Return a tool's capability, raising for unknown names."""
    try:
        if extension_roles and tool_name in extension_roles:
            return extension_roles[tool_name][0]
        return TOOL_CAPABILITIES[tool_name]
    except KeyError as exc:
        raise KeyError(f"unclassified MCP tool: {tool_name}") from exc


def role_tool_registry(
    extension_roles: dict[str, tuple[str, str]] | None = None
) -> dict[str, set[str]]:
    """Return base role grants plus explicitly enabled extension grants."""
    roles = {
        role: set(tools)
        for role, tools in _ROLE_TOOL_ARTIFACT["roles"].items()
    }
    for name, (capability, role) in (extension_roles or {}).items():
        if name in TOOL_CAPABILITIES or capability not in PERMITTED_CAPABILITIES or role not in roles:
            raise ValueError(f"invalid extension tool role: {name}")
        if role == "trusted" and capability not in TRUSTED_ALLOWED_CAPABILITIES:
            raise ValueError(f"invalid trusted extension capability: {name}")
        roles["owner"].add(name)
        if role == "trusted":
            roles["trusted"].add(name)
    return roles


def allowed_tools(
    profile: str,
    registered: set[str],
    extension_roles: dict[str, tuple[str, str]] | None = None,
) -> set[str]:
    """Filter registered tools by the artifact's role grant; unknown names deny."""
    if profile == "trusted":
        granted = role_tool_registry(extension_roles)["trusted"]
    elif profile in {"full", "owner"}:
        granted = role_tool_registry(extension_roles)["owner"]
    else:
        raise ValueError(f"unknown MCP tool profile: {profile}")
    return registered & granted


def profile_for_role(role: str) -> str:
    """Map deployment role names to MCP registration profiles."""
    if role == "trusted":
        return "trusted"
    if role == "owner":
        return "full"
    raise ValueError(f"unknown Telegram role: {role}")


def qwen_role_tool_registry() -> dict[str, Any]:
    """Return the exact artifact Qwen receives at build time."""
    return _ROLE_TOOL_ARTIFACT
