#!/usr/bin/env python3
"""LLM behavior gate: run one openclaw-gateway agent turn and classify the reply.

Used by the CI workflow as the LLM behavior check. Exits 0 on a strict pass,
non-zero otherwise. Configurable via environment variables:

    DEBUG_AGENT_TIMEOUT   per-call agent timeout in seconds (default 300)
    DEBUG_MAX_ATTEMPTS    number of agent retries before failing (default 5)
    DEBUG_USE_SESSION_ID  set to 1/true to send a session id with the call
    DEBUG_SESSION_ID      explicit session id when DEBUG_USE_SESSION_ID is set
"""
# ruff: noqa: T201

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from typing import Any

_MSG = "Get Crazy Ex Girlfriend season 4."
_ACCEPT = (
    "Got it! Here are some options",
    "OK! It's downloading.",
)


def _run_agent(session_id: str | None) -> dict[str, Any]:
    project = os.environ.get("COMPOSE_PROJECT", "homelab")
    cmd: list[str] = [
        "docker",
        "compose",
        "-p",
        project,
        "exec",
        "-T",
        "openclaw-gateway",
        "node",
        "dist/index.js",
        "agent",
        "--agent",
        "main",
    ]
    if session_id is not None:
        cmd += ["--session-id", session_id]
    cmd += [
        "-m",
        _MSG,
        "--json",
        "--timeout",
        os.environ.get("DEBUG_AGENT_TIMEOUT", "300"),
    ]
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    p = subprocess.run(
        cmd,
        cwd=repo_root,
        env=os.environ,
        capture_output=True,
        text=True,
        timeout=720,
    )
    return {
        "returncode": p.returncode,
        "stdout": p.stdout,
        "stderr": p.stderr,
        "session_id": session_id,
    }


def _parse_agent_json(out: str) -> dict[str, Any]:
    m = re.search(r"\{\s*\"runId\"\s*:\s*\"[0-9a-f-]+\"", out)
    if m:
        start = m.start()
        dec = json.JSONDecoder()
        try:
            return dec.raw_decode(out[start:])[0]
        except json.JSONDecodeError:
            pass
    start = out.rfind('{\n  "status"')
    if start < 0:
        start = out.find("{")
    if start < 0:
        return {"error": "no_json", "raw": out[:2000]}
    dec = json.JSONDecoder()
    return dec.raw_decode(out[start:])[0]


def _tool_names_from_report(j: dict[str, Any]) -> list[str]:
    try:
        entries = (
            j.get("result", {})
            .get("meta", {})
            .get("systemPromptReport", {})
            .get("tools", {})
            .get("entries", [])
        )
        return [e.get("name", "") for e in entries if isinstance(e, dict)]
    except (TypeError, KeyError, AttributeError):
        return []


def _assistant_texts(j: dict[str, Any]) -> str:
    parts: list[str] = []
    for p in j.get("result", {}).get("payloads", []) or []:
        t = p.get("text")
        if t:
            parts.append(t)
    return "\n\n".join(parts)


def _tool_summary(j: dict[str, Any]) -> dict[str, Any]:
    return (j.get("result", {}) or {}).get("meta", {}).get("toolSummary") or {}


def classify(assistant: str, tool_names: list[str], tool_summary: dict[str, Any]) -> dict[str, Any]:
    lower = assistant.lower()
    has_exec = "exec" in tool_names
    has_fake = '{"name":' in assistant or '"name": "exec"' in assistant
    has_fence = "```json" in assistant or "```" in assistant and "curl" in assistant
    option_ok = any(a.lower() in lower for a in _ACCEPT if "options" in a.lower())
    download_ok = any(a.lower() in lower for a in _ACCEPT if "downloading" in a.lower())
    acceptable = any(a in assistant for a in _ACCEPT)
    st = (assistant or "").lstrip()
    full = assistant or ""
    pasted_shell = (
        st.startswith("exec")
        or st.startswith("curl")
        or "exec curl" in full
        or "${MEDIA_AGENT_URL}" in full
        or "MEDIA_AGENT_TOKEN" in full
    )
    calls = int((tool_summary or {}).get("calls") or 0)
    looks_placeholder = (
        "Title A" in full
        or "Seeders X" in full
        or "Indexer Alpha" in full
        or "waiting for user input" in full.lower()
        or "media-agent.download-client.com" in full
    )
    strict_ok = bool(acceptable and calls > 0 and not pasted_shell and not looks_placeholder)
    return {
        "has_exec_in_schema": has_exec,
        "tool_names": tool_names,
        "tool_summary": tool_summary,
        "tool_calls": calls,
        "has_roleplayed_tool_json": has_fake,
        "has_suspicious_fence": has_fence,
        "pasted_curl_in_reply": pasted_shell,
        "looks_placeholder": looks_placeholder,
        "accept_phrase_options": option_ok,
        "accept_phrase_download": download_ok,
        "acceptable": acceptable,
        "strict_ok": strict_ok,
    }


def main() -> int:
    max_attempts = int(os.environ.get("DEBUG_MAX_ATTEMPTS", "5"))
    last_classification: dict[str, Any] = {}
    for _attempt in range(1, max_attempts + 1):
        sid: str | None
        if os.environ.get("DEBUG_USE_SESSION_ID", "").lower() in ("1", "true", "yes"):
            sid = os.environ.get("DEBUG_SESSION_ID") or str(uuid.uuid4())
        else:
            sid = None
        r = _run_agent(sid)
        if r["returncode"] != 0:
            print(json.dumps({"ok": False, "error": "agent_failed", "stdout": r["stdout"][:2000]}, indent=2))
            return 1
        j = _parse_agent_json((r["stdout"] or "") + "\n" + (r["stderr"] or ""))
        tool_names = _tool_names_from_report(j)
        text = _assistant_texts(j)
        ts = _tool_summary(j)
        last_classification = classify(text, tool_names, ts)
        if last_classification["strict_ok"]:
            print(json.dumps({"ok": True, "classify": last_classification}, indent=2))
            return 0
        time.sleep(1)
    print(json.dumps({"ok": False, "last": last_classification}, indent=2))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
