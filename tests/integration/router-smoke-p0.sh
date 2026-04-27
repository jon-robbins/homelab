#!/usr/bin/env bash
# Router P0 smoke gate (expects media-agent container). See .github/workflows/ci.yml.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

COMPOSE_PROJECT="${COMPOSE_PROJECT:-homelab}"

out="$(
  docker compose -p "${COMPOSE_PROJECT}" exec -T media-agent python - <<'PY'
import json
import os
import urllib.error
import urllib.request

base = "http://127.0.0.1:8000/internal/media-agent/v1"
auth = {"Authorization": "Bearer " + os.environ["MEDIA_AGENT_TOKEN"]}
headers = {**auth, "Content-Type": "application/json"}

def post_json(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers=headers,
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=180).read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() or "{}"
        try:
            return json.loads(body)
        except Exception:
            return {"ok": False, "error": {"code": "HTTP_ERROR", "message": body}}

def get_json(path: str) -> dict:
    req = urllib.request.Request(base + path, headers=auth)
    return json.loads(urllib.request.urlopen(req, timeout=180).read().decode())

gate = get_json("/router-smoke-gate")
steps = gate.get("steps") or []
expect = gate.get("expectations") or {}
session_key = str(gate.get("session_key") or "smoke-cxg-s4")
if len(steps) < 2:
    raise SystemExit("router-smoke-gate returned insufficient steps")

first = post_json("/router", steps[0])
first_tool = first.get("tool_result") or {}
first_reuse_ok = (
    first_tool.get("existing_torrent_reused") is True
    and str(first_tool.get("status") or "") in {"enabled", "already_downloaded", "already_selected"}
)
second = post_json("/router", steps[1]) if not first_reuse_ok else {}
second_tool = second.get("tool_result") or {}
season_selection = second_tool.get("season_selection") or {}
second_ok = (
    second_tool.get("ok") is True
    and season_selection.get("status") == expect.get("season_only_status")
)
ok = first_reuse_ok or second_ok
print(json.dumps({
    "session_key": session_key,
    "first": {
        "action": (first.get("action") or {}).get("action"),
        "tool_ok": first_tool.get("ok"),
        "reuse": first_tool.get("existing_torrent_reused"),
        "reuse_status": first.get("status"),
        "response": first.get("response_text"),
    },
    "second": {
        "action": (second.get("action") or {}).get("action"),
        "response": second.get("response_text"),
        "tool_ok": second_tool.get("ok"),
        "season_selection": season_selection,
        "error": second_tool.get("error") or second.get("error"),
    },
    "ok": ok,
}))
PY
)"

python3 - "$out" <<'PY'
import json
import sys

obj = json.loads(sys.argv[1])
if obj.get("ok") is True:
    raise SystemExit(0)
print(json.dumps(obj, indent=2))
raise SystemExit(1)
PY
