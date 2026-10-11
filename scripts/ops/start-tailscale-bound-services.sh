#!/usr/bin/env bash
# Back-compat wrapper: Tailscale IP wait + recreate caddy/homelab-mcp.
# Prefer scripts/ops/homelab-boot-orchestrator.sh (full P0–P6 boot).
# This script runs orchestrator phases P3–P4 only.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec "${ROOT}/scripts/ops/homelab-boot-orchestrator.sh" --phases P3,P4 "$@"
