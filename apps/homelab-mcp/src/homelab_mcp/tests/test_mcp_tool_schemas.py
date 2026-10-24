from __future__ import annotations

import pytest

from homelab_mcp.server import mcp


def _registered_tools():
    return mcp._tool_manager.list_tools()


def test_all_tools_have_descriptions() -> None:
    tools = _registered_tools()
    assert len(tools) >= 15
    for registered in tools:
        assert registered.description.strip(), f"{registered.name} missing tool description"
        assert "Output data:" in registered.description, f"{registered.name} missing output section"
        assert "ok (bool)" in registered.description, f"{registered.name} missing envelope docs"


@pytest.mark.parametrize(
    "tool_name,param_name",
    [
        ("homelab_services_health", "services"),
        ("qbt_torrents_summary", "tag"),
        ("qbt_torrents_summary", "category"),
        ("qbt_torrents_list", "tag"),
        ("qbt_torrents_list", "state"),
        ("qbt_torrents_list", "limit"),
        ("qbt_torrents_list", "offset"),
        ("qbt_torrent_detail", "hash_prefix"),
        ("bazarr_missing_subtitles_summary", "scan_limit"),
        ("seerr_request_diagnosis", "request_id"),
        ("seerr_request_diagnosis", "lookback_hours"),
        ("seerr_request_diagnosis", "title"),
    ],
)
def test_parameter_descriptions(tool_name: str, param_name: str) -> None:
    registered = mcp._tool_manager.get_tool(tool_name)
    assert registered is not None, tool_name
    props = registered.parameters.get("properties", {})
    assert param_name in props, f"{tool_name} missing parameter {param_name}"
    desc = props[param_name].get("description", "")
    assert desc.strip(), f"{tool_name}.{param_name} missing description"


def test_limit_has_bounds_in_schema() -> None:
    registered = mcp._tool_manager.get_tool("qbt_torrents_list")
    assert registered is not None
    limit = registered.parameters["properties"]["limit"]
    assert limit.get("minimum") == 1
    assert limit.get("maximum") == 100
