"""3-way HOMELAB_MCP_ALLOWED_TOOLS semantics for make_register_tool.

Scenarios:
a) env unset -> unrestricted (all tools register, backwards compat)
b) env explicitly "" -> fail closed (zero tools registered, security failure mode)
c) env CSV trimmed -> allowlist only
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_mcp():
    return FastMCP("test")


def _registered_tool_names(mcp: FastMCP) -> set[str]:
    """Return tool names currently registered on the FastMCP instance.

    FastMCP exposes registered tools via the protocol list_tools handler;
    introspect the internal manager:
    """
    # FastMCP stores tools on mcp._tool_manager._tools dict keyed by name
    tools = getattr(mcp, "_tool_manager", None)
    if tools is None:
        return set()
    internal = getattr(tools, "_tools", None)
    if internal is None:
        return set()
    return set(internal.keys())


def _register_three(tool) -> None:
    """Register three trivial tools with the provided @tool decorator."""

    @tool("a", "- x int")
    def tool_a(x: int) -> int:
        return x

    @tool("b", "- y int")
    def tool_b(y: int) -> int:
        return y + 1

    @tool("c", "- z int")
    def tool_c(z: int) -> int:
        return z * 2


# ---------------------------------------------------------------------------
# Scenario (a): env UNSET = unrestricted (backwards compat for primary homelab-mcp)
# ---------------------------------------------------------------------------


def test_allowlist_env_unset_registers_all(monkeypatch) -> None:
    monkeypatch.delenv("HOMELAB_MCP_ALLOWED_TOOLS", raising=False)
    # Force module reimport so allowlist env is freshly read with env unset state
    import importlib
    import homelab_mcp.mcp_schema as mod
    importlib.reload(mod)
    mcp = _fresh_mcp()
    tool = mod.make_register_tool(mcp)
    _register_three(tool)
    names = _registered_tool_names(mcp)
    assert names == {"tool_a", "tool_b", "tool_c"}, names


# ---------------------------------------------------------------------------
# Scenario (b): env EXPLICITLY EMPTY string -> fail closed, zero tools registered
# ---------------------------------------------------------------------------


def test_allowlist_env_empty_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("HOMELAB_MCP_ALLOWED_TOOLS", "")
    import importlib
    import homelab_mcp.mcp_schema as mod
    importlib.reload(mod)
    mcp = _fresh_mcp()
    tool = mod.make_register_tool(mcp)
    _register_three(tool)
    names = _registered_tool_names(mcp)
    assert names == set(), names  # FAIL CLOSED: no tools at all


# ---------------------------------------------------------------------------
# Scenario (c): env CSV with whitespace -> trimmed allowlist only
# ---------------------------------------------------------------------------


def test_allowlist_env_csv_trimmed_only(monkeypatch) -> None:
    monkeypatch.setenv("HOMELAB_MCP_ALLOWED_TOOLS", "tool_a, tool_b")  # note leading space on tool_b
    import importlib
    import homelab_mcp.mcp_schema as mod
    importlib.reload(mod)
    mcp = _fresh_mcp()
    tool = mod.make_register_tool(mcp)

    # register FOUR tools (a,b,c,d) — only a and b should survive the filter
    @tool("a", "- x")
    def tool_a(x: int) -> int: return x

    @tool("b", "- y")
    def tool_b(y: int) -> int: return y

    @tool("c", "- z")
    def tool_c(z: int) -> int: return z

    @tool("d", "- w")
    def tool_d(w: int) -> int: return w

    names = _registered_tool_names(mcp)
    assert names == {"tool_a", "tool_b"}, names
    assert "tool_c" not in names
    assert "tool_d" not in names
