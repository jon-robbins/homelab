from __future__ import annotations

import inspect
import json
import pathlib

from homelab_mcp.server import mcp

out: list[dict[str, object]] = []
tools = getattr(mcp, "_tool_manager", None)
if tools and hasattr(tools, "_tools"):
    for name, entry in sorted(tools._tools.items()):
        fn = entry.fn
        sig = inspect.signature(fn)
        params: list[str] = []
        for pname, param in sig.parameters.items():
            if pname.startswith("_"):
                continue
            params.append(pname)
        desc = entry.description or getattr(fn, "__doc__", "") or ""
        out.append(
            {
                "name": name,
                "description": desc,
                "params": params,
            }
        )

target = pathlib.Path("../../tests/fixtures/homelab_mcp/mcp_tools_fixture.json")
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps({"tools": out}, indent=2) + "\n", encoding="utf-8")
print(len(out), "tools written to", target)
