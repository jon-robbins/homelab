#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$REPO_ROOT"

COMPOSE_FILES=(
  "docker-compose.yml"
  "compose/network.yml"
  "compose/arr.yml"
  "compose/media.yml"
  "compose/download.yml"
  "compose/workers.yml"
  "compose/autobrr.yml"
  "compose/llm.yml"
)

HARDCODED_PATTERNS=(
  "/mnt/media-hdd"
  "/mnt/media-nvme"
  "/srv/plex"
)

failures=0
for pattern in "${HARDCODED_PATTERNS[@]}"; do
  # Ignore interpolated defaults, e.g. ${MEDIA_HDD_PATH:-/mnt/media-hdd}.
  matches="$(
    grep -RnsF -- "$pattern" "${COMPOSE_FILES[@]}" 2>/dev/null \
      | grep -v '\${' \
      || true
  )"
  if [[ -n "$matches" ]]; then
    echo "FAIL: hardcoded path found (${pattern}):"
    echo "$matches"
    ((failures += 1))
  fi
done

if ((failures > 0)); then
  exit 1
fi

echo "PASS: No non-interpolated hardcoded mount paths found"
