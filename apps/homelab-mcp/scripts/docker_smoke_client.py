"""Drive a running homelab-mcp over streamable HTTP and fail on any wrong answer.

Run inside the homelab-mcp image by docker-smoke.sh. SMOKE_EXTRA names an optional
file defining ``async def run(call)`` for checks an extension adds.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import time

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

BASE = sys.argv[1].rstrip("/")
TOKEN = sys.argv[2]
REQUIRED_TOOLS = {
    "media_files_list",
    "media_trash_list",
    "media_file_move",
    "media_file_delete",
    "media_trash_empty",
    "qbt_torrents_delete",
    "arr_item_delete",
    "seerr_request_action",
    "autobrr_pipeline_diagnosis",
}


def wait_healthy() -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{BASE}/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise SystemExit("homelab-mcp never became healthy")


def expect(condition: bool, label: str, detail: object = "") -> None:
    print(f"{'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        raise SystemExit(f"smoke failed: {label}: {detail}")


async def checks(session: ClientSession) -> None:
    async def call(name: str, **args: object) -> dict:
        result = await session.call_tool(name, args)
        return json.loads(result.content[0].text)

    tools = {tool.name for tool in (await session.list_tools()).tools}
    expect(REQUIRED_TOOLS <= tools, "mutation tools registered", REQUIRED_TOOLS - tools)

    listed = await call("media_files_list", path="/downloads")
    expect([e["name"] for e in listed["data"]["entries"]] == ["Film.mkv"], "list a root", listed)

    src, dest = "/downloads/Film.mkv", "/movies/Film (2020)/Film.mkv"
    refused = await call("media_file_move", src=src, dest=dest)
    expect(refused["error"]["code"] == "EXPECTED_ACTION_MISMATCH", "move needs expected_action")
    expected = f"file-move:{src}->{dest}"
    expect(expected in refused["error"]["hint"], "hint names the exact action", refused)
    moved = await call("media_file_move", src=src, dest=dest, expected_action=expected)
    expect(moved.get("ok") is True, "move applies", moved)
    in_movies = await call("media_files_list", path="/movies/Film (2020)")
    expect([e["name"] for e in in_movies["data"]["entries"]] == ["Film.mkv"], "file landed")

    escape = await call("media_file_move", src="/etc/passwd", dest="/movies/x", expected_action="x")
    expect(escape["error"]["code"] == "PATH_REJECTED", "paths outside roots refused")
    whole_root = await call("media_file_delete", path="/tv", expected_action="file-delete:/tv")
    expect(whole_root["error"]["code"] == "PATH_REJECTED", "a whole root cannot be deleted")

    deleted = await call(
        "media_file_delete", path="/tv/Show", expected_action="file-delete:/tv/Show"
    )
    expect(deleted.get("ok") is True, "delete moves to trash", deleted)
    trash = await call("media_trash_list")
    expect(trash["data"]["count"] == 1, "trash lists the entry", trash)
    emptied = await call("media_trash_empty", older_than_days=0, expected_action="trash-empty:0")
    expect(emptied["data"]["applied"]["removed_count"] == 1, "trash empties", emptied)

    extra = os.environ.get("SMOKE_EXTRA")
    if extra:
        spec = importlib.util.spec_from_file_location("smoke_extra", extra)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        await module.run(call, expect)


async def main() -> None:
    wait_healthy()
    unauthorized = httpx.post(f"{BASE}/mcp", timeout=5)
    expect(unauthorized.status_code == 401, "requests without the token are refused")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with streamablehttp_client(f"{BASE}/mcp", headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await checks(session)
    print("smoke passed")


asyncio.run(main())
