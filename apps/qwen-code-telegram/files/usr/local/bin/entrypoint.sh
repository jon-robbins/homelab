#!/usr/bin/env bash
# qwen-code-telegram entrypoint. Validates security invariants, renders
# project-level settings JSON at runtime, then exec()s qwen channel start.
#
# Three sources build effective settings (ascending priority / non-overwritable
# lower-priority layers first):
#   1. /etc/qwen-code/settings.json  (system: baked into image, immutable security policy)
#   2. Runtime render → /run/qwen-code/project-settings.json
#        (instance-level: model provider, channel, MCP servers with allowlisted tools)
#   3. Project/workspace settings — USER IS BLOCKED from overriding security by
#      slashCommands.disabled + tools.disabled, which system-layer policy wins.

set -euo pipefail

log()  { echo "[entrypoint] $*" >&2; }
fail() { echo "[entrypoint] FAIL: $*" >&2; exit 2; }

# ---------------------------------------------------------------------------
# Phase 0: Mount checks — ensure no accidental writable host /home leaks in.
# ---------------------------------------------------------------------------
# (No host mounts beyond the single named volume workdir/ and secrets; enforced
# by compose. The image user's own home is expected, but extra /home entries
# and /homelab indicate an accidental host mount.)
check_forbidden_mounts() {
  local home_root="/home"
  local own_home="${HOME}"
  local path

  if [ -d /homelab ]; then
    fail "refusing to start: forbidden path /homelab is present in container"
  fi
  if [ -d "$home_root" ]; then
    while IFS= read -r path; do
      if [ "$path" != "$own_home" ]; then
        fail "refusing to start: forbidden path $path is present in container"
      fi
    done < <(find "$home_root" -mindepth 1 -maxdepth 1 -type d -print)
  fi
}
check_forbidden_mounts

# ---------------------------------------------------------------------------
# Phase 1: Hydrate Docker secrets into env vars (SECRET FILE → env export).
# Never echo or log values — fail messages only reference env names / paths.
# ---------------------------------------------------------------------------
hydrate_secret_to_env() {
  local file="$1"
  local env_name="$2"
  [[ -f "$file" ]] || fail "required secret file $file is missing for $env_name"
  local val
  val="$(< "$file")"
  val="${val%$'\r'}"
  while [[ "$val" == *$'\n' || "$val" == *$'\r' || "$val" == *" " || "$val" == *$'\t' ]]; do
    val="${val%$'\n'}"; val="${val%$'\r'}"; val="${val% }"; val="${val%$'\t'}"
  done
  while [[ "$val" == $'\n'* || "$val" == $'\r'* || "$val" == " "* || "$val" == $'\t'* ]]; do
    val="${val#$'\n'}"; val="${val#$'\r'}"; val="${val# }"; val="${val#$'\t'}"
  done
  [[ -n "$val" ]] || fail "required secret $env_name (file $file) is empty or whitespace-only"
  # shellcheck disable=SC2163
  export "$env_name"="$val"
}

hydrate_secret_to_env "/run/secrets/TELEGRAM_BOT_TOKEN" "TELEGRAM_BOT_TOKEN"
hydrate_secret_to_env "/run/secrets/VLLM_API_KEY" "OPENAI_API_KEY"
hydrate_secret_to_env "/run/secrets/QWEN_MCP_TELEGRAM_TOKEN" "MCP_API_TOKEN"

# Validate remaining required environment variables
: "${QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS:?QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS must be non-empty comma-separated whitelist}"
: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN must be set (via secret hydration)}"
: "${OPENAI_API_KEY:?OPENAI_API_KEY must be set (via secret hydration)}"
: "${MCP_API_TOKEN:?MCP_API_TOKEN must be set (via secret hydration)}"

# TELEGRAM_MCP_MODE: default readonly; Stage A runtime pinned readonly even if acquire requested
: "${TELEGRAM_MCP_MODE:=readonly}"
case "$TELEGRAM_MCP_MODE" in
  readonly|acquire) ;;
  *) fail "TELEGRAM_MCP_MODE must be 'readonly' or 'acquire'; got '$TELEGRAM_MCP_MODE'";;
esac
if [[ "$TELEGRAM_MCP_MODE" == "acquire" ]]; then
  log "WARNING: TELEGRAM_MCP_MODE=acquire — Stage A runtime pinned readonly"
  export TELEGRAM_MCP_MODE=readonly
fi

