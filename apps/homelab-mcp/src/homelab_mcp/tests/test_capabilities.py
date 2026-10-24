"""Capability registry and role-profile contracts."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP
import homelab_mcp.capabilities as capabilities_module

from homelab_mcp.capabilities import (
    CAPABILITIES,
    OWNER_CAPABILITIES,
    TRUSTED_CAPABILITIES,
    allowed_tools,
    capability_for,
    profile_for_role,
    qwen_role_tool_registry,
    role_tool_artifact_path,
    role_tool_registry,
)
from homelab_mcp.mcp_schema import make_register_tool


def registered_tools() -> set[str]:
    server_path = Path(__file__).parents[1] / "server.py"
    tree = ast.parse(server_path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Name)
            and dec.func.id == "tool"
            for dec in node.decorator_list
        )
    }


def qwen_role_tool_artifact() -> dict[str, object]:
    artifact_path = role_tool_artifact_path()
    return json.loads(artifact_path.read_text(encoding="utf-8"))


def test_every_server_tool_has_an_explicit_capability() -> None:
    tools = registered_tools()
    assert tools
    assert all(capability_for(name) in CAPABILITIES for name in tools)


def test_unknown_tools_fail_closed() -> None:
    with pytest.raises(KeyError):
        capability_for("not_a_registered_tool")


def test_trusted_is_strict_subset_when_mutation_tools_exist() -> None:
    registered = registered_tools()
    trusted = allowed_tools("trusted", registered)
    owner = allowed_tools("owner", registered)

    assert TRUSTED_CAPABILITIES < OWNER_CAPABILITIES
    assert trusted < owner
    assert trusted == {
        name for name in registered if {capability_for(name)} <= TRUSTED_CAPABILITIES
    }


def test_owner_equals_registered_classified_set() -> None:
    registered = registered_tools()
    assert allowed_tools("owner", registered) == registered


def test_qwen_role_artifact_covers_public_registry_profiles() -> None:
    registered = registered_tools()
    artifact = qwen_role_tool_artifact()
    roles = artifact["roles"]
    assert isinstance(roles, dict)

    assert set(artifact) == {"capabilities", "roles"}
    assert set(roles) == {"trusted", "owner"}
    assert artifact == qwen_role_tool_registry()
    assert allowed_tools("trusted", registered) <= set(roles["trusted"])
    assert allowed_tools("owner", registered) == registered
    assert registered <= set(artifact["capabilities"])
    assert len(roles["trusted"]) == len(set(roles["trusted"]))
    assert len(roles["owner"]) == len(set(roles["owner"]))


def test_mcp_loads_its_public_base_role_artifact() -> None:
    artifact_path = role_tool_artifact_path()
    assert artifact_path == (
        Path(__file__).parents[1] / "mcp-role-tools.json"
    )


def test_role_profiles_are_explicit() -> None:
    assert profile_for_role("trusted") == "trusted"
    assert profile_for_role("owner") == "full"
    with pytest.raises(ValueError):
        profile_for_role("unknown")


@pytest.mark.parametrize(
    ("profile", "role"),
    [("trusted", "trusted"), ("full", "owner")],
)
def test_mcp_registration_consumes_the_canonical_role_artifact(
    profile: str, role: str
) -> None:
    """The real decorator registration boundary uses the artifact's role grants."""
    mcp = FastMCP("test")
    tool = make_register_tool(mcp, profile=profile)
    artifact_tools = set().union(*role_tool_registry().values())

    for name in artifact_tools:
        def candidate() -> None:
            return None

        candidate.__name__ = name
        tool("test tool", "- none")(candidate)

    registered = set(mcp._tool_manager._tools)
    assert registered == role_tool_registry()[role]


def test_artifact_rejects_capability_outside_permitted_set(tmp_path, monkeypatch) -> None:
    artifact = qwen_role_tool_artifact()
    capabilities = dict(artifact["capabilities"])
    capabilities["arr_lookup"] = "not-permitted"
    artifact["capabilities"] = capabilities
    artifact_path = tmp_path / "mcp-role-tools.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    monkeypatch.setattr(capabilities_module, "role_tool_artifact_path", lambda: artifact_path)

    with pytest.raises(RuntimeError, match="invalid capability values"):
        capabilities_module._load_role_tool_artifact()


def test_artifact_rejects_trusted_grant_with_owner_only_capability(tmp_path, monkeypatch) -> None:
    artifact = qwen_role_tool_artifact()
    artifact["roles"]["trusted"].append("qbt_apply_profile")
    artifact["roles"]["trusted"].sort()
    artifact_path = tmp_path / "mcp-role-tools.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    monkeypatch.setattr(capabilities_module, "role_tool_artifact_path", lambda: artifact_path)

    with pytest.raises(RuntimeError, match="trusted role has disallowed capabilities"):
        capabilities_module._load_role_tool_artifact()
