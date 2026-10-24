"""Capability reports reflect the tools in the running MCP server."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys



_PROBE = """
import asyncio
import json

from homelab_mcp.server import homelab_mcp_capabilities, mcp

registered = {tool.name for tool in asyncio.run(mcp.list_tools())}
reported = json.loads(homelab_mcp_capabilities())["data"]["tools"]
enabled = {entry["name"] for entry in reported if entry["enabled"]}
assert enabled == registered, (sorted(enabled - registered), sorted(registered - enabled))
assert all(entry.get("capability") for entry in reported), [
    entry["name"] for entry in reported if not entry.get("capability")
]
print(json.dumps({"registered": sorted(registered), "reported": reported}))
"""


def _probe(**overrides: str) -> dict[str, object]:
    env = os.environ.copy()
    for name in (
        "HOMELAB_MCP_EXTENSIONS",
        "HOMELAB_MCP_TOOL_PROFILE",
        "HOMELAB_MCP_ALLOWED_TOOLS",
        "HOMELAB_MCP_ENABLE_MUTATIONS",
    ):
        env.pop(name, None)
    env.update(overrides)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=Path(__file__).parents[5],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    import json

    return json.loads(result.stdout)


def test_public_only_startup_reports_exact_registered_set() -> None:
    result = _probe()
    names = set(result["registered"])
    assert "homelab_mcp_capabilities" in names
    assert "plex_library_search" in names
    assert "homelab_compose_service_catalog" not in names


def test_owner_allowlist_limits_reported_enabled_tools() -> None:
    result = _probe(
        HOMELAB_MCP_TOOL_PROFILE="owner",
        HOMELAB_MCP_ALLOWED_TOOLS="homelab_mcp_capabilities,qbt_transfer_summary,qbt_torrents_resume",
        HOMELAB_MCP_ENABLE_MUTATIONS="1",
    )
    assert set(result["registered"]) == {
        "homelab_mcp_capabilities", "qbt_transfer_summary", "qbt_torrents_resume"
    }


def test_mutations_disabled_removes_public_mutations_from_enabled_tools() -> None:
    result = _probe(HOMELAB_MCP_ENABLE_MUTATIONS="0")
    names = set(result["registered"])
    assert "qbt_torrents_resume" not in names
    assert "homelab_mcp_capabilities" in names
