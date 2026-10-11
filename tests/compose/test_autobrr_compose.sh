#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$REPO_ROOT"

COMPOSE_FILE="compose/autobrr.yml"

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "FAIL: ${COMPOSE_FILE} not found"
  exit 1
fi

failures=0

if ! grep -q '^  autobrr:' "$COMPOSE_FILE"; then
  echo "FAIL: autobrr service not defined in ${COMPOSE_FILE}"
  failures=$((failures + 1))
fi

if grep -E '^[[:space:]]*ports:' "$COMPOSE_FILE" >/dev/null; then
  echo "FAIL: autobrr must not publish host ports"
  failures=$((failures + 1))
fi

if ! grep -q 'homelab_net' "$COMPOSE_FILE"; then
  echo "FAIL: autobrr must join homelab_net"
  failures=$((failures + 1))
fi

if ! grep -q 'healthcheck:' "$COMPOSE_FILE"; then
  echo "FAIL: autobrr must define a healthcheck"
  failures=$((failures + 1))
fi

if ! grep -q 'api/healthz/liveness' "$COMPOSE_FILE"; then
  echo "FAIL: autobrr healthcheck must probe /api/healthz/liveness"
  failures=$((failures + 1))
fi

if ! grep -q '\${APP_DATA_DIR:-../data}/autobrr:/config' "$COMPOSE_FILE"; then
  echo "FAIL: autobrr must mount \${APP_DATA_DIR:-../data}/autobrr:/config"
  failures=$((failures + 1))
fi

if ((failures > 0)); then
  exit 1
fi

echo "PASS: autobrr compose contract satisfied"
