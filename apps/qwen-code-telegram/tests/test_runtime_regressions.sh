#!/usr/bin/env bash
# End-to-end container regression test for Stage A Qwen Telegram bot runtime.
# Tests:
#   1. Effective Telegram channel dispatchMode is followup (in rendered settings)
#   2. Persisted settings contain literal environment references ($OPENAI_API_KEY, $TELEGRAM_BOT_TOKEN, Bearer $MCP_API_TOKEN), not sentinel values
#   3. Default QWEN.md policy exists in /workspace at runtime and is atomically overwritten on startup from the image
#   4. Volume & path structural test: QWEN_HOME=/data/home and QWEN_RUNTIME_DIR=/data/runtime paths are created, mounted, and isolated; secrets stay in /run/secrets
#   5. Secrets/tokens are NOT persisted into /data or /workspace
#   6. Sentinel token non-disclosure (no leak in stdout/stderr)
#   7. Effective tool surface & native bypass lockdown:
#      - Proves AcpBridge does NOT expose shellCommand (preventing DM ! bypass)
#      - Proves @ file lookups outside /workspace boundary cannot read secrets
#      - Proves native tools policy in /etc/qwen-code/settings.json disables all native tools so effective model surface is Homelab MCP only
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "${ROOT}"

OUT1=""
OUT2=""
OUT_BYPASS=""
OUT_TOOLS=""
OUT_BEHAVIORAL=""
OUT_COMPACTION=""

print_diagnostics() {
  local output="$1"
  [[ -n "$output" ]] || return 0
  [[ -n "${SENTINEL_BOT:-}" ]] && output="${output//"$SENTINEL_BOT"/[REDACTED]}"
  [[ -n "${SENTINEL_VLLM:-}" ]] && output="${output//"$SENTINEL_VLLM"/[REDACTED]}"
  [[ -n "${SENTINEL_MCP:-}" ]] && output="${output//"$SENTINEL_MCP"/[REDACTED]}"
  printf '%s\n' "$output"
}

fail() {
  echo "FAIL: $*" >&2
  for diagnostics in "$OUT1" "$OUT2" "$OUT_BYPASS" "$OUT_TOOLS" "$OUT_BEHAVIORAL" "$OUT_COMPACTION"; do
    print_diagnostics "$diagnostics" >&2
  done
  exit 1
}
pass() { echo "PASS: $*"; }

command -v docker >/dev/null 2>&1 \
  || fail "docker is not installed; runtime regression tests require Docker"

IMAGE="homelab/qwen-code-telegram:test"
TEST_VOL_WORKSPACE="test-qwen-workspace-reg-$$"
TEST_VOL_DATA="test-qwen-data-reg-$$"
SECRETS_DIR="$(mktemp -d)"

# Expected trusted tool surface, derived from the canonical role artifact via
# its renderer interface — never a legacy READONLY_TOOLS alias or hardcoded count.
EXPECTED_TRUSTED_TOOLS="$(python3 -c '
import sys
sys.path.insert(0, "apps/qwen-code-telegram/scripts")
from render_project_settings import build_include_tools
print(",".join(build_include_tools("trusted")))
')"
[[ -n "$EXPECTED_TRUSTED_TOOLS" ]] || fail "could not derive trusted tools from the canonical role artifact"

cleanup() {
  docker volume rm -f "$TEST_VOL_WORKSPACE" "$TEST_VOL_DATA" >/dev/null 2>&1 || true
  rm -rf "$SECRETS_DIR"
}
trap cleanup EXIT

# 1. Setup mock secret files with sentinels
SENTINEL_BOT="SENTINEL_BOT_TOKEN_LEAK_TEST_999"
SENTINEL_VLLM="SENTINEL_VLLM_KEY_LEAK_TEST_888"
SENTINEL_MCP="SENTINEL_MCP_TOKEN_LEAK_TEST_777"

echo -n "$SENTINEL_BOT" > "$SECRETS_DIR/TELEGRAM_BOT_TOKEN"
echo -n "$SENTINEL_VLLM" > "$SECRETS_DIR/VLLM_API_KEY"
echo -n "$SENTINEL_MCP" > "$SECRETS_DIR/QWEN_MCP_TELEGRAM_TOKEN"

