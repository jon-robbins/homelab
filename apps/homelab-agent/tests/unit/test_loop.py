from __future__ import annotations

from app.agent.tool_policy import filter_tool_specs


def test_filter_tool_specs_keeps_only_allowed_prefixes() -> None:
    tools = [
        {"name": "homelab_services_health", "description": "ok"},
        {"name": "shell_exec", "description": "bad"},
    ]

    filtered = filter_tool_specs(tools, allowlist=["homelab_services_health"])

    assert [tool["name"] for tool in filtered] == ["homelab_services_health"]
