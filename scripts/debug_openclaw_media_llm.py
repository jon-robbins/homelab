#!/usr/bin/env python3
"""Debug harness: one agent turn, classify reply, append NDJSON to session log."""
# ruff: noqa: T201

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from typing import Any

# #region agent log
_LOG_PATH = "/home/jon/docker/.cursor/debug-f44d4c.log"
_SESSION = "f44d4c"


def _log(
    hypothesis_id: str,
    message: str,
    data: dict[str, Any],
    *,
    run_id: str = "pre-fix",
) -> None:
    line = {
        "sessionId": _SESSION,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    with open(_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


# #endregion

_MSG = "Get Crazy Ex Girlfriend season 4."
_ACCEPT = (
    "Got it! Here are some options",
    "OK! It's downloading.",
)


def _run_agent(session_id: str | None) -> dict[str, Any]:
    cmd: list[str] = [
        "docker",
        "exec",
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
    p = subprocess.run(
        cmd,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
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


def _run_metadata(j: dict[str, Any]) -> dict[str, Any]:
    res = j.get("result", {}) or {}
    meta = res.get("meta", {}) or {}
    am = meta.get("agentMeta", {}) or {}
    return {
        "top_level_status": j.get("status"),
        "model": am.get("model"),
        "provider": am.get("provider"),
        "completion": meta.get("completion"),
        "aborted": meta.get("aborted"),
    }


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
    run_label = os.environ.get("DEBUG_RUN", "harness")
    max_a = int(os.environ.get("DEBUG_MAX_ATTEMPTS", "5"))
    for attempt in range(1, max_a + 1):
        sid: str | None
        if os.environ.get("DEBUG_USE_SESSION_ID", "").lower() in ("1", "true", "yes"):
            sid = os.environ.get("DEBUG_SESSION_ID") or str(uuid.uuid4())
        else:
            sid = None
        r = _run_agent(sid)
        # #region agent log
        _log(
            "H0",
            "agent_subprocess",
            {
                "attempt": attempt,
                "debug_session_id": r.get("session_id"),
                "returncode": r["returncode"],
                "stderr_tail": (r["stderr"] or "")[-500:],
            },
            run_id=run_label,
        )
        # #endregion
        if r["returncode"] != 0:
            # #region agent log
            _log("H0", "agent_failed", {"stdout": r["stdout"][:2000]}, run_id=run_label)
            # #endregion
            return 1
        j = _parse_agent_json((r["stdout"] or "") + "\n" + (r["stderr"] or ""))
        tool_names = _tool_names_from_report(j)
        text = _assistant_texts(j)
        ts = _tool_summary(j)
        rm = _run_metadata(j)
        c = classify(text, tool_names, ts)
        # #region agent log
        _log(
            "H4",
            "model_and_completion",
            {"attempt": attempt, **rm, "summary": j.get("summary")},
            run_id=run_label,
        )
        _log(
            "H10",
            "exec_in_toolSummary",
            {
                "attempt": attempt,
                "tools": (ts or {}).get("tools", []),
                "calls": (ts or {}).get("calls"),
                "failures": (ts or {}).get("failures"),
            },
            run_id=run_label,
        )
        _log(
            "H1",
            "tool_schema_gates_exec",
            {
                "attempt": attempt,
                "has_exec": c["has_exec_in_schema"],
                "tool_names": tool_names,
            },
            run_id=run_label,
        )
        _log(
            "H2",
            "tool_invocation_summary",
            {
                "attempt": attempt,
                "tool_summary": ts,
            },
            run_id=run_label,
        )
        _log(
            "H3",
            "acceptance_strings",
            {
                "attempt": attempt,
                "acceptable": c["acceptable"],
                "classify": c,
                "text_preview": (text or "")[:1200],
            },
            run_id=run_label,
        )
        if c.get("pasted_curl_in_reply"):
            _log(
                "H9",
                "model_pasted_curl_text_not_tool",
                {
                    "attempt": attempt,
                    "preview": (text or "")[:300],
                },
                run_id=run_label,
            )
        calls = (ts or {}).get("calls")
        if c.get("acceptable") and not calls and (
            "Title A" in (text or "") or "Seeders X" in (text or "") or "waiting for user input" in (text or "").lower()
        ):
            _log(
                "H11",
                "magic_phrase_without_tools_or_plausible_data",
                {
                    "attempt": attempt,
                    "hint": "SOUL line present but no toolSummary.calls; text looks like placeholders",
                },
                run_id=run_label,
            )
        # #endregion
        if c["strict_ok"]:
            print(json.dumps({"ok": True, "attempt": attempt, "classify": c}, indent=2))
            return 0
        time.sleep(1)
    print(json.dumps({"ok": False, "last": c}, indent=2))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
