#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$REPO_ROOT"

COMPOSE_FILES=(
  "docker-compose.yml"
  "compose/docker-compose.network.yml"
  "compose/docker-compose.media.yml"
  "compose/docker-compose.llm.yml"
)

[[ -f "docker-compose.gpu.yml" ]] && COMPOSE_FILES+=("docker-compose.gpu.yml")
[[ -f "config/gpu/docker-compose.gpu.yml" ]] && COMPOSE_FILES+=("config/gpu/docker-compose.gpu.yml")

ALLOWED_HOST_SERVICES=(
  "pihole"
  "tailscale"
  "cloudflared"
  "plex"
  "jellyfin"
)

declare -A allowed=()
for svc in "${ALLOWED_HOST_SERVICES[@]}"; do
  allowed["$svc"]=1
done

mapfile -t host_mode_services < <(
  awk '
  function emit_current() {
    if (current_service == "") {
      return
    }
    if (network_mode_host == 1) {
      print current_service
    }
  }

  BEGIN {
    in_services = 0
    current_service = ""
  }

  FNR == 1 {
    in_services = 0
    current_service = ""
  }

  /^[^[:space:]].*:$/ {
    top_key = $0
    sub(/:$/, "", top_key)
    if (top_key == "services") {
      in_services = 1
      next
    }
    if (in_services == 1) {
      emit_current()
      in_services = 0
      current_service = ""
    }
  }

  in_services == 1 && /^[[:space:]]{2}[A-Za-z0-9_.-]+:[[:space:]]*$/ {
    emit_current()
    current_service = $0
    gsub(/^[[:space:]]{2}/, "", current_service)
    sub(/:[[:space:]]*$/, "", current_service)
    network_mode_host = 0
    next
  }

  in_services == 1 && current_service != "" {
    if ($0 ~ /^[[:space:]]{4}network_mode:[[:space:]]*host([[:space:]]*|$)/) {
      network_mode_host = 1
    }
  }

  END {
    emit_current()
  }
  ' "${COMPOSE_FILES[@]}" | sort -u
)

failures=0
for svc in "${host_mode_services[@]}"; do
  [[ -z "$svc" ]] && continue
  if [[ -n "${allowed[$svc]:-}" ]]; then
    echo "PASS: host-mode service allowed: ${svc}"
  else
    echo "FAIL: unauthorized host-mode service: ${svc}"
    ((failures += 1))
  fi
done

if (( ${#host_mode_services[@]} == 0 )); then
  echo "PASS: no host-mode services found"
fi

if ((failures > 0)); then
  exit 1
fi
