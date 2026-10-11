#!/usr/bin/env bash
# Thin wrapper — delegates to infisical_seed_from_env.py (stdlib REST client).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec python3 "${REPO_ROOT}/scripts/ops/infisical_seed_from_env.py" "$@"
