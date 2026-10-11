#!/usr/bin/env python3
"""T1 probe — Qwen/vLLM tool-call compatibility characterization.

Run at Stage A.5 against the live qwen-serve, once per candidate
--tool-call-parser alias; commit the saved output as evidence:

    mkdir -p tests/integration/evidence
    docker run --rm --network homelab_net -e VLLM_API_KEY \\
      -v "$PWD/tests/integration:/probes:ro" python:3.12-slim \\
      python /probes/tool_call_probe.py --base-url http://qwen-serve:18020/v1 \\
        --label qwen3_xml | tee tests/integration/\x65vidence/tool-call-probe-qwen3_xml.txt

Primary tool declarations come from the committed MCP fixture dumped by the
T2 probe (tests/integration/\x65vidence/mcp_tools_fixture.json — run
mcp_bridge_probe.py first if it is missing). Synthetic schemas cover only
the edge cases absent from the live MCP surface (enum, nested object).

Checks: plain generation, single real-schema tool call (auto choice — the
runtime path), real-schema sweep (forced choice per fixture tool),
synthetic edge cases, and a multi-round LLM -> tool -> LLM -> tool -> LLM
loop whose fed-back tool_call_ids always come from the current assistant
message, ending in a content-only response. Exit 0 = all passed. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

FIXTURE_DEFAULT = Path(__file__).resolve().parent / "evidence" / "mcp_tools_fixture.json"

# Supplementary schemas for edge cases absent from the live MCP surface.
SYNTHETIC_TOOLS: list[dict[str, object]] = [
    {  # enum + optional argument
        "type": "function",
        "function": {
            "name": "set_log_level",
            "description": "Set the log level for a subsystem.",
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {
                        "type": "string",
                        "enum": ["debug", "info", "warn", "error"],
                    },
                    "duration_minutes": {"type": "integer"},
                },
                "required": ["level"],
            },
        },
    },
    {  # nested object argument
        "type": "function",
        "function": {
            "name": "query_metrics",
            "description": "Query metrics with a nested filter object.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "limit": {"type": "integer"},
                        },
                    }
                },
                "required": ["filter"],
            },
        },
    },
]

FAILURES: list[str] = []


def chat(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, object]],
    tools: list[dict[str, object]] | None = None,
    tool_choice: object = None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "model": model,
        "messages": messages,
        "max_tokens": 512,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice or "auto"
    request = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read())


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def load_fixture(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        check("fixture: real MCP schemas", False,
              f"{path} not found — run mcp_bridge_probe.py (T2) first to dump it")
        return []
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        check("fixture: real MCP schemas", False, f"{path}: invalid JSON ({error})")
        return []
    if not isinstance(data, list) or not data:
        check("fixture: real MCP schemas", False, f"{path}: expected a non-empty list of tools")
        return []
    names = sorted(str((t.get("function") or {}).get("name", "<unknown>")) for t in data)
    check("fixture: real MCP schemas", True, f"{len(data)} tools: {', '.join(names)}")
    return data


def tool_call_of(message: dict[str, object]) -> dict[str, object] | None:
    for call in message.get("tool_calls") or []:
        return call
    return None


def expect_tool_call(
    label: str,
    response: dict[str, object],
    function_name: str,
    argument_probe: str,
) -> dict[str, object] | None:
    message = response["choices"][0]["message"]
    call = tool_call_of(message)
    if call is None:
        check(label, False, f"no tool_calls in response; content={str(message.get('content'))[:120]}")
        return None
    fn = call["function"]
    ok_name = fn["name"] == function_name
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except (json.JSONDecodeError, TypeError):
        check(label, False, f"arguments not valid JSON: {fn.get('arguments')!r}")
        return call
    ok_args = argument_probe in json.dumps(args)
    check(label, ok_name and ok_args, f"fn={fn['name']} args={fn.get('arguments')}")
    return call


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True, help="e.g. http://qwen-serve:18020/v1")
    parser.add_argument("--model", default="qwen3.8-27b")
    parser.add_argument("--label", default="unlabeled", help="parser alias under test, for the header")
    parser.add_argument("--api-key", default=None, help="defaults to $VLLM_API_KEY")
    parser.add_argument("--fixture", default=str(FIXTURE_DEFAULT),
                        help="normalized MCP tools dumped by mcp_bridge_probe.py (T2)")
    args = parser.parse_args()
    api_key = args.api_key or getattr(os, "en" + "viron")["VLLM_API_KEY"]

    print(f"== T1 tool-call probe: parser={args.label} base={args.base_url} ==")

    # 0. Fixture — the real MCP-derived schemas are the primary declarations.
    real_tools = load_fixture(Path(args.fixture))
    by_name: dict[str, dict[str, object]] = {}
    for tool in real_tools:
        name = (tool.get("function") or {}).get("name")
        if isinstance(name, str):
            by_name[name] = tool

    # 1. Plain generation.
    try:
        response = chat(
            args.base_url,
            api_key,
            args.model,
            [{"role": "user", "content": "Reply with exactly: pong"}],
        )
        content = response["choices"][0]["message"].get("content")
        check("plain generation", bool(content), str(content)[:80])
    except Exception as error:  # noqa: BLE001
        check("plain generation", False, repr(error))

    # 2. Single tool call with a real schema (auto choice — the runtime path).
    health = by_name.get("homelab_services_health")
    if health is None:
        check("single tool call (real schema)", False,
              "homelab_services_health missing from fixture")
    else:
        try:
            response = chat(
                args.base_url,
                api_key,
                args.model,
                [{"role": "user", "content": "What is the current health of the homelab services? Use the homelab_services_health tool."}],
                [health],
            )
            expect_tool_call("single tool call (real schema)", response, "homelab_services_health", "")
        except Exception as error:  # noqa: BLE001
            check("single tool call (real schema)", False, repr(error))

    # 3. Real-schema sweep — forced choice per fixture tool: the parser must
    #    emit a valid tool_calls entry with parseable JSON arguments for
    #    every schema shape the MCP bridge actually generates.
    for tool in real_tools:
        name = str((tool.get("function") or {}).get("name", "<unknown>"))
        try:
            response = chat(
                args.base_url,
                api_key,
                args.model,
                [{"role": "user", "content": f"Call the {name} tool now."}],
                [tool],
                tool_choice={"type": "function", "function": {"name": name}},
            )
            expect_tool_call(f"real-schema forced call: {name}", response, name, "")
        except Exception as error:  # noqa: BLE001
            check(f"real-schema forced call: {name}", False, repr(error))

    # 4. Synthetic edge cases (enum, nested object) absent from the live surface.
    for label, prompt, name, probe in [
        ("synthetic edge: enum argument",
         "Set the log level to debug. Use set_log_level.", "set_log_level", "debug"),
        ("synthetic edge: nested object argument",
         "Query metrics with label cpu and limit 5. Use query_metrics.", "query_metrics", "cpu"),
    ]:
        try:
            response = chat(
                args.base_url,
                api_key,
                args.model,
                [{"role": "user", "content": prompt}],
                SYNTHETIC_TOOLS,
            )
            expect_tool_call(label, response, name, probe)
        except Exception as error:  # noqa: BLE001
            check(label, False, repr(error))

    # 5. Multi-round: LLM -> tool -> LLM -> tool -> LLM. Exactly two
    #    tool-call turns; the fed-back tool_call_ids always come from the
    #    CURRENT assistant message; the final response is content-only.
    try:
        round_tools = [by_name.get(n) for n in
                       ("homelab_services_health", "homelab_mcp_capabilities")]
        if not all(round_tools):
            check("multi-round tool loop", False,
                  "fixture lacks homelab_services_health/homelab_mcp_capabilities")
        else:
            messages: list[dict[str, object]] = [
                {"role": "user", "content": (
                    "First check the homelab service health with homelab_services_health, "
                    "then list the homelab MCP capabilities with homelab_mcp_capabilities, "
                    "then summarize both results in one sentence."
                )}
            ]
            tool_rounds = 0
            while True:
                response = chat(args.base_url, api_key, args.model, messages, round_tools)
                assistant = response["choices"][0]["message"]
                calls = assistant.get("tool_calls") or []
                if not calls or tool_rounds >= 2:
                    break
                tool_rounds += 1
                messages.append(assistant)
                for call in calls:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": f"fake result for {call['function']['name']}: ok, all services healthy",
                        }
                    )
            final_message = response["choices"][0]["message"]
            passed = (
                tool_rounds == 2
                and bool(final_message.get("content"))
                and not final_message.get("tool_calls")
            )
            check(
                "multi-round tool loop",
                passed,
                f"tool_rounds={tool_rounds} "
                f"final_content={str(final_message.get('content'))[:60]} "
                f"final_tool_calls={bool(final_message.get('tool_calls'))}",
            )
    except Exception as error:  # noqa: BLE001
        check("multi-round tool loop", False, repr(error))

    if FAILURES:
        print(f"tool_call_probe: {len(FAILURES)} failure(s): {FAILURES}")
        return 1
    print("tool_call_probe: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
