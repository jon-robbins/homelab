#!/usr/bin/env bash
# Stage A static entrypoint tests. No container build, no compose.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "${ROOT}"
EP="apps/qwen-code-telegram/files/usr/local/bin/entrypoint.sh"
RENDER="apps/qwen-code-telegram/scripts/render_project_settings.py"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

# 1. Syntax
bash -n "$EP" || fail "entrypoint syntax"
pass "entrypoint.sh bash -n syntax"

# 2. Syntaxes: python3 render
python3 -c "import py_compile; py_compile.compile('${RENDER}', doraise=True)" || fail "render_settings syntax"
pass "render_project_settings.py syntax"

# 3. Mount checks present
grep -q 'check_forbidden_mounts()' "$EP" || fail "entrypoint missing mount checks"
grep -q 'find "\$home_root" -mindepth 1 -maxdepth 1 -type d' "$EP" \
  || fail "entrypoint must inspect /home entries"
grep -q 'QWEN_MOUNT_CHECK_ROOT' "$EP" || fail "entrypoint mount checks must be testable against a temp root"
pass "mount-checks: allows only the image user's home and refuses /homelab or extra /home entries"

# 3b. Behavioral mount-check test against an isolated filesystem tree.
tmpdir=""
mount_root="$(mktemp -d)"
trap 'rm -rf "$tmpdir" "$mount_root"' EXIT
mkdir -p "$mount_root/home/qwen"
HOME=/home/qwen QWEN_MOUNT_CHECK_ROOT="$mount_root" QWEN_MOUNT_CHECK_ONLY=1 \
  bash "$EP" || fail "mount-checks should allow only the image user's home"
mkdir -p "$mount_root/home/other"
if HOME=/home/qwen QWEN_MOUNT_CHECK_ROOT="$mount_root" QWEN_MOUNT_CHECK_ONLY=1 \
  bash "$EP" >/dev/null 2>&1; then
  fail "mount-checks should reject an extra /home directory"
fi
rm -rf "$mount_root/home/other"
mkdir -p "$mount_root/homelab"
if HOME=/home/qwen QWEN_MOUNT_CHECK_ROOT="$mount_root" QWEN_MOUNT_CHECK_ONLY=1 \
  bash "$EP" >/dev/null 2>&1; then
  fail "mount-checks should reject /homelab"
fi
pass "mount-checks behavioral isolation: own home passes, extra /home and /homelab fail"

# 4. Secret hydration + required env validation present
grep -q 'hydrate_secret_to_env' "$EP" || fail "entrypoint missing hydrate_secret_to_env helper"
grep -q 'TELEGRAM_BOT_TOKEN' "$EP" || fail "entrypoint missing TELEGRAM_BOT_TOKEN reference"
grep -q 'VLLM_API_KEY' "$EP" || fail "entrypoint missing VLLM_API_KEY reference (hydrates to OPENAI_API_KEY)"
grep -q 'QWEN_MCP_TELEGRAM_TOKEN' "$EP" || fail "entrypoint missing QWEN_MCP_TELEGRAM_TOKEN reference (hydrates to MCP_API_TOKEN)"
grep -q 'QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS' "$EP" || fail "entrypoint missing QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS reference"
grep -q 'TELEGRAM_MCP_MODE' "$EP" || fail "entrypoint missing TELEGRAM_MCP_MODE reference"
pass "hydrate_secret_to_env + 4 env validations present (bot/vllm/mcp secrets + allowlist + mode)"

# 5. All 7 hardening export lines present + set to exact values
grep -q 'QWEN_CODE_DISABLE_ARTIFACT=1' "$EP" || fail "missing DISABLE_ARTIFACT=1"
grep -q 'QWEN_USAGE_STATISTICS_ENABLED=0' "$EP" || fail "missing USAGE_STATISTICS=0"
grep -q 'QWEN_TELEMETRY_ENABLED=0' "$EP" || fail "missing TELEMETRY_ENABLED=0"
grep -q 'QWEN_TELEMETRY_LOG_PROMPTS=0' "$EP" || fail "missing TELEMETRY_LOG_PROMPTS=0"
grep -q 'QWEN_CODE_AUTO_UPDATE_ENABLED=0' "$EP" || fail "missing AUTO_UPDATE_ENABLED=0"
grep -q 'QWEN_CODE_LEGACY_MCP_BLOCKING=1' "$EP" || fail "missing LEGACY_MCP_BLOCKING=1"
grep -q 'QWEN_CODE_SYSTEM_SETTINGS_PATH=/etc/qwen-code/settings.json' "$EP" \
  || fail "missing SYSTEM_SETTINGS_PATH pin"
