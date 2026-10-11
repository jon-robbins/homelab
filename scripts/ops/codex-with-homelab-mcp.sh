#!/usr/bin/env bash
# Launch Codex with the homelab MCP bearer token from the runtime .env.
#
# Keep the token out of ~/.codex/config.toml. Codex reads MCP_API_TOKEN from its
# own startup environment for the configured streamable HTTP MCP server.
set -euo pipefail

ROOT="${HOMELAB_ROOT:-${HOME}/homelab}"
REAL_CODEX="${CODEX_BIN:-${HOME}/.codex/packages/standalone/current/bin/codex}"

if [[ ! -x "${REAL_CODEX}" ]]; then
  echo "FAIL: Codex binary not executable: ${REAL_CODEX}" >&2
  exit 1
fi

if [[ -z "${MCP_API_TOKEN:-}" && -f "${ROOT}/.env" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" == MCP_API_TOKEN=* ]] || continue
    value="${line#MCP_API_TOKEN=}"
    value="${value%$'\r'}"
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    export MCP_API_TOKEN="$value"
    break
  done <"${ROOT}/.env"
fi

exec "${REAL_CODEX}" "$@"