# ---------------------------------------------------------------------------
# Phase 2: Privacy/telemetry hardening env (cannot be overridden by user settings)
# ---------------------------------------------------------------------------
export QWEN_CODE_DISABLE_ARTIFACT=1
export QWEN_USAGE_STATISTICS_ENABLED=0
export QWEN_TELEMETRY_ENABLED=0
export QWEN_TELEMETRY_LOG_PROMPTS=0
export QWEN_CODE_AUTO_UPDATE_ENABLED=0
# Qwen Code v0.21.15 progressive MCP/ACP race:
# ACP can accept the first prompt before background MCP discovery updates
# the model tool set, causing an empty tools payload to OpenAI-compatible
# backends. Keep MCP discovery blocking for this dedicated Telegram agent.
export QWEN_CODE_LEGACY_MCP_BLOCKING=1
export QWEN_CODE_SYSTEM_SETTINGS_PATH=/etc/qwen-code/settings.json
export QWEN_HOME="${QWEN_HOME:-/data/home}"
export QWEN_RUNTIME_DIR="${QWEN_RUNTIME_DIR:-/data/runtime}"

# ---------------------------------------------------------------------------
# Phase 3: Render project-level settings JSON at runtime from env-only inputs
# ---------------------------------------------------------------------------
mkdir -p "${QWEN_HOME}" "${QWEN_RUNTIME_DIR}" /run/qwen-code /workspace
python3 /usr/local/lib/qwen-code/render_project_settings.py "${QWEN_HOME}/settings.json"
cp "${QWEN_HOME}/settings.json" /run/qwen-code/project-settings.json

# Always maintain the default user's compatibility symlink to QWEN_HOME
if [[ "${QWEN_HOME}" != "${HOME}/.qwen" ]]; then
  rm -rf "${HOME}/.qwen"
  ln -s "${QWEN_HOME}" "${HOME}/.qwen"
fi

# Clean up any stale channel PID file from a previous container incarnation
rm -f "${QWEN_HOME}/channels/service.pid" "${HOME}/.qwen/channels/service.pid" 2>/dev/null || true

# QWEN.md is image-owned configuration: atomically overwrite /workspace/QWEN.md on startup
if [[ -f /usr/local/share/qwen-code/QWEN.md ]]; then
  cp /usr/local/share/qwen-code/QWEN.md /workspace/QWEN.md.tmp
  mv -f /workspace/QWEN.md.tmp /workspace/QWEN.md
fi

chmod 0700 "${QWEN_HOME}" "${QWEN_RUNTIME_DIR}" 2>/dev/null || true
chmod 0600 "${QWEN_HOME}/settings.json" /run/qwen-code/project-settings.json 2>/dev/null || true
chmod 0755 /workspace 2>/dev/null || true
if [[ -f /workspace/QWEN.md ]]; then
  chmod 0644 /workspace/QWEN.md 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# Phase 4: Hard fail if any hardening env ended up wrong (paranoia check)
# ---------------------------------------------------------------------------
[[ "${QWEN_CODE_DISABLE_ARTIFACT:-}" == "1" ]] || fail "QWEN_CODE_DISABLE_ARTIFACT must be 1"
[[ "${QWEN_USAGE_STATISTICS_ENABLED:-}" == "0" ]] || fail "QWEN_USAGE_STATISTICS_ENABLED must be 0"
[[ "${QWEN_TELEMETRY_ENABLED:-}" == "0" ]] || fail "QWEN_TELEMETRY_ENABLED must be 0"
[[ "${QWEN_TELEMETRY_LOG_PROMPTS:-}" == "0" ]] || fail "QWEN_TELEMETRY_LOG_PROMPTS must be 0"
[[ "${QWEN_CODE_AUTO_UPDATE_ENABLED:-}" == "0" ]] || fail "QWEN_CODE_AUTO_UPDATE_ENABLED must be 0"
[[ "${QWEN_CODE_LEGACY_MCP_BLOCKING:-}" == "1" ]] || fail "QWEN_CODE_LEGACY_MCP_BLOCKING must be 1"
[[ "${QWEN_CODE_SYSTEM_SETTINGS_PATH:-}" == "/etc/qwen-code/settings.json" ]] || fail "system settings path is pinned"

# ---------------------------------------------------------------------------
# Phase 5: Exec qwen channel start
# ---------------------------------------------------------------------------
export PATH="${HOME}/.local/bin:${PATH}"
log "executing qwen channel start homelab-telegram"
exec qwen channel start homelab-telegram
