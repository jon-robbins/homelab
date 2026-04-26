#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$REPO_ROOT"

COMPOSE_FILES=(
  "docker-compose.yml"
  "docker-compose.homelab-net.yml"
  "compose/docker-compose.network.yml"
  "compose/docker-compose.media.yml"
  "compose/docker-compose.llm.yml"
)

[[ -f "docker-compose.gpu.yml" ]] && COMPOSE_FILES+=("docker-compose.gpu.yml")
[[ -f "config/gpu/docker-compose.gpu.yml" ]] && COMPOSE_FILES+=("config/gpu/docker-compose.gpu.yml")

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