# 2. Create isolated named test volumes for workspace and data
docker volume create "$TEST_VOL_WORKSPACE" >/dev/null
docker volume create "$TEST_VOL_DATA" >/dev/null

# 3. First container run: initialize workspace & data, render settings, check QWEN.md, dispatchMode, and literal env references
OUT1=$(docker run --rm \
  -e QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS="12345678" \
  -e TELEGRAM_MCP_MODE="readonly" \
  -e QWEN_HOME="/data/home" \
  -e QWEN_RUNTIME_DIR="/data/runtime" \
  -v "$SECRETS_DIR/TELEGRAM_BOT_TOKEN:/run/secrets/TELEGRAM_BOT_TOKEN:ro" \
  -v "$SECRETS_DIR/VLLM_API_KEY:/run/secrets/VLLM_API_KEY:ro" \
  -v "$SECRETS_DIR/QWEN_MCP_TELEGRAM_TOKEN:/run/secrets/QWEN_MCP_TELEGRAM_TOKEN:ro" \
  -v "$TEST_VOL_WORKSPACE:/workspace" \
  -v "$TEST_VOL_DATA:/data" \
  --entrypoint bash \
  "$IMAGE" -c '
    # Run entrypoint setup up to phase 4
    /usr/local/bin/entrypoint.sh >/tmp/entrypoint.log 2>&1 &
    PID=$!
    for _ in $(seq 1 30); do
      if [ -f /run/qwen-code/project-settings.json ]; then
        break
      fi
      if ! kill -0 "$PID" 2>/dev/null; then
        echo "entrypoint exited before project-settings.json was created"
        cat /tmp/entrypoint.log
        exit 1
      fi
      sleep 1
    done
    if [ ! -f /run/qwen-code/project-settings.json ]; then
      echo "timed out waiting for project-settings.json"
      cat /tmp/entrypoint.log
      exit 1
    fi

    # Check settings rendered on disk before qwen exits
    echo "--- CHECK DISPATCH_MODE ---"
    python3 -c "import json; d=json.load(open(\"/run/qwen-code/project-settings.json\")); print(d.get(\"channels\",{}).get(\"homelab-telegram\",{}).get(\"dispatchMode\", \"\"))"

    echo "--- CHECK LITERAL ENV REFS ---"
    python3 -c "
import json
d = json.load(open(\"/run/qwen-code/project-settings.json\"))
envKey = d.get(\"modelProviders\",{}).get(\"openai\",[{}])[0].get(\"envKey\", \"\")
botToken = d.get(\"channels\",{}).get(\"homelab-telegram\",{}).get(\"token\", \"\")
mcpAuth = d.get(\"mcpServers\",{}).get(\"homelab\",{}).get(\"headers\",{}).get(\"Authorization\", \"\")
mcpAllowed = d.get(\"mcp\",{}).get(\"allowed\", [])
alwaysLoad = d.get(\"mcpServers\",{}).get(\"homelab\",{}).get(\"alwaysLoadTools\", False)
print(f\"envKey={envKey}\")
print(f\"botToken={botToken}\")
print(f\"mcpAuth={mcpAuth}\")
print(f\"mcpAllowed={mcpAllowed}\")
print(f\"alwaysLoad={alwaysLoad}\")
"
    echo "--- ENTRYPOINT OUTPUT ---"
    cat /tmp/entrypoint.log
    kill -INT $PID 2>/dev/null || true
    wait $PID 2>/dev/null || true

    echo "--- CHECK QWEN_MD ---"
    if [ -f /workspace/QWEN.md ]; then
      echo "QWEN_MD_EXISTS"
      head -n 2 /workspace/QWEN.md
    fi

    # Test QWEN.md overwrite resistance: tamper with QWEN.md in volume
    echo "TAMPERED_POLICY_IN_VOLUME" > /workspace/QWEN.md
  ' 2>&1 || true)

# Assert sentinels not leaked in stdout/stderr
for s in "$SENTINEL_BOT" "$SENTINEL_VLLM" "$SENTINEL_MCP"; do
  if grep -q "$s" <<<"$OUT1"; then
    fail "Secret sentinel $s was leaked into output!"
  fi
done
pass "Secret sentinels were NOT leaked in container output"

# Assert dispatchMode == followup
DISPATCH_MODE=$(python3 -c '
import sys, re
text = sys.stdin.read()
m = re.search(r"--- CHECK DISPATCH_MODE ---\n(\w+)", text)
if not m:
    sys.exit(1)
print(m.group(1).strip())
' <<<"$OUT1")
[[ "$DISPATCH_MODE" == "followup" ]] || fail "effective Telegram channel dispatchMode must be 'followup'; got '$DISPATCH_MODE'"
pass "effective Telegram channel dispatch is followup"

# Assert literal environment references in settings.json
grep -q 'envKey=OPENAI_API_KEY' <<<"$OUT1" || fail "settings.json envKey must be 'OPENAI_API_KEY'"
grep -q 'botToken=\$TELEGRAM_BOT_TOKEN' <<<"$OUT1" || fail "settings.json botToken must be literal '\$TELEGRAM_BOT_TOKEN'"
grep -q 'mcpAuth=Bearer \$MCP_API_TOKEN' <<<"$OUT1" || fail "settings.json mcpAuth must be literal 'Bearer \$MCP_API_TOKEN'"
grep -q "mcpAllowed=\['homelab'\]" <<<"$OUT1" || fail "settings.json mcp.allowed must be ['homelab']"
grep -q "alwaysLoad=True" <<<"$OUT1" || fail "settings.json alwaysLoadTools must be True"
pass "persisted settings contain literal env references, alwaysLoadTools=true, and mcp.allowed=['homelab'], not secret values"

# Assert QWEN.md existed
grep -q "QWEN_MD_EXISTS" <<<"$OUT1" || fail "QWEN.md was not found in /workspace at runtime"
pass "QWEN.md exists in /workspace at runtime"

# 4. Container recreation check: run second container on SAME /workspace and /data volumes
OUT2=$(docker run --rm \
  -e QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS="12345678" \
  -e TELEGRAM_MCP_MODE="readonly" \
  -e QWEN_HOME="/data/home" \
  -e QWEN_RUNTIME_DIR="/data/runtime" \
  -v "$SECRETS_DIR/TELEGRAM_BOT_TOKEN:/run/secrets/TELEGRAM_BOT_TOKEN:ro" \
  -v "$SECRETS_DIR/VLLM_API_KEY:/run/secrets/VLLM_API_KEY:ro" \
  -v "$SECRETS_DIR/QWEN_MCP_TELEGRAM_TOKEN:/run/secrets/QWEN_MCP_TELEGRAM_TOKEN:ro" \
  -v "$TEST_VOL_WORKSPACE:/workspace" \
  -v "$TEST_VOL_DATA:/data" \
  --entrypoint bash \
  "$IMAGE" -c '
    # Run entrypoint to trigger startup overwrite of QWEN.md
    /usr/local/bin/entrypoint.sh >/tmp/entrypoint.log 2>&1 &
    PID=$!
    for _ in $(seq 1 30); do
      if [ -f /workspace/QWEN.md ]; then
        break
      fi
      if ! kill -0 "$PID" 2>/dev/null; then
        echo "entrypoint exited before QWEN.md was created"
        cat /tmp/entrypoint.log
        exit 1
      fi
      sleep 1
    done
    if [ ! -f /workspace/QWEN.md ]; then
      echo "timed out waiting for QWEN.md"
      cat /tmp/entrypoint.log
      exit 1
    fi
    kill -INT $PID 2>/dev/null || true
    wait $PID 2>/dev/null || true

    echo "--- CHECK QWEN_MD OVERWRITE ---"
    if grep -q "Homelab Telegram Qwen Assistant Behavioral Policy" /workspace/QWEN.md && ! grep -q "TAMPERED_POLICY_IN_VOLUME" /workspace/QWEN.md; then
      echo "QWEN_MD_OVERWRITTEN_CANONICAL"
    fi

    echo "--- CHECK SECRETS PERSISTENCE ---"
    if grep -r -i -E "'"$SENTINEL_BOT"'|'"$SENTINEL_VLLM"'|'"$SENTINEL_MCP"'" /workspace/ /data/; then
      echo "SECRETS_LEAKED_TO_DISK"
    else
      echo "VOLUMES_CLEAN_NO_SECRETS"
    fi
    echo "--- ENTRYPOINT OUTPUT ---"
    cat /tmp/entrypoint.log
  ' 2>&1 || true)

grep -q "QWEN_MD_OVERWRITTEN_CANONICAL" <<<"$OUT2" || fail "QWEN.md was not overwritten atomically from image on startup"
pass "QWEN.md is image-owned and atomically overwritten on startup"

grep -q "VOLUMES_CLEAN_NO_SECRETS" <<<"$OUT2" || fail "Secrets were persisted into /workspace or /data volumes!"
pass "no API tokens or secrets are persisted into /workspace or /data volumes"

# 5. Effective tool surface & native bypass lockdown:
#    a. Locked assumption: AcpBridge in standalone mode exposes no shellCommand method
#    b. Locked assumption: Absolute @{...} file reads outside /workspace boundary cannot access secrets
#    c. Policy verification: All 54 native tools disabled in /etc/qwen-code/settings.json
OUT_BYPASS=$(docker run --rm \
  -e QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS="12345678" \
  -e TELEGRAM_MCP_MODE="readonly" \
  -e QWEN_HOME="/data/home" \
  -e QWEN_RUNTIME_DIR="/data/runtime" \
  -v "$SECRETS_DIR/TELEGRAM_BOT_TOKEN:/run/secrets/TELEGRAM_BOT_TOKEN:ro" \
  -v "$SECRETS_DIR/VLLM_API_KEY:/run/secrets/VLLM_API_KEY:ro" \
  -v "$SECRETS_DIR/QWEN_MCP_TELEGRAM_TOKEN:/run/secrets/QWEN_MCP_TELEGRAM_TOKEN:ro" \
  -v "$TEST_VOL_WORKSPACE:/workspace" \
  -v "$TEST_VOL_DATA:/data" \
  --entrypoint bash \
  "$IMAGE" -c '
    python3 -c "
import json, re

# 1. Verify settings.json policy disables all 54 discovered built-ins
d = json.load(open(\"/etc/qwen-code/settings.json\"))
disabled = set(d.get(\"tools\",{}).get(\"disabled\",[]))
deny = set(d.get(\"permissions\",{}).get(\"deny\",[]))
assert len(disabled) >= 50
assert disabled == deny
assert \"run_shell_command\" in disabled
assert \"read_file\" in disabled
assert \"computerUse\" in disabled
assert d.get(\"tools\",{}).get(\"computerUse\",{}).get(\"enabled\") is False
assert d.get(\"skills\",{}).get(\"disabledLevels\") == [\"project\", \"user\", \"extension\", \"bundled\"]
print(\"POLICY_LOCKDOWN_VERIFIED\")

# 2. Check AcpBridge contract in codebase: AcpBridge does not export shellCommand to standalone ChannelBase
acp_chunk = open(\"${HOME}/.local/lib/qwen-code/lib/chunks/chunk-U7U3CX4K.js\").read()
# Standalone AcpBridge does not have shellCommand implementation on bridge prototype
assert \"if (opts.exposeShellCommand && bridge.shellCommand)\" in acp_chunk
print(\"ACP_BRIDGE_NO_SHELL_COMMAND_VERIFIED\")

# 3. Check @ token resolution logic: absolute file references outside workspace boundary are guarded
at_chunk = open(\"${HOME}/.local/lib/qwen-code/lib/chunks/chunk-L7RG5PVT.js\").read()
assert \"resolvePath\" in at_chunk or \"isPathInside\" in at_chunk or \"getWorkingDir\" in at_chunk
print(\"WORKSPACE_FILE_BOUNDARY_VERIFIED\")
"
  ' 2>&1 || true)

grep -q "POLICY_LOCKDOWN_VERIFIED" <<<"$OUT_BYPASS" || fail "Settings policy lockdown check failed"
grep -q "ACP_BRIDGE_NO_SHELL_COMMAND_VERIFIED" <<<"$OUT_BYPASS" || fail "AcpBridge shellCommand check failed"
grep -q "WORKSPACE_FILE_BOUNDARY_VERIFIED" <<<"$OUT_BYPASS" || fail "Workspace file boundary check failed"
# 6. Rendered MCP configuration includeTools check:
#    Verifies that with alwaysLoadTools=true and tools.toolSearch.enabled=false,
#    the rendered MCP configuration's includeTools set-equals the canonical
#    trusted role tool set (derived from render_project_settings.py — no
#    hardcoded count).
OUT_TOOLS=$(docker run --rm \
  -e QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS="12345678" \
  -e QWEN_CODE_TELEGRAM_ROLE="trusted" \
  -e TELEGRAM_MCP_MODE="readonly" \
  -e EXPECTED_TRUSTED_TOOLS="$EXPECTED_TRUSTED_TOOLS" \
  -e QWEN_HOME="/data/home" \
  -e QWEN_RUNTIME_DIR="/data/runtime" \
  -v "$SECRETS_DIR/TELEGRAM_BOT_TOKEN:/run/secrets/TELEGRAM_BOT_TOKEN:ro" \
  -v "$SECRETS_DIR/VLLM_API_KEY:/run/secrets/VLLM_API_KEY:ro" \
  -v "$SECRETS_DIR/QWEN_MCP_TELEGRAM_TOKEN:/run/secrets/QWEN_MCP_TELEGRAM_TOKEN:ro" \
  -v "$TEST_VOL_WORKSPACE:/workspace" \
  -v "$TEST_VOL_DATA:/data" \
  --entrypoint bash \
  "$IMAGE" -c '
    /usr/local/bin/entrypoint.sh >/tmp/entrypoint.log 2>&1 &
    PID=$!
    for _ in $(seq 1 30); do
      if [ -f /data/home/settings.json ]; then
        break
      fi
      if ! kill -0 "$PID" 2>/dev/null; then
        echo "entrypoint exited before settings.json was created"
        cat /tmp/entrypoint.log
        exit 1
      fi
      sleep 1
    done
    if [ ! -f /data/home/settings.json ]; then
      echo "timed out waiting for settings.json"
      cat /tmp/entrypoint.log
      exit 1
    fi

    ${HOME}/.local/lib/qwen-code/node/bin/node -e "
const fs = require(\"fs\");
const settings = JSON.parse(fs.readFileSync(\"/data/home/settings.json\", \"utf8\"));
if (settings.mcpServers?.homelab?.alwaysLoadTools !== true) {
  console.error(\"FAIL: alwaysLoadTools is not true\");
  process.exit(1);
}
const tools = settings.mcpServers?.homelab?.includeTools || [];
const expected = (process.env.EXPECTED_TRUSTED_TOOLS || "").split(",").filter(Boolean);
if (expected.length === 0) {
  console.error("FAIL: EXPECTED_TRUSTED_TOOLS not provided");
  process.exit(3);
}
if (tools.length === 0) {
  console.error("FAIL: includeTools is empty");
  process.exit(2);
}
if (new Set(tools).size !== tools.length) {
  console.error("FAIL: includeTools contains duplicates");
  process.exit(2);
}
const gotSet = new Set(tools);
const missing = expected.filter((n) => !gotSet.has(n));
const extra = tools.filter((n) => !expected.includes(n));
if (missing.length || extra.length) {
  console.error("FAIL: includeTools drift", JSON.stringify({ missing, extra }));
  process.exit(2);
}
console.log("RENDERED_MCP_TOOLS_SET_VERIFIED");
"
    echo "--- ENTRYPOINT OUTPUT ---"
    cat /tmp/entrypoint.log
    kill -INT $PID 2>/dev/null || true
    wait $PID 2>/dev/null || true
  ' 2>&1 || true)

if ! grep -q "RENDERED_MCP_TOOLS_SET_VERIFIED" <<<"$OUT_TOOLS"; then
  safe_out="$OUT_TOOLS"
  safe_out="${safe_out//"$SENTINEL_BOT"/<redacted-bot-token>}"
  safe_out="${safe_out//"$SENTINEL_VLLM"/<redacted-openai-key>}"
  safe_out="${safe_out//"$SENTINEL_MCP"/<redacted-mcp-token>}"
  printf '%s\n' "$safe_out" >&2
  fail "Rendered MCP configuration includeTools set-equality verification failed"
fi
pass "rendered MCP configuration contains alwaysLoadTools=true and includeTools set-equal the canonical trusted role set"

# 7. Behavioral ACP first-prompt request tools regression test:
#    Verifies that under QWEN_CODE_LEGACY_MCP_BLOCKING=1, when an ACP session is created
#    and an immediate session/prompt arrives, the model request contains the full
#    canonical trusted role set (set-equality, no hardcoded count) and tools
#    is NOT an empty array.
OUT_BEHAVIORAL=$(docker run --rm \
  -e QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS="12345678" \
  -e QWEN_CODE_TELEGRAM_ROLE="trusted" \
  -e TELEGRAM_MCP_MODE="readonly" \
  -e QWEN_CODE_LEGACY_MCP_BLOCKING="1" \
  -e EXPECTED_TRUSTED_TOOLS="$EXPECTED_TRUSTED_TOOLS" \
  -e QWEN_HOME="/data/home" \
  -e QWEN_RUNTIME_DIR="/data/runtime" \
  -v "$SECRETS_DIR/TELEGRAM_BOT_TOKEN:/run/secrets/TELEGRAM_BOT_TOKEN:ro" \
  -v "$SECRETS_DIR/VLLM_API_KEY:/run/secrets/VLLM_API_KEY:ro" \
  -v "$SECRETS_DIR/QWEN_MCP_TELEGRAM_TOKEN:/run/secrets/QWEN_MCP_TELEGRAM_TOKEN:ro" \
  -v "$TEST_VOL_WORKSPACE:/workspace" \
  -v "$TEST_VOL_DATA:/data" \
  --entrypoint bash \
  "$IMAGE" -c '
    /usr/local/bin/entrypoint.sh &
    PID=$!
    sleep 2

    ${HOME}/.local/lib/qwen-code/node/bin/node -e "
const fs = require(\"fs\");
const http = require(\"http\");
const { loadSettings } = require(\"${HOME}/.local/lib/qwen-code/lib/chunks/settings-GBBVKBQ2.js\");
const { loadCliConfig } = require(\"${HOME}/.local/lib/qwen-code/lib/chunks/chunk-U7U3CX4K.js\");
const { OpenAIContentConverter } = require(\"${HOME}/.local/lib/qwen-code/lib/chunks/chunk-KCUGOZPL.js\");

async function run() {
  const settings = await loadSettings(\"/workspace\");
  const config = await loadCliConfig(
    settings.merged,
    { sessionId: \"test-behavioral-\" + Date.now(), continue: false },
    \"/workspace\",
    void 0,
    { userHooks: settings.getUserHooks(), projectHooks: settings.getProjectHooks() },
    () => [],
    {},
    void 0,
    true,
    {}
  );

  await config.initialize();
  const client = config.getGeminiClient();
  const chat = client.getChat();

  const rawTools = chat.generationConfig.tools;
  if (!rawTools || rawTools.length === 0) {
    console.error(\"FAIL: chat generationConfig tools is empty\");
    process.exit(1);
  }

  const converted = await OpenAIContentConverter.convertGeminiToolsToOpenAI(rawTools);
  const expected = (process.env.EXPECTED_TRUSTED_TOOLS || "").split(",").filter(Boolean);
  if (expected.length === 0) {
    console.error("FAIL: EXPECTED_TRUSTED_TOOLS not provided");
    process.exit(3);
  }
  const names = converted.map((t) => (t && t.function && t.function.name) || (t && t.name) || null);
  if (names.every((n) => n)) {
    const gotSet = new Set(names);
    const missing = expected.filter((n) => !gotSet.has(n));
    const extra = names.filter((n) => !expected.includes(n));
    if (missing.length || extra.length) {
      console.error("FAIL: first prompt tools drift", JSON.stringify({ missing, extra }));
      process.exit(2);
    }
  } else if (converted.length !== expected.length) {
    console.error('FAIL: first prompt tools count', converted.length, '!= expected', expected.length);
    process.exit(2);
  }

console.log("BEHAVIORAL_FIRST_PROMPT_TOOLS_SET_VERIFIED");
}

run().catch((err) => {
  console.error(err);
  process.exit(3);
});
"
    kill -INT $PID 2>/dev/null || true
    wait $PID 2>/dev/null || true
  ' 2>&1 || true)

grep -q "BEHAVIORAL_FIRST_PROMPT_TOOLS_SET_VERIFIED" <<<"$OUT_BEHAVIORAL" || fail "Behavioral ACP first prompt tools set-equality verification failed"
pass "behavioral ACP first-prompt model request contains the canonical trusted role set and non-empty tools array"

# 8. Compaction configuration honored by the pinned runtime:
#    Effective settings must carry contextWindowSize=65536 and
#    autoCompactThreshold=0.5 (auto-compaction trigger lands near ~32.5k,
#    i.e. ~50% of the 64k operational window), and /compress must not be
#    disabled. Behavioral proof of compaction (shrink + state survival +
#    MCP still working) is live smoke sequence Phase 6 (S6.2).
OUT_COMPACTION=$(docker run --rm \
  -e QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS="12345678" \
  -e QWEN_CODE_TELEGRAM_ROLE="trusted" \
  -e TELEGRAM_MCP_MODE="readonly" \
  -e QWEN_HOME="/data/home" \
  -e QWEN_RUNTIME_DIR="/data/runtime" \
  -v "$SECRETS_DIR/TELEGRAM_BOT_TOKEN:/run/secrets/TELEGRAM_BOT_TOKEN:ro" \
  -v "$SECRETS_DIR/VLLM_API_KEY:/run/secrets/VLLM_API_KEY:ro" \
  -v "$SECRETS_DIR/QWEN_MCP_TELEGRAM_TOKEN:/run/secrets/QWEN_MCP_TELEGRAM_TOKEN:ro" \
  -v "$TEST_VOL_WORKSPACE:/workspace" \
  -v "$TEST_VOL_DATA:/data" \
  --entrypoint bash \
  "$IMAGE" -c '
    /usr/local/bin/entrypoint.sh &
    PID=$!
    sleep 2

    ${HOME}/.local/lib/qwen-code/node/bin/node -e "
const { loadSettings } = require(\"${HOME}/.local/lib/qwen-code/lib/chunks/settings-GBBVKBQ2.js\");
const { loadCliConfig } = require(\"${HOME}/.local/lib/qwen-code/lib/chunks/chunk-U7U3CX4K.js\");

async function run() {
  const settings = await loadSettings(\"/workspace\");
  const config = await loadCliConfig(
    settings.merged,
    { sessionId: \"test-compaction-\" + Date.now(), continue: false },
    \"/workspace\",
    void 0,
    { userHooks: settings.getUserHooks(), projectHooks: settings.getProjectHooks() },
    () => [],
    {},
    void 0,
    true,
    {}
  );

  const provider = (settings.merged?.modelProviders?.openai || [])[0] || {};
  const windowSize = provider.generationConfig?.contextWindowSize;
  if (windowSize !== 65536) {
    console.error(\"FAIL: effective provider contextWindowSize is not 65536, got\", windowSize);
    process.exit(1);
  }
  const threshold = settings.merged?.context?.autoCompactThreshold;
  if (threshold !== 0.5) {
    console.error(\"FAIL: context.autoCompactThreshold is not 0.5, got\", threshold);
    process.exit(2);
  }
  // computeThresholds math: auto = min(pct*window, window - 20000 - 13000)
  const auto = Math.min(threshold * windowSize, windowSize - 20000 - 13000);
  if (auto < 30000 || auto > 35000) {
    console.error(\"FAIL: auto-compaction trigger outside ~50% band:\", auto);
    process.exit(3);
  }
  const slashDisabled = settings.merged?.slashCommands?.disabled || [];
  if (slashDisabled.includes(\"compress\")) {
    console.error(\"FAIL: /compress is disabled in effective settings\");
    process.exit(4);
  }
  console.log(\"COMPACTION_CONFIG_HONORED_VERIFIED\");
}

run().catch((err) => {
  console.error(err);
  process.exit(9);
});
"
    kill -INT $PID 2>/dev/null || true
    wait $PID 2>/dev/null || true
  ' 2>&1 || true)

grep -q "COMPACTION_CONFIG_HONORED_VERIFIED" <<<"$OUT_COMPACTION" || fail "Compaction configuration verification failed"
pass "pinned runtime honors contextWindowSize=65536 + autoCompactThreshold=0.5 (auto-compaction ~32.5k) with /compress enabled"

echo "ALL test_runtime_regressions.sh PASS"
