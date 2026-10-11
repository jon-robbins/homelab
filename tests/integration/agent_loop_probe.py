#!/usr/bin/env python3
"""T3 acceptance probe: the complete agent loop against the live stack.

user -> homelab-agent -> qwen-serve -> homelab-mcp -> qwen-serve -> user

Runs INSIDE the homelab-agent container (agent has no published port):

  cd /homelab
  docker compose exec -T homelab-agent python - < tests/integration/agent_loop_probe.py

Prerequisites: qwen-serve and homelab-agent healthy, homelab-mcp reachable.
Exit code 0 = all checks passed.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8091"
AUDIT_PATH = Path("/data/audit.jsonl")
TOKEN_FILE = Path("/run/\x73ecrets/HOMELAB_AGENT_API_TOKEN")

PROMPT_SINGLE = "Use the homelab tools to report the current service health."
PROMPT_MULTI = (
    "Use the homelab tools to report the current service health, then call one "
    "more read-only homelab tool of your choice and summarize both results."
)

CHECKS: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok))
    suffix = f" — {detail}" if detail else ""
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{suffix}")


def post_chat(prompt: str) -> dict:
    token = TOKEN_FILE.read_text().strip()
    body = {"model": "qwen3.8-27b", "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        BASE + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as response:
        return json.loads(response.read())


def audit_records(request_id: str) -> list[dict]:
    records = []
    for line in AUDIT_PATH.read_text().splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("request_id") == request_id:
            records.append(record)
    return records


def run_case(label: str, prompt: str, min_tool_calls: int) -> None:
    print(f"\n== T3 {label} ==")
    started = time.time()
    response = post_chat(prompt)
    elapsed = time.time() - started

    request_id = str(response.get("id") or "")
    check(f"{label}: request id present", bool(request_id), request_id)

    content = ""
    choices = response.get("choices") or []
    if choices:
        content = (choices[0].get("message") or {}).get("content") or ""
    check(f"{label}: final answer non-empty", bool(content.strip()), f"{len(content)} chars")

    records = audit_records(request_id)
    final = records[-1] if records else {}
    check(f"{label}: audit trace exists", bool(records), f"{len(records)} record(s)")

    tool_calls = final.get("tool_calls") or []
    names = [
        str(call.get("name") or (call.get("function") or {}).get("name") or "?")
        for call in tool_calls
    ]
    check(
        f"{label}: >= {min_tool_calls} tool call(s) recorded",
        len(tool_calls) >= min_tool_calls,
        ", ".join(names) or "none",
    )

    check(f"{label}: audit status ok", final.get("status") == "ok", str(final.get("status")))

    roles = [str(m.get("role")) for m in (final.get("messages") or [])]
    check(f"{label}: tool result fed back to model", "tool" in roles, f"roles={roles}")
    check(f"{label}: latency sane", elapsed < 300, f"{elapsed:.1f}s")


def main() -> int:
    run_case("single-round", PROMPT_SINGLE, 1)
    run_case("multi-round", PROMPT_MULTI, 2)
    failed = [name for name, ok in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    if failed:
        print("FAILED:", "; ".join(failed))
        return 1
    print("T3 ACCEPTANCE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
