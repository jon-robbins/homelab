#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${MCP_GAP_CLOSER_ENV:-$ROOT/data/secrets/mcp-gap-closer.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi
cd "$ROOT"
if [[ -x "$ROOT/.venv/bin/homelab-mcp-gap-cloud-runner" ]]; then
  exec "$ROOT/.venv/bin/homelab-mcp-gap-cloud-runner" "$@"
fi
exec python3 -m homelab_workers.cli.mcp_gap_cloud_runner "$@"
