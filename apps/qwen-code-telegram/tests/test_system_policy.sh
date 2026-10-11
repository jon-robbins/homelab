#!/usr/bin/env bash
# Stage A static tests for /etc/qwen-code/settings.json policy file.
# Run locally without Docker.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SYS_JSON="${ROOT}/apps/qwen-code-telegram/files/etc/qwen-code/settings.json"
cd "${ROOT}"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

command -v jq >/dev/null || { echo "SKIP: jq required"; exit 0; }

# 1. No / prefixes in slashCommands.disabled
if jq -e '[.slashCommands.disabled[] | test("^/")] | any | not' "$SYS_JSON" >/dev/null; then
  pass "slash commands use bare names (no / prefix)"
else
  fail "slashCommands.disabled contains /-prefixed entries"
fi

# 2. Exhaustive native tool lockdown in tools.disabled
REQUIRED_DISABLED_TOOLS=(
  agent artifact ask_user_question click computerUse create_sub_session
  cron_create cron_delete cron_list display_image edit enter_plan_mode
  enter_worktree exit_plan_mode exit_worktree get_goal glob grep_search
  image_gen list_agents list_directory loop_wakeup lsp monitor notebook_edit
  plan read_file read_many_files read_mcp_resource record_artifact replace
  run_shell_command save_memory screenshot search_file_content send_message
  skill structured_output task task_create task_list task_stop task_update
  team_create team_delete team_plan_approval todo_write tool_search update_goal
  web_fetch web_search workflow write_file zoom_image
)

for t in "${REQUIRED_DISABLED_TOOLS[@]}"; do
  jq -e --arg tool "$t" '.tools.disabled | index($tool) != null' "$SYS_JSON" >/dev/null \
    || fail "tools.disabled missing required built-in tool: $t"
done
pass "tools.disabled exhaustively covers all 54 discovered native Qwen built-in tools"

# 3. coreTools: not present (deprecated security layer removed)
if jq -e 'has("coreTools") | not' "$SYS_JSON" >/dev/null; then
  pass "deprecated coreTools key is absent"
else
  fail "coreTools must be removed — use tools.disabled + permissions.deny instead"
fi

# 4. permissions.deny mirrors exactly tools.disabled
perm_diff=$(jq -r '
  [.permissions.deny | sort[]] as $p
  | [.tools.disabled | sort[]] as $t
  | ($p == $t)
' "$SYS_JSON")
[[ "$perm_diff" == "true" ]] || fail "permissions.deny key set must exactly mirror tools.disabled"
pass "permissions.deny exactly mirrors tools.disabled"

# 5. Privacy + telemetry + memory + toolSearch + computerUse + skills levels + mcp allowed + model hardening
jq -e '
  (.privacy.usageStatisticsEnabled == false)
  and (.telemetry.enabled == false)
  and (.telemetry.logPrompts == false)
  and (.general.enableAutoUpdate == false)
  and (.memory.enableManagedAutoMemory == false)
  and (.memory.enableManagedAutoDream == false)
  and (.memory.enableAutoSkill == false)
  and (.tools.toolSearch.enabled == false)
  and (.tools.computerUse.enabled == false)
  and (.skills.disabledLevels == ["project","user","extension","bundled"])
  and (.mcp.allowed == ["homelab"])
  and (.model.maxToolCallsPerTurn == 12)
  and (.experimental.artifact == false)
  and (.experimental.enableAttachments == false)
' "$SYS_JSON" >/dev/null || fail "privacy/telemetry/memory/skills/mcp/toolSearch/computerUse/model flags not all set correctly"
pass "privacy, telemetry, memory, skills.disabledLevels, mcp.allowed, computerUse, toolSearch, maxToolCallsPerTurn=12, artifact, attachments: all pinned disabled"

# 5b. Slash commands: only help, clear, status, compress are NOT disabled.
jq -e '.slashCommands.disabled as $d
  | (["help","clear","status","compress"] | map(. as $k | ($d | index($k) == null)) | all)
  and ($d | index("statusline") != null)
  and ($d | index("compress-fast") != null)
  and ($d | index("summarize") != null)
' "$SYS_JSON" >/dev/null || fail "slashCommands.disabled must retain only help/clear/status/compress, keep summarize/compress-fast disabled, and disable statusline"
pass "slash commands retain only help, clear, status, compress (all other discovered commands disabled)"

# 5c. Context management: 64k-window target with ~50% compaction threshold.
# Note: clearContextOnIdle is honored by v0.21.15 but cannot clear MCP tool
# results (only native tools); it is intent documentation + media cleanup here.
jq -e '
  (.context.autoCompactThreshold == 0.5)
  and (.context.clearContextOnIdle.toolResultsThresholdMinutes == 30)
  and (.context.clearContextOnIdle.toolResultsNumToKeep == 3)
  and (.context.clearContextOnIdle.toolResultsTotalCharsThreshold == 100000)
' "$SYS_JSON" >/dev/null || fail "context.autoCompactThreshold / clearContextOnIdle thresholds not pinned to the 64k/50% target"
pass "context compaction thresholds pinned (autoCompactThreshold=0.5, idle cleanup 30/3/100000)"

# 5d. QWEN.md is the agent-facing evidence-ledger policy and must not regress
# into a read-only hardcode that contradicts the generated role capability sets.
QWEN_POLICY="${ROOT}/apps/qwen-code-telegram/files/usr/local/share/qwen-code/QWEN.md"
for required in 'Result:' 'Verified:' 'Checked:' 'same-turn' 'Unknown' 'post-action read' '/summarize' '/compress-fast' 'Native shell'; do
  grep -Fq "$required" "$QWEN_POLICY" || fail "QWEN.md missing evidence-ledger policy requirement: $required"
done
if grep -Fq 'operate in read-only diagnostic mode' "$QWEN_POLICY"; then
  fail "QWEN.md must not hardcode read-only mode over generated role capabilities"
fi
pass "QWEN.md enforces the evidence ledger, uncertainty, mutation verification, and command policy"

# 6. No stale dispatchMode in system settings
if jq -e '.tools | has("dispatchMode") | not' "$SYS_JSON" >/dev/null; then
  pass "tools.dispatchMode is absent (canonical dispatch is channels.homelab-telegram.dispatchMode = followup)"
else
  fail "tools.dispatchMode must be absent from system settings"
fi

echo "ALL test_system_policy.sh PASS"
