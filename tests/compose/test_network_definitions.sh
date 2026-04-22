#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$REPO_ROOT"

TARGET_FILE="docker-compose.network.yml"

if [[ ! -f "$TARGET_FILE" ]]; then
  echo "FAIL: ${TARGET_FILE} not found"
  exit 1
fi

awk '
BEGIN {
  in_networks = 0
  in_homelab = 0
  found_homelab = 0
  external_true = 0
}

/^[^[:space:]].*:$/ {
  top_key = $0
  sub(/:$/, "", top_key)
  if (top_key == "networks") {
    in_networks = 1
  } else {
    in_networks = 0
    in_homelab = 0
  }
}

in_networks == 1 && /^[[:space:]]{2}[A-Za-z0-9_.-]+:[[:space:]]*$/ {
  network_name = $0
  gsub(/^[[:space:]]{2}/, "", network_name)
  sub(/:[[:space:]]*$/, "", network_name)
  in_homelab = (network_name == "homelab_net")
  if (in_homelab) {
    found_homelab = 1
  }
}

in_networks == 1 && in_homelab == 1 && /^[[:space:]]{4}external:[[:space:]]*true([[:space:]]*|$)/ {
  external_true = 1
}

END {
  if (found_homelab == 0) {
    print "FAIL: homelab_net is not defined under networks"
    exit 1
  }
  if (external_true == 1) {
    print "FAIL: homelab_net must be managed (external: true is not allowed)"
    exit 1
  }
  print "PASS: homelab_net is defined and managed (not external)"
}
' "$TARGET_FILE"
