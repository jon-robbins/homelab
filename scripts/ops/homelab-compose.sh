#!/usr/bin/env bash
# DEPRECATED: Compatibility wrapper for homelab-compose.py (Infisical REST client).
# Preferred secret delivery is file-backed via Infisical Agent.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec python3 "${ROOT}/scripts/ops/homelab-compose.py" "$@"
