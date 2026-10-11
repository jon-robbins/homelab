#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT"
RENDER="apps/qwen-code-telegram/scripts/render_project_settings.py"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

render_role() {
  local role="$1" out="$2"
  QWEN_CODE_TELEGRAM_ROLE="$role" \
  OPENAI_API_KEY=test TELEGRAM_BOT_TOKEN=test MCP_API_TOKEN=test \
  QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS=1 \
    python3 "$RENDER" "$out"
}

render_role trusted "$tmpdir/trusted.json"
render_role owner "$tmpdir/owner.json"

python3 - "$tmpdir/trusted.json" "$tmpdir/owner.json" \
  "apps/qwen-code-telegram/config/mcp-role-tools.json" <<'PY'
import json
import sys

sys.path.insert(0, "apps/qwen-code-telegram/scripts")
import render_project_settings as renderer

trusted = set(json.load(open(sys.argv[1]))["mcpServers"]["homelab"]["includeTools"])
owner = set(json.load(open(sys.argv[2]))["mcpServers"]["homelab"]["includeTools"])
artifact = json.load(open(sys.argv[3]))
assert set(artifact) == {"capabilities", "roles"}
assert set(artifact["roles"]) == {"trusted", "owner"}
assert set(artifact["roles"]["owner"]) == set(artifact["capabilities"])
assert set(artifact["capabilities"].values()) <= renderer.PERMITTED_CAPABILITIES
assert {
    artifact["capabilities"][tool] for tool in artifact["roles"]["trusted"]
} <= renderer.TRUSTED_ALLOWED_CAPABILITIES
assert trusted
assert trusted < owner
assert trusted == set(artifact["roles"]["trusted"])
assert owner == set(artifact["roles"]["owner"])

invalid = json.loads(json.dumps(artifact))
invalid["capabilities"]["arr_lookup"] = "not-permitted"
try:
    renderer.validate_role_tool_artifact(invalid)
except ValueError as exc:
    assert "invalid capability values" in str(exc)
else:
    raise AssertionError("renderer accepted a capability outside the permitted set")

trusted_owner = json.loads(json.dumps(artifact))
trusted_owner["roles"]["trusted"].append("qbt_apply_profile")
trusted_owner["roles"]["trusted"].sort()
try:
    renderer.validate_role_tool_artifact(trusted_owner)
except ValueError as exc:
    assert "trusted role has disallowed capabilities" in str(exc)
else:
    raise AssertionError("renderer granted an owner-only capability to trusted")
print(f"PASS: trusted={len(trusted)} owner={len(owner)} role sets are distinct")
PY