pass "7 hardening envs present with exact values (including QWEN_CODE_LEGACY_MCP_BLOCKING=1)"

# 6. Non-root exec present; command is `qwen channel start homelab-telegram`
grep -q 'exec qwen channel start homelab-telegram' "$EP" \
  || fail "entrypoint must run 'exec qwen channel start homelab-telegram'"
pass "exec → qwen channel start homelab-telegram"

# 7. Render trusted role settings end-to-end in a temp dir and verify the v0.21.15 shape.
tmpdir="$(mktemp -d)"
OPENAI_API_KEY="vllm-key-fake" \
TELEGRAM_BOT_TOKEN="bot-tok-fake" \
MCP_API_TOKEN="mcp-token-fake" \
QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS="100, 200, -500" \
QWEN_CODE_TELEGRAM_ROLE="trusted" \
python3 apps/qwen-code-telegram/scripts/render_project_settings.py "$tmpdir/out.json"
jq -e '
  (.modelProviders.openai[0].id == "qwen3.8-27b")
  and (.modelProviders.openai[0].baseUrl == "http://qwen-serve:18020/v1")
  and (.modelProviders.openai[0].envKey == "OPENAI_API_KEY")
  and (.modelProviders.openai[0].generationConfig.contextWindowSize == 65536)
  and (.security.auth.selectedType == "openai")
  and (.model.name == "qwen3.8-27b")
  and (.defaultModel == "qwen3.8-27b")
  and (.channels["homelab-telegram"].type == "telegram")
  and (.channels["homelab-telegram"].token == "$TELEGRAM_BOT_TOKEN")
  and (.channels["homelab-telegram"].senderPolicy == "allowlist")
  and (.channels["homelab-telegram"].allowedUsers == ["100","200","-500"])
  and (.channels["homelab-telegram"].sessionScope == "user")
  and (.channels["homelab-telegram"].cwd == "/workspace")
  and (.channels["homelab-telegram"].groupPolicy == "disabled")
  and (.channels["homelab-telegram"].dispatchMode == "followup")
  and (.mcpServers.homelab.httpUrl == "http://homelab-mcp-telegram:8090/mcp")
  and (.mcpServers.homelab.trust == false)
  and (.mcpServers.homelab.alwaysLoadTools == true)
  and (.mcpServers.homelab.headers.Authorization == "Bearer $MCP_API_TOKEN")
  and ((.mcpServers.homelab.includeTools | length) > 0)
  and (.mcp.allowed == ["homelab"])
' "$tmpdir/out.json" >/dev/null || fail "render_settings output v0.21.15 shape incorrect"
python3 - "$tmpdir/out.json" <<'PY' || fail "rendered includeTools must set-equal the canonical trusted role tool set"
import json, sys
sys.path.insert(0, "apps/qwen-code-telegram/scripts")
import render_project_settings as r
got = json.load(open(sys.argv[1]))["mcpServers"]["homelab"]["includeTools"]
assert got, "includeTools must be non-empty"
assert len(got) == len(set(got)), "includeTools has duplicates"
expected = set(r.build_include_tools("trusted"))
assert set(got) == expected, "includeTools drift: %r" % sorted(set(got) ^ expected)
PY
pass "render_project_settings.py → correct v0.21.15 JSON shape (contextWindowSize=65536, includeTools set-equal the canonical trusted role set, alwaysLoadTools=true, literal env references, dispatchMode=followup, mcp.allowed)"

# 7b. Owner rendering is derived from the same generated role artifact, not mode switches or counts.
QWEN_CODE_TELEGRAM_ROLE="owner" \
OPENAI_API_KEY="x" TELEGRAM_BOT_TOKEN="x" MCP_API_TOKEN="x" \
QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS="1" \
python3 apps/qwen-code-telegram/scripts/render_project_settings.py "$tmpdir/out-owner.json"
python3 - "$tmpdir/out-owner.json" <<'PY' || fail "owner role must render exactly the generated owner tool set"
import json, sys
sys.path.insert(0, "apps/qwen-code-telegram/scripts")
import render_project_settings as r
got = json.load(open(sys.argv[1]))["mcpServers"]["homelab"]["includeTools"]
expected = set(r.build_include_tools("owner"))
assert len(got) == len(set(got)), "includeTools has duplicates"
assert set(got) == expected, "owner includeTools drift: %r" % sorted(set(got) ^ expected)
PY
pass "render: owner role derives exactly the generated owner tool set"

