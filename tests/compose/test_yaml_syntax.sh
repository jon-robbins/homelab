#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$REPO_ROOT"

ensure_test_network() {
  if docker network inspect homelab_net >/dev/null 2>&1; then
    return
  fi
  docker network create homelab_net >/dev/null
}

if ! command -v docker >/dev/null 2>&1; then
  echo "SKIP: docker is not installed; cannot run compose schema validation."
  exit 0
fi

ensure_test_network

failures=0

run_config() {
  local label="$1"
  shift
  if docker compose "$@" config --quiet >/dev/null 2>&1; then
    echo "PASS: ${label}"
  else
    echo "FAIL: ${label}"
    failures=$((failures + 1))
  fi
}

run_config "docker-compose.yml (root include bundle)" -f docker-compose.yml

if ((failures > 0)); then
  exit 1
fi
