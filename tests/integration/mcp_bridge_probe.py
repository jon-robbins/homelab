#!/usr/bin/env python3
"""T2 probe — agent→MCP bridge verification (read-only, no GPU).

Exercises the homelab-agent's own MCP client against the live homelab-mcp:
handshake, tools/list, OpenAI schema conversion, and one read-only
homelab_services_health call. No model/qwen-serve involvement.

Also dumps the normalized live schemas to
tests/integration/\x65vidence/mcp_tools_fixture.json — committed, and consumed
by the T1 probe (tool_call_probe.py) as its primary schema source.

Usage (worktree root):
    set -a; . /homelab/.env; set +a
    uv run --project apps/homelab-agent \\
      python tests/integration/mcp_bridge_probe.py \\
      --mcp-url "http://${TAILSCALE_IP:-127.0.0.1}:${HOMELAB_MCP_PORT:-8090}" \\
      --api-key "$MCP_API_TOKEN"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "homelab-agent"))

from app.agent.prompting import normalize_tool_spec  # noqa: E402
from app.clients.mcp import McpClient  # noqa: E402

FIXTURE_DEFAULT = Path(__file__).resolve().parent / "evidence" / "mcp_tools_fixture.json"

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--fixture-out", default=str(FIXTURE_DEFAULT),
                        help="where to dump the normalized schemas for the T1 probe")
    args = parser.parse_args()

    client = McpClient(args.mcp_url, args.api_key)
    tools = client.list_tools()
    check("handshake + tools/list", len(tools) >= 1, f"{len(tools)} tools")

    normalized = [normalize_tool_spec(spec) for spec in tools]
    converted = 0
    for openai_tool in normalized:
        function = openai_tool.get("function", {})
        if openai_tool.get("type") == "function" and function.get("name") and "parameters" in function:
            converted += 1
        else:
            check(f"schema conversion: {function.get('name', '<unknown>')}", False)
    check("all MCP schemas convert to OpenAI form", converted == len(tools) and len(tools) > 0,
          f"{converted}/{len(tools)}")

    names = {(t.get("function") or {}).get("name") for t in normalized}

    # Dump the fixture the T1 probe consumes as its primary schema source.
    fixture_path = Path(args.fixture_out)
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n")
    fixture_data = json.loads(fixture_path.read_text())
    check("fixture dump for T1", isinstance(fixture_data, list) and len(fixture_data) == len(normalized),
          f"{fixture_path} ({len(normalized)} tools)")
    check("homelab_services_health present", "homelab_services_health" in names)
    check("homelab_mcp_capabilities present", "homelab_mcp_capabilities" in names)

    if "homelab_services_health" in names:
        result = client.call_tool("homelab_services_health", {})
        snippet = (result or "").strip()[:120].replace("\n", " ")
        check("read-only homelab_services_health call", bool(result and result.strip()), snippet)

    if FAILURES:
        print(f"mcp_bridge_probe: {len(FAILURES)} failure(s): {FAILURES}")
        return 1
    print("mcp_bridge_probe: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
