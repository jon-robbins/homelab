#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$REPO_ROOT"

COMPOSE_FILES=(
  "docker-compose.network.yml"
  "docker-compose.media.yml"
  "docker-compose.llm.yml"
)

[[ -f "docker-compose.gpu.yml" ]] && COMPOSE_FILES+=("docker-compose.gpu.yml")
[[ -f "config/gpu/docker-compose.gpu.yml" ]] && COMPOSE_FILES+=("config/gpu/docker-compose.gpu.yml")

awk '
function report_service_status() {
  if (current_service == "" || has_caddy == 0) {
    return
  }

  if (network_mode_host == 1) {
    if (has_host_reverse_proxy == 0) {
      printf("FAIL: %s -> %s uses caddy labels with host networking but lacks host.docker.internal reverse_proxy\n", file_name, current_service)
      failures += 1
    } else {
      printf("PASS: %s -> %s host-network caddy label sanity ok\n", file_name, current_service)
    }
    return
  }

  if (has_networks == 0) {
    printf("FAIL: %s -> %s uses caddy labels but has no networks entry\n", file_name, current_service)
    failures += 1
  } else {
    printf("PASS: %s -> %s bridge-network caddy label sanity ok\n", file_name, current_service)
  }
}

BEGIN {
  in_services = 0
  current_service = ""
  failures = 0
}

FNR == 1 {
  in_services = 0
  current_service = ""
  file_name = FILENAME
}

/^[^[:space:]].*:$/ {
  top_key = $0
  sub(/:$/, "", top_key)
  if (top_key == "services") {
    in_services = 1
    next
  }
  if (in_services == 1) {
    report_service_status()
    in_services = 0
    current_service = ""
  }
}

in_services == 1 && /^[[:space:]]{2}[A-Za-z0-9_.-]+:[[:space:]]*$/ {
  report_service_status()
  current_service = $0
  gsub(/^[[:space:]]{2}/, "", current_service)
  sub(/:[[:space:]]*$/, "", current_service)

  has_caddy = 0
  has_networks = 0
  network_mode_host = 0
  has_host_reverse_proxy = 0
  next
}

in_services == 1 && current_service != "" {
  if ($0 ~ /^[[:space:]]{4}networks:[[:space:]]*$/) {
    has_networks = 1
  }
  if ($0 ~ /^[[:space:]]{4}network_mode:[[:space:]]*host([[:space:]]*|$)/) {
    network_mode_host = 1
  }
  if ($0 ~ /^[[:space:]]{6}caddy([.:]|$)/) {
    has_caddy = 1
  }
  if ($0 ~ /caddy\.reverse_proxy:/ && $0 ~ /host\.docker\.internal/) {
    has_host_reverse_proxy = 1
  }
}

END {
  report_service_status()
  if (failures > 0) {
    exit 1
  }
}
' "${COMPOSE_FILES[@]}"

echo "PASS: Caddy label structure sanity checks completed"
