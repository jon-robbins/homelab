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

BASE_COMPOSE_FILES=(
  "docker-compose.network.yml"
  "docker-compose.media.yml"
  "docker-compose.llm.yml"
)

failures=0

for compose_file in "${BASE_COMPOSE_FILES[@]}"; do
  if [[ ! -f "$compose_file" ]]; then
    echo "FAIL: missing compose file: ${compose_file}"
    ((failures += 1))
    continue
  fi

  if docker compose -f "$compose_file" config --quiet >/dev/null 2>&1; then
    echo "PASS: ${compose_file}"
  else
    echo "FAIL: ${compose_file}"
    ((failures += 1))
  fi
done

gpu_overlay=""
if [[ -f "docker-compose.gpu.yml" ]]; then
  gpu_overlay="docker-compose.gpu.yml"
elif [[ -f "config/gpu/docker-compose.gpu.yml" ]]; then
  gpu_overlay="config/gpu/docker-compose.gpu.yml"
fi

if [[ -n "$gpu_overlay" ]]; then
  # Overlay-only files are partial definitions; validate against combined stack.
  overlay_bases=()
  for base_file in "docker-compose.media.yml" "docker-compose.llm.yml"; do
    [[ -f "$base_file" ]] && overlay_bases+=("-f" "$base_file")
  done

  if (( ${#overlay_bases[@]} == 0 )); then
    echo "FAIL: GPU overlay found (${gpu_overlay}) but no compatible base compose files were found"
    ((failures += 1))
  elif docker compose "${overlay_bases[@]}" -f "$gpu_overlay" config --quiet >/dev/null 2>&1; then
    echo "PASS: combined base stack + ${gpu_overlay}"
  else
    echo "FAIL: combined base stack + ${gpu_overlay}"
    ((failures += 1))
  fi
else
  echo "PASS: no GPU overlay file present"
fi

if ((failures > 0)); then
  exit 1
fi