# 7c. Invalid role → render exits non-zero
set +e
QWEN_CODE_TELEGRAM_ROLE="junk" \
OPENAI_API_KEY="x" TELEGRAM_BOT_TOKEN="x" MCP_API_TOKEN="x" \
QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS="1" \
python3 apps/qwen-code-telegram/scripts/render_project_settings.py "$tmpdir/out-bad.json"
rc=$?
set -e
[[ "$rc" -ne 0 ]] || fail "render_settings must exit non-zero when QWEN_CODE_TELEGRAM_ROLE is junk"
pass "render: invalid role → fail-closed exit non-zero"

# 7d. Stage A readonly pin (behavioral, against the real entrypoint text):
# TELEGRAM_MCP_MODE=acquire must collapse back to readonly, unset defaults to
# readonly, readonly passes through, junk fails closed.
pin_block="$(awk '/: "\$\{TELEGRAM_MCP_MODE:=readonly\}"/,/^fi$/' "$EP")"
[[ -n "$pin_block" ]] || fail "could not extract TELEGRAM_MCP_MODE pin block from entrypoint.sh"
run_pin_block() { # $1 = initial TELEGRAM_MCP_MODE value, or "unset"
  (
    log()  { :; }
    fail() { echo "ENTRYPOINT_FAIL" >&2; exit 9; }
    if [[ "$1" == "unset" ]]; then unset TELEGRAM_MCP_MODE; else export TELEGRAM_MCP_MODE="$1"; fi
    eval "$pin_block"
    echo "$TELEGRAM_MCP_MODE"
  )
}
[[ "$(run_pin_block acquire)" == "readonly" ]] || fail "entrypoint pin: TELEGRAM_MCP_MODE=acquire must collapse to readonly"
[[ "$(run_pin_block unset)" == "readonly" ]] || fail "entrypoint pin: unset TELEGRAM_MCP_MODE must default to readonly"
[[ "$(run_pin_block readonly)" == "readonly" ]] || fail "entrypoint pin: readonly must pass through unchanged"
if run_pin_block junk >/dev/null 2>&1; then
  fail "entrypoint pin: junk TELEGRAM_MCP_MODE must fail closed"
fi
pass "entrypoint Stage A pin (behavioral): acquire→readonly, unset→readonly, junk rejected"

# 7e. The legacy mode pin cannot alter role-derived tool selection.
pinned_mode="$(run_pin_block acquire)"
TELEGRAM_MCP_MODE="$pinned_mode" \
QWEN_CODE_TELEGRAM_ROLE="trusted" \
OPENAI_API_KEY="x" TELEGRAM_BOT_TOKEN="x" MCP_API_TOKEN="x" \
QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS="1" \
python3 apps/qwen-code-telegram/scripts/render_project_settings.py "$tmpdir/out-pinned.json"
python3 - "$tmpdir/out-pinned.json" <<'PY' || fail "pinned-acquire render must equal exactly the canonical trusted role tool set"
import json, sys
sys.path.insert(0, "apps/qwen-code-telegram/scripts")
import render_project_settings as r
got = json.load(open(sys.argv[1]))["mcpServers"]["homelab"]["includeTools"]
expected = set(r.build_include_tools("trusted"))
assert set(got) == expected, "pinned render drift: %r" % sorted(set(got) ^ expected)
PY
pass "legacy mode pin leaves trusted role tool selection unchanged"

# 8. Fail-close when required env missing (OPENAI_API_KEY empty)
set +e
OPENAI_API_KEY="" TELEGRAM_BOT_TOKEN="x" MCP_API_TOKEN="x" \
  QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS="1" \
  TELEGRAM_MCP_MODE="readonly" \
  python3 apps/qwen-code-telegram/scripts/render_project_settings.py "$tmpdir/out2.json"
rc=$?
set -e
[[ "$rc" -ne 0 ]] || fail "render_settings must exit non-zero when OPENAI_API_KEY is empty"
pass "render_settings fail-closed: exits non-zero on missing required env"

# 9. QWEN.md scaffold in entrypoint
grep -q '/workspace/QWEN.md' "$EP" || fail "entrypoint missing QWEN.md scaffold into /workspace"
pass "entrypoint copies default QWEN.md policy into /workspace if not present"

echo "ALL test_entrypoint.sh PASS"
